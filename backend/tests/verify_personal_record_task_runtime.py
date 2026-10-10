import os
import unittest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from drafts.models import Draft
from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
from authorization.models import AccessGrant
from archives.models import PersonalRecordTask
from notifications.errors import NotificationRejected
from notifications.material_delivery import material_unavailability_reason, notice_list, record_material_failure
from notifications.material_scheduler import due_candidates, schedule_due
from notifications.personal_record_tasks import SCHEMA, REGISTRY, task_list
from notifications import reminders
from requests.personal_record_tasks import task_route
from jsonschema import Draft202012Validator, FormatChecker
from tests.verify_v2_migration import database_args, snapshot
from tasks.models import OutboxEvent
import psycopg


class PersonalRecordTaskRuntimeTest(unittest.TestCase):
    def setUp(self):
        with psycopg.connect(**database_args(settings.DATABASES['default']['NAME'])) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            self.before = snapshot(conn)

    def tearDown(self):
        with psycopg.connect(**database_args(settings.DATABASES['default']['NAME'])) as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            self.assertEqual(snapshot(conn), self.before)

    def scope(self):
        now = timezone.now()
        grant = AccessGrant.objects.select_related('principal', 'patient').filter(action='manage_draft',
            purpose='family_observation', record_scope__in=['own', 'all'], valid_from__lte=now,
            revoked_at__isnull=True, principal__account_status='active', patient__status='active')
        grant = grant.filter(Q(valid_until__isnull=True) | Q(valid_until__gt=now)).order_by('id').first()
        self.assertIsNotNone(grant)
        return reminders.current_scope(owner=grant.principal, patient=grant.patient, auth_epoch=grant.principal.auth_epoch)

    def test_anonymous_record_task_operations_require_identity(self):
        draft = Draft.objects.order_by('id').first()
        self.assertIsNotNone(draft)
        routes = (
            ('POST', f'/api/v2/patients/{draft.patient_id}/personal-record-tasks'),
            ('POST', f'/api/v2/patients/{draft.patient_id}/personal-record-tasks/search'),
            ('GET', f'/api/v2/personal-record-tasks/{draft.id}'),
            ('POST', f'/api/v2/personal-record-tasks/{draft.id}/status'),
            ('GET', f'/api/v2/patients/{draft.patient_id}/personal-record-task-retention'),
            ('POST', f'/api/v2/personal-record-task-intents/{draft.id}/cancel'),
            ('POST', f'/api/v2/material-reminder-intents/{draft.id}/cancel'),
            ('POST', f'/api/v2/patients/{draft.patient_id}/material-reminder-notices/search'),
            ('GET', f'/api/v2/material-reminder-notices/{draft.id}'),
            ('GET', f'/api/v2/material-reminder-notices/{draft.id}/message'),
            ('POST', f'/api/v2/material-reminder-notices/{draft.id}/delivered'),
            ('POST', f'/api/v2/material-reminder-notices/{draft.id}/read'),
            ('POST', f'/api/v2/material-reminder-notices/{draft.id}/retry'),
        )
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for method, route in routes:
                with self.subTest(route=route):
                    response = client.request(method, route)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])

    def test_real_empty_personal_directory_and_notice_history_are_read_only(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            scope = self.scope()
            self.assertFalse(PersonalRecordTask.objects.exists())
            targets = reminders.reminder_target_list(scope=scope, target_type='record_task', limit=2,
                expires_at=timezone.now() + timedelta(minutes=1))
            self.assertEqual(targets['items'], [])
            self.assertEqual(targets['capability_state'], 'available')
            notices = notice_list(scope=scope, limit=2, expires_at=timezone.now() + timedelta(minutes=1))
            self.assertEqual(notices['items'], [])
            self.assertIsNone(notices['next_cursor'])
            self.assertEqual(len(notices['snapshot_version']), 64)

    def test_actual_draft_identity_cannot_be_used_as_personal_task(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            scope = self.scope()
            draft = Draft.objects.filter(patient=scope.patient).order_by('id').first()
            self.assertIsNotNone(draft)
            with self.assertRaises(NotificationRejected) as rejected:
                reminders.target_snapshot(scope, 'record_task', draft.id)
            self.assertEqual(rejected.exception.reason_code, 'REMINDER_TARGET_UNAVAILABLE')

    def test_empty_due_candidates_and_unqualified_scheduler_create_no_effect(self):
        self.assertEqual(due_candidates(limit=10, now=timezone.now()), [])
        with self.assertRaises(NotificationRejected) as rejected:
            schedule_due()
        self.assertEqual(rejected.exception.reason_code, 'NOTIFICATION_PROVIDER_UNAVAILABLE')
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            self.assertEqual(material_unavailability_reason(), 'NOTIFICATION_PROVIDER_UNAVAILABLE')

    def test_registered_schema_definitions_and_routes_use_current_modules(self):
        for name in SCHEMA['$defs']:
            Draft202012Validator.check_schema({**SCHEMA, '$ref': f'#/$defs/{name}'})
        draft = Draft.objects.order_by('id').first()
        self.assertEqual(task_route('personal_record_task_create',
            f'/patients/{draft.patient_id}/personal-record-tasks').kwargs['patient_id'], draft.patient_id)
        self.assertEqual(task_route('personal_record_task_change',
            f'/personal-record-tasks/{draft.id}/status').kwargs['task_id'], draft.id)

    def test_task_table_constraints_are_validated(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT conname, convalidated FROM pg_constraint WHERE conrelid='sjia.personal_record_task'::regclass")
            constraints = cursor.fetchall()
        self.assertEqual(len(constraints), 20)
        self.assertTrue(all(row[1] for row in constraints))

    def test_actual_other_event_cannot_update_reminder_failure(self):
        event = OutboxEvent.objects.exclude(event_type='notification.material_requested').order_by('id').first()
        self.assertIsNotNone(event)
        with self.assertRaises(NotificationRejected) as rejected:
            record_material_failure(event, 'REMINDER_EXECUTION_FAILED')
        self.assertEqual(rejected.exception.reason_code, 'REMINDER_EVENT_SCOPE_MISMATCH')


if __name__ == '__main__':
    unittest.main(verbosity=2)
