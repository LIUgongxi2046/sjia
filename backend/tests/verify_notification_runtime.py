import hashlib
import json
import os
import unittest
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction
from django.utils import timezone

from alerting.models import AlertRecipient
from family_content.models import EnvironmentGate
from notifications.models import NotificationAttempt, NotificationReceipt
from notifications.service import NotificationRejected, authorize_attempt, cancel_attempt, mark_uncertain


def notification_fingerprint():
    records = {
        'recipients': list(AlertRecipient.objects.order_by('id').values()),
        'attempts': list(NotificationAttempt.objects.order_by('id').values()),
        'receipts': list(NotificationReceipt.objects.order_by('id').values()),
    }
    return {
        'counts': {name: len(rows) for name, rows in records.items()},
        'sha256': hashlib.sha256(json.dumps(records, cls=DjangoJSONEncoder,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
    }


class NotificationRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
        self.assertIsNone(self.gate.runtime_config_id, '本项需要当前未登记 runtime 的实际环境。')
        self.recipient = AlertRecipient.objects.order_by('id').first()
        self.assertIsNotNone(self.recipient, '本项需要当前已有站内接收记录。')
        self.assertEqual(self.recipient.channel, 'in_app')
        self.before = notification_fingerprint()

    def tearDown(self):
        self.assertEqual(notification_fingerprint(), self.before, '拒绝操作不得修改现有通知资料。')

    def test_unconfigured_callback_rejects_before_body_or_attempt_lookup(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', trust_env=False, timeout=10) as client:
            for prefix in ('/api', '/api/v1', '/api/v2'):
                path = f'{prefix}/integrations/{self.recipient.channel}/receipts'
                with self.subTest(path=path):
                    response = client.post(path)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()['code'], 'NOTIFICATION_PROVIDER_UNAVAILABLE')
                    self.assertIn('no-store', response.headers['cache-control'])
                    self.assertIn('private', response.headers['cache-control'])

    def test_unconfigured_environment_cannot_issue_send_permit(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            with self.assertRaises(NotificationRejected) as rejected:
                authorize_attempt(patient_id=self.recipient.patient_id, recipient_id=self.recipient.id,
                    provider_idempotency_key=str(self.recipient.id),
                    permit_expires_at=timezone.now() + timedelta(seconds=15))
            self.assertEqual(rejected.exception.reason_code, 'NOTIFICATION_PROVIDER_UNAVAILABLE')

    def test_existing_recipient_cannot_be_used_as_a_send_attempt(self):
        for operation in (cancel_attempt, mark_uncertain):
            with self.subTest(operation=operation.__name__), transaction.atomic():
                self.assertFalse(NotificationAttempt.objects.filter(id=self.recipient.id).exists())
                with self.assertRaises(NotificationRejected) as rejected:
                    operation(attempt=self.recipient)
                self.assertEqual(rejected.exception.reason_code, 'NOTIFICATION_ATTEMPT_NOT_FOUND')


if __name__ == '__main__':
    print(json.dumps(notification_fingerprint()), flush=True)
    unittest.main(verbosity=2)
