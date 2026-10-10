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
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from archives.inputs import PROTOCOL, REGISTRY
from archives.models import MaterialReminder, MaterialReminderNotice
from authorization.models import AccessGrant
from drafts.models import Draft
from notifications.errors import NotificationRejected
from notifications.reminders import current_scope, reminder_list, save_reminder, target_queries, target_snapshot
from notifications.reminder_schemas import reminder_input


def reminder_fingerprint():
    rows = {'drafts': list(Draft.objects.order_by('id').values()),
        'reminders': list(MaterialReminder.objects.order_by('id').values()),
        'notices': list(MaterialReminderNotice.objects.order_by('id').values())}
    return {'counts': {name: len(items) for name, items in rows.items()},
        'sha256': hashlib.sha256(json.dumps(rows, cls=DjangoJSONEncoder, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()}


class ReminderRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.before = reminder_fingerprint()
        self.draft = Draft.objects.select_related('owner', 'patient').order_by('id').first()
        self.assertIsNotNone(self.draft, '本项需要当前已有本人草稿。')

    def tearDown(self):
        self.assertEqual(reminder_fingerprint(), self.before, '提醒检查不得修改已有业务资料。')

    def active_scope(self):
        now = timezone.now()
        grant = (AccessGrant.objects.select_related('principal', 'patient').filter(action='manage_draft',
            purpose='family_observation', field_codes__contains=['observation_note'], revoked_at__isnull=True, valid_from__lte=now,
            principal__account_status='active', patient__status='active', record_scope='all')
            .filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now)).order_by('id').first())
        self.assertIsNotNone(grant, '本项需要已有有效本人草稿授权。')
        return current_scope(owner=grant.principal, patient=grant.patient, auth_epoch=grant.principal.auth_epoch)

    def test_anonymous_reminder_reads_require_authentication(self):
        endpoints = [('POST', f'/api/v2/patients/{self.draft.patient_id}/material-reminders/search'),
            ('GET', f'/api/v2/material-reminders/{self.draft.id}')]
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for method, path in endpoints:
                with self.subTest(path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])

    def test_existing_draft_targets_keep_actual_completion_state(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            scope = self.active_scope()
            drafts, _ = target_queries(scope)
            self.assertTrue(drafts.exists())
            checked = 0
            for draft in drafts:
                target, state = target_snapshot(scope, 'draft', draft.id, draft.version)
                self.assertEqual(target.id, draft.id)
                if draft.status == 'submitted':
                    self.assertEqual(state, 'completed')
                elif draft.status == 'editing' and (draft.expires_at is None or draft.expires_at > timezone.now()):
                    self.assertEqual(state, 'pending')
                else:
                    self.assertEqual(state, 'cancelled')
                checked += 1
            self.assertGreater(checked, 0)

    def test_completed_real_draft_cannot_create_pending_reminder(self):
        with transaction.atomic():
            scope = self.active_scope()
            drafts, _ = target_queries(scope)
            draft = drafts.filter(status='submitted').first()
            self.assertIsNotNone(draft)
            value = {'target_type': 'draft', 'target_id': str(draft.id), 'target_version': str(draft.version),
                'due_at': (timezone.now() + timedelta(minutes=1)).isoformat(), 'timezone': settings.TIME_ZONE,
                'quiet_start': None, 'quiet_end': None}
            with self.assertRaises(NotificationRejected) as rejected:
                save_reminder(owner=scope.owner, patient=scope.patient, auth_epoch=scope.owner.auth_epoch, payload=value)
            self.assertEqual(rejected.exception.reason_code, 'REMINDER_TARGET_NOT_PENDING')

    def test_existing_other_owner_draft_is_unavailable(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            scope = self.active_scope()
            other = Draft.objects.exclude(owner=scope.owner).first()
            self.assertIsNotNone(other)
            with self.assertRaises(NotificationRejected) as rejected:
                target_snapshot(scope, 'draft', other.id, other.version)
            self.assertEqual(rejected.exception.reason_code, 'REMINDER_TARGET_UNAVAILABLE')

    def test_current_empty_reminder_list_is_exact(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            self.assertFalse(MaterialReminder.objects.exists(), '本项使用当前真实空提醒表。')
            scope = self.active_scope()
            value = reminder_list(scope=scope, limit=1, expires_at=timezone.now() + timedelta(minutes=1))
            self.assertEqual(value['items'], [])
            self.assertIsNone(value['next_cursor'])
            self.assertEqual(len(value['snapshot_version']), 64)
            validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/ReminderList'},
                registry=REGISTRY, format_checker=FormatChecker())
            self.assertEqual(list(validator.iter_errors(value)), [])

    def test_saved_past_time_cannot_become_a_future_schedule(self):
        value = {'target_type': 'draft', 'target_id': str(self.draft.id), 'target_version': str(self.draft.version),
            'due_at': self.draft.saved_at.isoformat(), 'timezone': settings.TIME_ZONE,
            'quiet_start': None, 'quiet_end': None}
        with self.assertRaises(NotificationRejected) as rejected:
            reminder_input(value, now=timezone.now())
        self.assertEqual(rejected.exception.reason_code, 'REMINDER_SCHEDULE_INVALID')


if __name__ == '__main__':
    print(json.dumps(reminder_fingerprint()), flush=True)
    unittest.main(verbosity=2)
