import os
import unittest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.conf import settings
from django.urls import reverse

from api.base import ApiError
from api.views_releases import release_receipt
from contacts.models import ContactVersion
from configurations.models import ConfigApproval, ConfigVersion
from family_content.models import EnvironmentGate, ReleaseBundle
from requests.controlled import CONTROLLED_OPERATIONS, controlled_payload, controlled_target, controlled_target_ready
from requests.models import WriteIntent
from requests.service import IntentRejected


class ControlledRequestRuntimeTest(unittest.TestCase):
    def test_unrelated_saved_request_cannot_produce_controlled_receipt(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            intent = WriteIntent.objects.select_related('write_request').filter(operation_id='record_create',
                status='committed', write_request__isnull=False).order_by('id').first()
            self.assertIsNotNone(intent, '当前数据库需要已有记录提交凭据。')
            with self.assertRaisesRegex(ApiError, 'CONTROLLED_RECEIPT_INVALID'):
                release_receipt(intent)

    def test_unrelated_saved_intent_cannot_restore_controlled_payload(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            intent = WriteIntent.objects.filter(operation_id='record_create').order_by('id').first()
            self.assertIsNotNone(intent)
            with self.assertRaisesRegex(ApiError, 'CONTROLLED_REQUEST_INVALID'):
                controlled_payload(intent)

    def test_canonical_targets_use_actual_resource_ids(self):
        release = ReleaseBundle.objects.order_by('id').first()
        contact = ContactVersion.objects.order_by('id').first()
        self.assertIsNotNone(release)
        self.assertIsNotNone(contact)
        config = ConfigVersion.objects.order_by('id').first()
        approval = ConfigApproval.objects.order_by('id').first()
        self.assertIsNotNone(config)
        self.assertIsNotNone(approval)
        for operation in CONTROLLED_OPERATIONS:
            keys = {}
            if operation.startswith('release_') and operation != 'release_create':
                keys = {'release_id': release.id}
            elif operation.startswith('contact_') and operation != 'contact_create':
                keys = {'contact_version_id': contact.id}
            elif operation == 'config_approval_revoke':
                keys = {'approval_id': approval.id}
            elif operation.startswith('config_') and operation != 'config_create':
                keys = {'config_id': config.id}
            target = reverse(operation, kwargs=keys)
            with self.subTest(operation=operation):
                self.assertTrue(target.startswith('/api/support/'))
                self.assertEqual(controlled_target(operation, target), keys)
                with self.assertRaisesRegex(IntentRejected, 'TARGET_KEY_INVALID'):
                    controlled_target(operation, target.replace('/api/', '/api/v1/', 1))

    def test_existing_active_contact_cannot_prepare_repeated_activation(self):
        with transaction.atomic():
            contact = ContactVersion.objects.filter(status='active').order_by('id').first()
            self.assertIsNotNone(contact)
            gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
            original = (contact.status, contact.verified_at, gate.version, gate.publication_epoch)
            self.assertFalse(controlled_target_ready(contact.owner, gate, 'contact_activate',
                reverse('contact_activate', kwargs={'contact_version_id': contact.id}),
                {'expected_gate_version': str(gate.version), 'reason': '核查现有联系状态'}))
            contact.refresh_from_db()
            gate.refresh_from_db()
            self.assertEqual(original, (contact.status, contact.verified_at, gate.version, gate.publication_epoch))

    def test_existing_enabled_release_cannot_prepare_approval(self):
        with transaction.atomic():
            gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
            release = ReleaseBundle.objects.filter(environment=gate.environment, status='enabled').order_by('id').first()
            self.assertIsNotNone(release)
            self.assertFalse(controlled_target_ready(release.contact_version.owner, gate, 'release_approve',
                reverse('release_approve', kwargs={'release_id': release.id}),
                {'expected_state': release.status, 'expected_version': str(release.version), 'reason': '核查现有发布状态'}))
            release.refresh_from_db()
            self.assertEqual(release.status, 'enabled')

    def test_anonymous_recovery_endpoints_require_authentication(self):
        intent = WriteIntent.objects.order_by('id').first()
        self.assertIsNotNone(intent)
        paths = [('POST', '/api/write-intents'), ('POST', f'/api/write-intents/{intent.id}/retry'),
            ('GET', f'/api/requests/{intent.id}'), ('GET', '/api/requests')]
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for method, path in paths:
                with self.subTest(method=method, path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])
                    self.assertIn('private', response.headers['cache-control'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
