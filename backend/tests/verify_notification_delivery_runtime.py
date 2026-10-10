import json
import os
import unittest
from hashlib import sha256

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.utils import timezone

from alerting.models import AlertRecipient
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from django.conf import settings
from django.urls import resolve, reverse
from notifications.errors import NotificationRejected
from notifications.native import native_configuration
from notifications.reading import notification_record
from notifications.models import NotificationAttempt, NotificationReceipt
from requests.models import WriteIntent
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from tests.verify_v2_migration import ROOT, require, snapshot


class NotificationDeliveryRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atomic = transaction.atomic()
        cls.atomic.__enter__()
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cls.before = snapshot(connection.connection)
        cls.counts = {'anonymous_http_requests': 0}
        cls.sources = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/api/urls_v2.py', 'backend/api/views_notifications.py', 'backend/api/views_v2_requests.py',
            'backend/api/views_family.py', 'backend/api/base.py', 'backend/notifications/native.py',
            'backend/notifications/policies.py', 'backend/notifications/reading.py', 'backend/notifications/service.py',
            'backend/notifications/availability.py', 'backend/requests/notifications.py', 'backend/tasks/worker.py',
            'backend/notifications/management/commands/reconcile_native_notification.py',
            'backend/tasks/dispatcher.py', 'backend/configurations/schemas.py', 'backend/configurations/service.py',
            'backend/authorization/actions.py', 'backend-design/notification.schema.json',
            'backend/tests/verify_notification_delivery_runtime.py')}
        cls.passed = True

    def tearDown(self):
        if self._outcome.result.failures or self._outcome.result.errors:
            type(self).passed = False

    @classmethod
    def tearDownClass(cls):
        require(snapshot(connection.connection) == cls.before, '通知检查改变现有业务资料。')
        report = {'checked_at': timezone.now().isoformat(), 'status': 'PASS' if cls.passed else 'FAIL',
            'scope': 'ACTUAL_NOTIFICATION_DELIVERY_HTTP', 'counts': cls.counts, 'preserved_tables': len(cls.before),
            'business_writes': 0, 'source_sha256': cls.sources, 'positive_notification': 'NOT_RUN', 'visual': 'NOT_RUN'}
        (ROOT / '.planning-work/t52-delivery-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        cls.atomic.__exit__(None, None, None)
        print(json.dumps({key: value for key, value in report.items() if key != 'source_sha256'}, ensure_ascii=False))

    def test_delivery_reads_require_identity_before_resource_lookup(self):
        recipient = AlertRecipient.objects.order_by('id').first()
        self.assertIsNotNone(recipient)
        paths = ['/api/v2/support/notification-scopes', f'/api/v2/patients/{recipient.patient_id}/notification-records',
            f'/api/v2/support/patients/{recipient.patient_id}/notification-records',
            f'/api/v2/notification-records/{recipient.id}', f'/api/v2/support/notification-records/{recipient.id}',
            f'/api/v2/notification-records/{recipient.id}/message', f'/api/v2/alerts/{recipient.alert_id}/notification']
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for path in paths:
                response = client.get(path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                self.assertIn('no-store', response.headers['cache-control'])
                self.counts['anonymous_http_requests'] += 1

    def test_notification_mutations_require_identity_before_body(self):
        recipient = AlertRecipient.objects.order_by('id').first()
        intent = WriteIntent.objects.order_by('id').first()
        self.assertIsNotNone(recipient)
        self.assertIsNotNone(intent)
        paths = [f'/api/v2/support/notification-records/{recipient.id}/send',
            f'/api/v2/notification-records/{recipient.id}/delivered', f'/api/v2/support/notification-intents/{intent.id}/cancel']
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for path in paths:
                response = client.post(path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                self.assertIn('no-store', response.headers['cache-control'])
                self.counts['anonymous_http_requests'] += 1

    def test_actual_existing_records_keep_unproved_delivery_unknown(self):
        self.assertEqual(NotificationAttempt.objects.count(), 0)
        self.assertEqual(NotificationReceipt.objects.count(), 0)
        protocol = json.loads((ROOT / 'backend-design/protocol.schema.json').read_text())
        schema = json.loads((ROOT / 'backend-design/notification.schema.json').read_text())
        registry = Registry().with_resource('protocol.schema.json', Resource.from_contents(protocol))
        validator = Draft202012Validator({**schema, '$ref': '#/$defs/Record'}, registry=registry, format_checker=FormatChecker())
        count = 0
        for row in AlertRecipient.objects.order_by('id'):
            record = notification_record(row)
            validator.validate(record)
            self.assertEqual(record['recipient_id'], str(row.id))
            self.assertEqual(record['trace_state'], 'unavailable')
            self.assertEqual(record['attempts'], [])
            self.assertEqual(record['delivery_status'], 'not_sent' if row.delivery_status == 'not_sent' else 'unknown')
            self.assertEqual(record['snapshot_version'], compute_checksum({key: value for key, value in record.items() if key != 'snapshot_version'}))
            count += 1
        self.assertGreater(count, 0)
        self.counts['actual_unproved_records'] = count

    def test_actual_environment_cannot_enable_native_without_runtime(self):
        gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
        self.assertIsNone(gate.runtime_config_id)
        with self.assertRaises(NotificationRejected) as rejected:
            native_configuration(gate)
        self.assertEqual(rejected.exception.reason_code, 'NOTIFICATION_PROVIDER_UNAVAILABLE')

    def test_registered_send_route_uses_actual_recipient_identity(self):
        recipient = AlertRecipient.objects.order_by('id').first()
        path = reverse('notification_v2_send', kwargs={'recipient_id': recipient.id})
        self.assertEqual(path, f'/api/v2/support/notification-records/{recipient.id}/send')
        self.assertEqual(resolve(path).kwargs['recipient_id'], recipient.id)


if __name__ == '__main__':
    unittest.main(verbosity=2)
