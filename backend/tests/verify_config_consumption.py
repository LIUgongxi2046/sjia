import os
import hashlib
import json
import unittest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction

from configurations.field_config import read_field_config
from configurations.models import ConfigApproval, ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from configurations.evidence import inspect_approved_evidence
from identity.models import Patient
from summaries.models import Summary, SummaryItem
from summaries.service import approved_snapshot_config, read_summary
from tasks.models import Task


class ConfigConsumptionRuntimeTest(unittest.TestCase):
    def test_field_configuration_requires_exact_current_approval(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            config = ConfigVersion.objects.filter(schema_version='field-config-1', status='enabled').order_by('id').first()
            patient = Patient.objects.order_by('id').first()
            self.assertIsNotNone(config)
            self.assertIsNotNone(patient)
            self.assertFalse(ConfigApproval.objects.filter(config=config,
                effective_scope__config_checksum=config.checksum, effective_scope__environment=settings.SJIA_ENVIRONMENT).exists())
            result = read_field_config(patient, config.payload['kind'], version=config.id)
            self.assertNotEqual(result['state'], 'available')
            self.assertEqual(result['fields'], [])

    def test_summary_configuration_requires_exact_current_approval(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            config = ConfigVersion.objects.filter(kind='summary', status='enabled').order_by('id').first()
            self.assertIsNotNone(config)
            self.assertFalse(ConfigApproval.objects.filter(config=config,
                effective_scope__config_checksum=config.checksum, effective_scope__environment=settings.SJIA_ENVIRONMENT).exists())
            self.assertFalse(approved_snapshot_config(config, config.checksum, settings.SJIA_ENVIRONMENT))

    def test_unqualified_summary_returns_no_content_and_preserves_saved_items(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            original = list(SummaryItem.objects.order_by('id').values())
            self.assertTrue(original)
            before = hashlib.sha256(json.dumps(original, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
            checked = 0
            for summary in Summary.objects.select_related('patient').order_by('id'):
                task = Task.objects.get(id=summary.task_id)
                if compute_checksum(task.input_refs) != task.input_sha256:
                    continue
                result = read_summary(summary.id, summary.patient)
                self.assertTrue(result['needs_regeneration'])
                self.assertEqual(result['items'], [])
                checked += 1
            self.assertGreater(checked, 0, '当前数据库需要具有准确任务摘要的已有摘要。')
            after = hashlib.sha256(json.dumps(list(SummaryItem.objects.order_by('id').values()),
                sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
            self.assertEqual(before, after)

    def test_current_configuration_without_exact_proof_is_unavailable(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            checked = 0
            for config in ConfigVersion.objects.filter(status='enabled'):
                if ConfigApproval.objects.filter(config=config, effective_scope__config_checksum=config.checksum,
                        effective_scope__environment=settings.SJIA_ENVIRONMENT).exists():
                    continue
                with self.subTest(config_id=str(config.id)):
                    self.assertIsNotNone(configuration_qualification_reason(config, settings.SJIA_ENVIRONMENT))
                checked += 1
            self.assertGreater(checked, 0)

    def test_existing_unregistered_approval_evidence_is_unavailable_without_writes(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            approval = ConfigApproval.objects.exclude(evidence_ref__startswith='urn:sjia:source-revision:').order_by('id').first()
            self.assertIsNotNone(approval)
            revision, content, reason = inspect_approved_evidence(approval.evidence_ref, purpose='configuration_approval')
            self.assertIsNone(revision)
            self.assertIsNone(content)
            self.assertEqual(reason, 'CONFIG_EVIDENCE_REFERENCE_INVALID')


if __name__ == '__main__':
    unittest.main(verbosity=2)
