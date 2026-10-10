import hashlib
import json
import os
import unittest
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from django.db import connection, transaction

from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.schemas import payload_reason
from configurations.service import ConfigRejected, register_config


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / '.planning-work/t51-config-baseline.json'


class ArchiveQuotaConfigurationTest(unittest.TestCase):
    def test_source_inventory_is_not_a_quota_configuration(self):
        payload = json.loads((ROOT / 'demo-data/current-patient/source-manifest.json').read_text())
        self.assertEqual(payload_reason('provider', 'archive-quota-1', payload), 'CONFIG_PAYLOAD_INVALID')

    def test_existing_configuration_cannot_supply_unrecorded_quota(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            configs = list(ConfigVersion.objects.exclude(schema_version='archive-quota-1').order_by('id'))
            self.assertTrue(configs, '当前检查需要已有配置。')
            for config in configs:
                with self.subTest(config_id=str(config.id)):
                    self.assertNotEqual(config.schema_version, 'archive-quota-1')
                    self.assertEqual(payload_reason('provider', 'archive-quota-1', config.payload),
                        'CONFIG_PAYLOAD_INVALID')
                    with self.assertRaisesRegex(ConfigRejected, '^CONFIG_PAYLOAD_INVALID$'):
                        register_config(kind='provider', config_key=config.config_key,
                            schema_version='archive-quota-1', payload=config.payload,
                            created_by=config.created_by, source_revision=config.source_revision)

    def test_actual_quota_schema_requires_prices_and_evidence_without_defaults(self):
        schema = json.loads((ROOT / 'backend/archives/quota.schema.json').read_text())
        fields = {'per_page_reservation', 'per_batch_budget', 'daily_budget',
            'measurement_evidence_ref', 'pricing_evidence_ref', 'runtime_config_id', 'provider_config_id'}
        self.assertTrue(fields <= set(schema['required']))
        pending = [schema]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                self.assertNotIn('default', value)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    def test_configuration_records_are_preserved(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            rows = {model._meta.db_table: list(model.objects.order_by('id').values())
                for model in (ConfigVersion, ConfigApproval, ConfigValidation)}
            actual = {'counts': {key: len(value) for key, value in rows.items()},
                'content_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, default=str,
                    ensure_ascii=False).encode()).hexdigest()}
            self.assertEqual(actual, json.loads(BASELINE.read_text()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
