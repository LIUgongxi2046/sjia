"""Actual loopback HTTP write-intent/record/read with test-only qualified fixtures.

Run only under Django's isolated test database. No browser, external model, broker
consumer or configuration approval is made in a standalone development database.
"""
import http.cookiejar
import json
import urllib.error
import urllib.request
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.test import LiveServerTestCase, override_settings

from identity.models import Patient, PrincipalCredential
from records.models import Record
from tests.synthetic_fixtures import current_record_payload, ensure_synthetic_audit_policy, qualify_demo


@override_settings(SJIA_ENVIRONMENT='test', SESSION_COOKIE_SECURE=False,
    CSRF_COOKIE_SECURE=False, SESSION_COOKIE_NAME='sjia_http_test',
    ALLOWED_HOSTS=['127.0.0.1', 'localhost', 'testserver'])
class SyntheticRecordLiveHttpTests(LiveServerTestCase):
    host = '127.0.0.1'

    def setUp(self):
        from tests.isolated_rows import IsolatedSchemaRows

        rows = IsolatedSchemaRows(require_empty=True)
        self.addCleanup(rows.cleanup)
        call_command('seed_demo', verbosity=0)
        self.principal = PrincipalCredential.objects.get(username='guardian-1').principal
        self.patient = Patient.objects.get(display_id='demo-child')
        qualify_demo(self, self.principal, self.patient)
        ensure_synthetic_audit_policy(self.principal)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        # The test's opener talks only to this loopback server. Provider transports
        # are blocked independently; no model call is necessary for manual records.
        for target in ('httpx.Client.send', 'urllib3.PoolManager.request', 'urllib.request.urlopen'):
            guard = patch(target, side_effect=AssertionError('External request forbidden in synthetic HTTP test'))
            guard.start()
            self.addCleanup(guard.stop)

    def request(self, path, payload=None, token=None, headers=None):
        self.assertTrue(path.startswith('/'))
        sent = {'Content-Type': 'application/json', **(headers or {})}
        if token:
            sent['X-CSRFToken'] = token
        request = urllib.request.Request(self.live_server_url + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            method='POST' if payload is not None else 'GET', headers=sent)
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read()), response.headers

    def test_authenticated_http_intent_write_read_replay_and_logout(self):
        status, bootstrap, _ = self.request('/api/auth/bootstrap')
        self.assertEqual(status, 200)
        status, anonymous, _ = self.request('/api/session')
        self.assertEqual((status, anonymous['code']), (401, 'AUTH_REQUIRED'))
        status, logged_in, _ = self.request('/api/auth/login',
            {'username': 'guardian-1', 'password': 'demo-pass-1'}, bootstrap['csrf_token'])
        self.assertEqual(status, 200, logged_in)
        self.assertEqual(logged_in['principal_id'], str(self.principal.id))
        token = logged_in['csrf_token']
        payload = current_record_payload(self.patient, {'kind': 'observation', 'capture_method': 'manual',
            'fields': [{'field_code': 'temp', 'value_type': 'decimal', 'value_state': 'known',
                'raw_numeric': '38.0', 'raw_unit': 'C', 'sample_time_origin': 'observation'}]})
        target = f'/api/patients/{self.patient.id}/records'
        request_id = str(uuid4())
        status, intent, _ = self.request('/api/write-intents', {'operation_id': 'record_create',
            'target_key': target, 'request_id': request_id, 'payload': payload, 'schema_version': 'back-json-1'}, token)
        self.assertEqual(status, 201, intent)
        intent_headers = {'X-Write-Intent': intent['intent_id'],
            'X-Intent-Generation': str(intent['generation']), 'Idempotency-Key': request_id}
        before = Record.objects.filter(patient=self.patient).count()
        status, created, headers = self.request(target, payload, token, intent_headers)
        self.assertEqual(status, 201, created)
        self.assertIn('no-store', headers.get('Cache-Control', ''))
        self.assertRegex(headers['X-Trace-ID'], r'^[0-9a-f]{32}$')
        record_id = created['record_id']
        status, replayed, _ = self.request(target, payload, token, intent_headers)
        self.assertEqual(status, 200, replayed)
        self.assertEqual(replayed['record_id'], record_id)
        self.assertEqual(Record.objects.filter(patient=self.patient).count(), before + 1)
        status, reread, headers = self.request(f'/api/records/{record_id}')
        self.assertEqual(status, 200, reread)
        self.assertEqual(reread['fields'][0]['raw_numeric'], '38.0')
        self.assertIn('no-store', headers.get('Cache-Control', ''))
        status, _, _ = self.request('/api/session/logout', {}, token)
        self.assertEqual(status, 200)
        status, denied, _ = self.request(f'/api/records/{record_id}')
        self.assertEqual((status, denied['code']), (401, 'AUTH_REQUIRED'))
