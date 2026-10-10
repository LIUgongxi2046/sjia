import hashlib
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction

from contacts.models import ContactVersion
from contacts.approval import contact_content_reason
from contacts.service import ContactRejected, activate_saved_contact, save_contact
from family_content.models import EnvironmentGate, ReleaseBundle
from family_content.qualification import contact_qualification_reason
from django.utils import timezone


class ContactRuntimeTest(unittest.TestCase):
    def test_saved_contact_without_exact_approval_cannot_create_version(self):
        with transaction.atomic():
            release = ReleaseBundle.objects.select_related('contact_version__approval_config',
                'contact_version__source_revision', 'contact_version__owner').order_by('id').first()
            self.assertIsNotNone(release, '当前数据库需要已有联系来源。')
            contact = release.contact_version
            self.assertNotEqual(contact.approval_config.schema_version, 'contact-1', '本项需要未具备准确联系批准的既有记录。')
            gate = EnvironmentGate.objects.get(environment=release.environment)
            payload = {'contact_id': str(contact.contact_id), 'contact_name': contact.contact_name,
                'purpose': contact.purpose, 'channel': contact.channel, 'channel_value': contact.channel_value,
                'source_revision_id': str(contact.source_revision_id), 'valid_until': contact.valid_until.isoformat()}
            before = ContactVersion.objects.count()
            with self.assertRaises(ContactRejected) as rejected:
                save_contact(owner=contact.owner, gate=gate, payload=payload, approval_config=contact.approval_config)
            self.assertEqual(rejected.exception.reason_code, 'CONTACT_APPROVAL_SCHEMA_UNAVAILABLE')
            self.assertEqual(ContactVersion.objects.count(), before)

    def test_active_contact_rejects_repeated_activation(self):
        with transaction.atomic():
            release = ReleaseBundle.objects.select_related('contact_version').order_by('id').first()
            self.assertIsNotNone(release)
            self.assertEqual(release.contact_version.status, 'active', '本项需要当前已有启用联系记录。')
            gate = EnvironmentGate.objects.get(environment=release.environment)
            version = gate.version
            with self.assertRaises(ContactRejected) as rejected:
                activate_saved_contact(contact=release.contact_version, gate=gate, expected_gate_version=version)
            self.assertEqual(rejected.exception.reason_code, 'CONTACT_NOT_ACTIVATABLE')
            gate.refresh_from_db()
            self.assertEqual(gate.version, version)

    def test_existing_contact_content_is_preserved(self):
        baseline = json.loads((Path(__file__).resolve().parents[2] / '.planning-work/t51-contact-baseline.json').read_text())
        self.assertEqual(current_contact_fingerprint(), baseline)

    def test_anonymous_contact_requests_are_rejected(self):
        contact = ContactVersion.objects.order_by('id').first()
        self.assertIsNotNone(contact, '当前数据库需要已有联系记录。')
        paths = ['/api/support/contacts', f'/api/support/contacts/{contact.id}/activate',
            f'/api/support/contacts/{contact.id}/disable']
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for path in paths:
                with self.subTest(path=path):
                    response = client.post(path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])
                    self.assertIn('private', response.headers['cache-control'])

    def test_current_contact_requires_approval_of_exact_content(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            releases = list(ReleaseBundle.objects.select_related('contact_version__approval_config',
                'contact_version__owner', 'contact_version__source_revision__source'))
            self.assertTrue(releases, '当前数据库需要已有发布及联系记录。')
            for release in releases:
                contact = release.contact_version
                if contact_content_reason(contact, release.environment) is not None:
                    self.assertIsNotNone(contact_qualification_reason(contact, release.environment, timezone.now()))

    def test_unverified_draft_storage_is_available(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
                cursor.execute("SELECT attnotnull FROM pg_attribute WHERE attrelid = 'sjia.contact_version'::regclass AND attname = 'verified_at'")
                result = cursor.fetchone()
            self.assertIsNotNone(result)
            self.assertFalse(result[0], '未核验草稿需要保留空白 verified_at。')
            with connection.cursor() as cursor:
                cursor.execute("SELECT convalidated FROM pg_constraint WHERE conrelid = 'sjia.contact_version'::regclass AND conname = 'contact_verified_state_requires_time'")
                constraint = cursor.fetchone()
            self.assertEqual(constraint, (True,), '已批准和启用状态必须受到核验时间约束。')

    def test_saved_active_contacts_have_verification(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            self.assertTrue(ContactVersion.objects.exists(), '当前数据库需要已有联系记录。')
            self.assertFalse(ContactVersion.objects.filter(status__in=['approved', 'active'], verified_at__isnull=True).exists())


def current_contact_fingerprint():
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
        rows = list(ContactVersion.objects.order_by('id').values())
        digest = hashlib.sha256(json.dumps(rows, cls=DjangoJSONEncoder, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        return {'contacts': len(rows), 'content_sha256': digest, 'database_write': False}


if __name__ == '__main__':
    print(json.dumps(current_contact_fingerprint()), flush=True)
    unittest.main(verbosity=2)
