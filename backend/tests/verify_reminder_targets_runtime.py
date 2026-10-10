import os
import unittest
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from archives.inputs import PROTOCOL, REGISTRY
from archives.models import MaterialReminder
from authorization.models import AccessGrant
from drafts.models import Draft
from jsonschema import Draft202012Validator, FormatChecker
from notifications import reminders
from notifications.errors import NotificationRejected
from notifications.reminder_lifecycle import reconcile_reminder, reconcile_reminders
from verify_reminder_runtime import reminder_fingerprint
from verify_reminder_write_runtime import write_fingerprint


class ReminderTargetRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.before = reminder_fingerprint(), write_fingerprint()

    def tearDown(self):
        self.assertEqual((reminder_fingerprint(), write_fingerprint()), self.before)

    def scope(self):
        now = timezone.now()
        grant = (AccessGrant.objects.select_related('principal', 'patient').filter(action='manage_draft',
            purpose='family_observation', field_codes__contains=['observation_note'], revoked_at__isnull=True,
            valid_from__lte=now, principal__account_status='active', patient__status='active', record_scope='all')
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now)).order_by('id').first())
        self.assertIsNotNone(grant)
        return reminders.current_scope(owner=grant.principal, patient=grant.patient, auth_epoch=grant.principal.auth_epoch)

    def test_anonymous_target_search_requires_authentication(self):
        draft = Draft.objects.order_by('id').first()
        self.assertIsNotNone(draft)
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            response = client.post(f'/api/v2/patients/{draft.patient_id}/material-reminder-targets/search')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
        self.assertIn('no-store', response.headers['cache-control'])

    def test_actual_targets_are_exact_and_paginated(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            scope = self.scope()
            drafts, _ = reminders.target_queries(scope)
            expected = set(drafts.values_list('id', flat=True))
            self.assertTrue(expected)
            cursor_value, observed = None, set()
            validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/ReminderTargetList'},
                registry=REGISTRY, format_checker=FormatChecker())
            while True:
                result = reminders.reminder_target_list(scope=scope, target_type='draft', limit=2,
                    expires_at=timezone.now() + timedelta(minutes=1), cursor=cursor_value)
                self.assertEqual(list(validator.iter_errors(result)), [])
                self.assertEqual(result['capability_state'], 'available')
                self.assertIsNone(result['capability_reason'])
                for item in result['items']:
                    draft = Draft.objects.get(id=item['target_id'])
                    self.assertEqual(draft.owner_id, scope.owner.id)
                    self.assertEqual(draft.patient_id, scope.patient.id)
                    self.assertEqual(str(draft.version), item['target_version'])
                    self.assertEqual(item['saved_at'], draft.saved_at.isoformat())
                    self.assertNotIn(draft.id, observed)
                    observed.add(draft.id)
                cursor_value = result['next_cursor']
                if cursor_value is None:
                    break
            self.assertEqual(observed, expected)

    def test_cursor_cannot_change_page_size(self):
        with transaction.atomic():
            scope = self.scope()
            expiry = timezone.now() + timedelta(minutes=1)
            first = reminders.reminder_target_list(scope=scope, target_type='draft', limit=1, expires_at=expiry)
            self.assertIsNotNone(first['next_cursor'])
            with self.assertRaises(NotificationRejected) as rejected:
                reminders.reminder_target_list(scope=scope, target_type='draft', limit=2,
                    expires_at=expiry, cursor=first['next_cursor'])
            self.assertEqual(rejected.exception.reason_code, 'CURSOR_STALE')

    def test_record_task_directory_reads_actual_personal_entity(self):
        with transaction.atomic():
            result = reminders.reminder_target_list(scope=self.scope(), target_type='record_task', limit=1,
                expires_at=timezone.now() + timedelta(minutes=1))
            self.assertEqual(result['items'], [])
            self.assertIsNone(result['next_cursor'])
            self.assertEqual(result['capability_state'], 'available')
            self.assertIsNone(result['capability_reason'])
            self.assertFalse(reminders.record_task_query(self.scope()).exists())

    def test_field_grants_do_not_claim_whole_batch_capability(self):
        with transaction.atomic():
            scope = self.scope()
            self.assertTrue(all(grant.field_codes for grant in scope.grants))
            result = reminders.reminder_target_list(scope=scope, target_type='batch', limit=1,
                expires_at=timezone.now() + timedelta(minutes=1))
            self.assertEqual(result['items'], [])
            self.assertEqual(result['capability_state'], 'unavailable')
            self.assertEqual(result['capability_reason'], 'REMINDER_SCOPE_DENIED')

    def test_session_scope_requires_an_explicit_epoch(self):
        with transaction.atomic():
            scope = self.scope()
            with self.assertRaises(NotificationRejected) as rejected:
                reminders.current_scope(owner=scope.owner, patient=scope.patient, auth_epoch=None)
            self.assertEqual(rejected.exception.reason_code, 'REMINDER_SCOPE_DENIED')

    def test_empty_lifecycle_scan_keeps_actual_counts(self):
        self.assertFalse(MaterialReminder.objects.exists())
        result = reconcile_reminders(limit=10, dry_run=True)
        self.assertEqual(result, {'dry_run': True, 'scanned': 0, 'changed': 0, 'would_change': 0,
            'next_cursor': None, 'items': []})

    def test_existing_draft_cannot_be_used_as_reminder(self):
        draft = Draft.objects.order_by('id').first()
        self.assertIsNotNone(draft)
        with self.assertRaises(NotificationRejected) as rejected:
            reconcile_reminder(draft.id, dry_run=True)
        self.assertEqual(rejected.exception.reason_code, 'REMINDER_NOT_FOUND')


if __name__ == '__main__':
    unittest.main(verbosity=2)
