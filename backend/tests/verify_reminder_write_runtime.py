import hashlib
import json
import os
import unittest
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.core.serializers.json import DjangoJSONEncoder
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from api.base import ApiError
from drafts.models import Draft
from archives.models import MaterialReminder, MaterialReminderNotice
from audit.models import AuditEvent
from authorization.models import AccessGrant
from requests.models import WriteIntent, WriteRequest
from requests.material_reminders import OPERATIONS, checked_payload, prepared_scope, reminder_receipt, target_route
from notifications.errors import NotificationRejected
from notifications.reminder_schemas import reminder_input
from notifications.reminders import current_scope, target_queries


def write_fingerprint():
    rows = {'reminders': list(MaterialReminder.objects.order_by('id').values()),
        'notices': list(MaterialReminderNotice.objects.order_by('id').values()),
        'intents': list(WriteIntent.objects.order_by('id').values()),
        'requests': list(WriteRequest.objects.order_by('id').values()),
        'audit': list(AuditEvent.objects.order_by('id').values())}
    return {'counts': {name: len(items) for name, items in rows.items()},
        'sha256': hashlib.sha256(json.dumps(rows, cls=DjangoJSONEncoder, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()}


class ReminderWriteRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.before = write_fingerprint()

    def tearDown(self):
        self.assertEqual(write_fingerprint(), self.before)

    def test_anonymous_reminder_writes_require_authentication(self):
        before = write_fingerprint()
        draft = Draft.objects.order_by('id').first()
        self.assertIsNotNone(draft)
        paths = [f'/api/v2/patients/{draft.patient_id}/material-reminders',
            f'/api/v2/material-reminders/{draft.id}/settings']
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for path in paths:
                with self.subTest(path=path):
                    response = client.post(path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(write_fingerprint(), before)

    def test_anonymous_reminder_request_recovery_requires_authentication(self):
        intent = WriteIntent.objects.order_by('id').first()
        self.assertIsNotNone(intent)
        paths = [('POST', '/api/v2/write-intents'),
            ('GET', f'/api/v2/requests/{intent.id}'),
            ('POST', f'/api/v2/write-intents/{intent.id}/retry')]
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for method, path in paths:
                with self.subTest(path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])

    def test_real_unrelated_intents_cannot_supply_reminder_receipts(self):
        with transaction.atomic():
            intents = list(WriteIntent.objects.exclude(operation_id__in=OPERATIONS).order_by('id'))
            self.assertTrue(intents)
            for intent in intents:
                for check in (checked_payload, reminder_receipt):
                    with self.subTest(intent_id=str(intent.id), check=check.__name__):
                        with self.assertRaises(ApiError) as rejected:
                            check(intent)
                        self.assertEqual(rejected.exception.code, 'REMINDER_RECOVERY_INVALID')

    def test_historical_schedule_structure_remains_readable(self):
        draft = Draft.objects.order_by('id').first()
        self.assertIsNotNone(draft)
        payload = {'target_type': 'draft', 'target_id': str(draft.id), 'target_version': str(draft.version),
            'due_at': draft.saved_at.isoformat(), 'timezone': settings.TIME_ZONE,
            'quiet_start': None, 'quiet_end': None}
        restored = reminder_input(payload, now=timezone.now(), check_schedule=False)
        self.assertEqual(restored.due_at, draft.saved_at)
        with self.assertRaises(NotificationRejected) as rejected:
            reminder_input(payload, now=timezone.now())
        self.assertEqual(rejected.exception.reason_code, 'REMINDER_SCHEDULE_INVALID')

    def test_same_submission_enabled_choice_is_strict(self):
        draft = Draft.objects.order_by('id').first()
        self.assertIsNotNone(draft)
        payload = {'target_type': 'draft', 'target_id': str(draft.id), 'target_version': str(draft.version),
            'due_at': (timezone.now() + timedelta(minutes=1)).isoformat(), 'timezone': settings.TIME_ZONE,
            'quiet_start': None, 'quiet_end': None}
        self.assertFalse(reminder_input(payload, now=timezone.now()).enabled)
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.assertEqual(reminder_input({**payload, 'enabled': enabled}, now=timezone.now()).enabled, enabled)
        for invalid in (None, 'true', 1):
            with self.subTest(invalid=invalid):
                with self.assertRaises(NotificationRejected) as rejected:
                    reminder_input({**payload, 'enabled': invalid}, now=timezone.now())
                self.assertEqual(rejected.exception.reason_code, 'REMINDER_INPUT_INVALID')

    def test_reminder_targets_bind_exact_operation_and_route(self):
        draft = Draft.objects.order_by('id').first()
        self.assertIsNotNone(draft)
        create_target = f'/patients/{draft.patient_id}/material-reminders'
        change_target = f'/material-reminders/{draft.id}/settings'
        self.assertEqual(target_route(OPERATIONS[0], create_target).kwargs['patient_id'], draft.patient_id)
        self.assertEqual(target_route(OPERATIONS[1], change_target).kwargs['reminder_id'], draft.id)
        with self.assertRaises(ApiError) as rejected:
            target_route(OPERATIONS[0], change_target)
        self.assertEqual(rejected.exception.code, 'TARGET_KEY_INVALID')

    def test_completed_real_target_prevents_intent_preparation(self):
        now = timezone.now()
        grant = (AccessGrant.objects.select_related('principal', 'patient').filter(action='manage_draft',
            purpose='family_observation', field_codes__contains=['observation_note'], revoked_at__isnull=True,
            valid_from__lte=now, principal__account_status='active', patient__status='active', record_scope='all')
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now)).order_by('id').first())
        self.assertIsNotNone(grant)
        with transaction.atomic():
            scope = current_scope(owner=grant.principal, patient=grant.patient, auth_epoch=grant.principal.auth_epoch)
            drafts, _ = target_queries(scope)
            draft = drafts.filter(status='submitted').first()
            self.assertIsNotNone(draft)
            payload = {'target_type': 'draft', 'target_id': str(draft.id), 'target_version': str(draft.version),
                'due_at': (now + timedelta(minutes=1)).isoformat(), 'timezone': settings.TIME_ZONE,
                'quiet_start': None, 'quiet_end': None}
            result = prepared_scope(scope.owner, scope.owner.auth_epoch, OPERATIONS[0],
                f'/patients/{scope.patient.id}/material-reminders', payload)
            self.assertEqual(result[-1], 'REMINDER_TARGET_NOT_PENDING')
            self.assertEqual(result[0].patient.id, draft.patient_id)
            self.assertEqual(len(result[4]['target_payload_sha256']), 64)


if __name__ == '__main__':
    print(json.dumps(write_fingerprint()), flush=True)
    unittest.main(verbosity=2)
