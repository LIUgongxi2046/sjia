"""Synthetic read-only session binding and persisted-deadline regressions."""
from datetime import datetime, timedelta, timezone as dt_timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.sessions.models import Session
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone
from jsonschema import Draft202012Validator, ValidationError

from identity.models import Principal
from identity.session import establish_session, session_read_metadata


class SessionContinuityPureTests(SimpleTestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 5, 12, tzinfo=dt_timezone.utc)
        self.deadline = self.now + timedelta(hours=1)
        self.request = SimpleNamespace(session=SimpleNamespace(session_key='synthetic-only-session-key'))
        self.identity = {'principal_id': uuid4(), 'auth_epoch': 0, 'audience': 'family'}

    def _metadata(self, *, at=None, expires_at=None, **changes):
        with patch('identity.session.timezone.now', return_value=at or self.now), \
                patch('identity.session.Session.objects.filter') as rows:
            rows.return_value.values_list.return_value.first.return_value = expires_at or self.deadline
            return session_read_metadata(self.request, **(self.identity | changes))

    def test_binding_and_persisted_deadline_do_not_slide_with_read_time(self):
        before = self._metadata()
        self.assertEqual(before, self._metadata(at=self.now + timedelta(minutes=10)))
        self.assertRegex(before['session_binding'], r'^[0-9a-f]{64}$')
        self.assertEqual(before['expires_at'], self.deadline.isoformat())
        self.assertNotIn(self.request.session.session_key, json.dumps(before))

    def test_binding_changes_for_key_principal_epoch_and_audience(self):
        before = self._metadata()['session_binding']
        for changes in ({'principal_id': uuid4()}, {'auth_epoch': 1}, {'audience': 'support'}):
            self.assertNotEqual(before, self._metadata(**changes)['session_binding'])
        self.request.session.session_key = 'synthetic-only-new-session-key'
        self.assertNotEqual(before, self._metadata()['session_binding'])

    def test_missing_or_expired_persisted_session_is_unknown_and_due(self):
        with patch('identity.session.timezone.now', return_value=self.now), \
                patch('identity.session.Session.objects.filter') as rows:
            rows.return_value.values_list.return_value.first.return_value = None
            self.assertEqual(session_read_metadata(self.request, **self.identity),
                {'session_binding': None, 'expires_at': self.now.isoformat()})
        expired = self._metadata(expires_at=self.now)
        self.assertEqual(expired, {'session_binding': None, 'expires_at': self.now.isoformat()})
        self.request.session.session_key = None
        with patch('identity.session.timezone.now', return_value=self.now):
            self.assertEqual(session_read_metadata(self.request, **self.identity), expired)

    def test_protocol_marker_is_optional_nullable_and_not_an_arbitrary_string(self):
        schema = json.loads((Path(__file__).resolve().parents[2] / 'backend-design/protocol.schema.json').read_text())
        validator = Draft202012Validator({'$ref': '#/$defs/SessionView', '$defs': schema['$defs']})
        item = {'principal_id': str(self.identity['principal_id']), 'account_status': 'active',
            'expires_at': self.deadline.isoformat(), 'audience': 'family', 'capabilities': [],
            'csrf_token': 'synthetic-only', 'selected_patient_id': None}
        validator.validate(item)
        for binding in (None, 'a' * 64):
            validator.validate(item | {'session_binding': binding})
        for binding in ('', 'A' * 64, 'a' * 63, 3, {}):
            with self.assertRaises(ValidationError):
                validator.validate(item | {'session_binding': binding})


class SessionContinuityTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
        self.request = RequestFactory().get('/api/session')
        SessionMiddleware(lambda request: None).process_request(self.request)
        establish_session(self.request, principal_id=self.actor.id, auth_epoch=0, audience='family')

    def _metadata(self):
        return session_read_metadata(self.request, principal_id=self.actor.id, auth_epoch=0, audience='family')

    def test_storage_deadline_is_used_without_session_save_or_extension(self):
        before = Session.objects.get(session_key=self.request.session.session_key)
        with self.assertNumQueries(1):
            metadata = self._metadata()
        self.assertEqual(metadata['expires_at'], before.expire_date.isoformat())
        later = timezone.now() + timedelta(minutes=5)
        with patch('identity.session.timezone.now', return_value=later):
            self.assertEqual(metadata, self._metadata())
        after = Session.objects.get(session_key=self.request.session.session_key)
        self.assertEqual((before.session_data, before.expire_date), (after.session_data, after.expire_date))

    def test_new_same_principal_login_rotates_binding(self):
        before = self._metadata()
        establish_session(self.request, principal_id=self.actor.id, auth_epoch=0, audience='family')
        self.assertNotEqual(before['session_binding'], self._metadata()['session_binding'])

    def test_http_reads_have_stable_binding_and_real_expiry_despite_csrf_mask(self):
        # Use the configured cookie name rather than relying on a deployment's prefix.
        from django.conf import settings
        self.client.cookies[settings.SESSION_COOKIE_NAME] = self.request.session.session_key
        before = Session.objects.get(session_key=self.request.session.session_key)
        first = self.client.get('/api/session')
        after_first = Session.objects.get(session_key=self.request.session.session_key)
        second = self.client.get('/api/session')
        after_second = Session.objects.get(session_key=self.request.session.session_key)
        third = self.client.get('/api/session')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 200)
        self.assertEqual(first.json()['session_binding'], second.json()['session_binding'])
        self.assertEqual(second.json()['session_binding'], third.json()['session_binding'])
        self.assertEqual(first.json()['expires_at'], before.expire_date.isoformat())
        # RequestFactory skipped the login response middleware. Its first real
        # response persists the CSRF secret (CSRF_USE_SESSIONS), legitimately
        # updating expiry after the DTO was read. Each response must use the
        # pre-response persisted deadline; subsequent ordinary reads stay stable.
        self.assertEqual(second.json()['expires_at'], after_first.expire_date.isoformat())
        self.assertEqual(third.json()['expires_at'], after_second.expire_date.isoformat())
        self.assertEqual(second.json()['expires_at'], third.json()['expires_at'])
        self.assertEqual((after_first.session_data, after_first.expire_date),
            (after_second.session_data, after_second.expire_date))
        self.assertNotIn(self.request.session.session_key, json.dumps(first.json()))

    def test_client_marker_does_not_replace_authenticated_cookie(self):
        binding = self._metadata()['session_binding']
        response = self.client.get('/api/session', {'session_binding': binding})
        self.assertEqual(response.status_code, 401)
