import os
import hashlib
import json
import unittest
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.conf import settings
from jsonschema import Draft202012Validator, FormatChecker

from api.base import ApiError
from api.views_configs import configuration_view
from configurations.evidence import evidence_revision_id
from configurations.lifecycle import configuration_target_ready, current_approval
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.service import ConfigRejected, register_config
from family_content.models import EnvironmentGate
from requests.controlled import PROTOCOL, checked_controlled_input


BASELINE = Path(__file__).resolve().parents[2] / '.planning-work/t51-config-baseline.json'


def configuration_fingerprint():
    rows = {model._meta.db_table: list(model.objects.order_by('id').values())
        for model in (ConfigVersion, ConfigApproval, ConfigValidation)}
    return {'counts': {key: len(value) for key, value in rows.items()},
        'content_sha256': hashlib.sha256(json.dumps(rows, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()}


class ConfigLifecycleRuntimeTest(unittest.TestCase):
    def test_existing_untyped_rule_cannot_register_as_usable_schema(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            config = ConfigVersion.objects.filter(kind='rule', schema_version='rule-1').order_by('id').first()
            self.assertIsNotNone(config, '当前数据库需要已有规则配置用于拒绝检查。')
            with self.assertRaisesRegex(ConfigRejected, 'CONFIG_SCHEMA_UNAVAILABLE'):
                register_config(kind=config.kind, config_key=config.config_key, schema_version=config.schema_version,
                    payload=config.payload, created_by=config.created_by, source_revision=config.source_revision)

    def test_existing_configuration_projection_matches_protocol(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/ConfigView'}, format_checker=FormatChecker())
            configs = list(ConfigVersion.objects.all())
            self.assertTrue(configs)
            for config in configs:
                with self.subTest(config_id=str(config.id)):
                    result = configuration_view(config)
                    validator.validate(result)
                    self.assertEqual(result['config']['payload'], config.payload)
                    self.assertEqual(result['checksum'], config.checksum)

    def test_existing_validation_outcome_cannot_be_supplied_by_client(self):
        validation = ConfigValidation.objects.order_by('id').first()
        self.assertIsNotNone(validation)
        payload = {'config_checksum': validation.config_checksum, 'environment': validation.environment_id,
            'artifact_sha256': validation.artifact_sha256, 'dataset_revision_id': str(validation.dataset_revision_id),
            'evidence_ref': validation.evidence_ref, 'evidence_sha256': validation.evidence_sha256,
            'outcome': validation.outcome}
        with self.assertRaisesRegex(ApiError, 'CONTROLLED_INPUT_INVALID'):
            checked_controlled_input('ValidationInput', payload)

    def test_existing_validation_reference_requires_registered_evidence(self):
        validation = ConfigValidation.objects.exclude(evidence_ref__startswith='urn:sjia:source-revision:').order_by('id').first()
        self.assertIsNotNone(validation)
        with self.assertRaisesRegex(ConfigRejected, 'CONFIG_EVIDENCE_REFERENCE_INVALID'):
            evidence_revision_id(validation.evidence_ref)

    def test_existing_approval_requires_exact_configuration_scope(self):
        with transaction.atomic():
            gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
            config = ConfigVersion.objects.filter(kind='rule', status='enabled').order_by('id').first()
            self.assertIsNotNone(config)
            self.assertFalse(ConfigApproval.objects.filter(config=config, effective_scope__environment=gate.environment,
                effective_scope__config_checksum=config.checksum).exists())
            with self.assertRaisesRegex(ConfigRejected, 'CONFIG_APPROVAL_UNAVAILABLE'):
                current_approval(config, gate)

    def test_enabled_configuration_cannot_be_submitted_again(self):
        with transaction.atomic():
            gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
            config = ConfigVersion.objects.filter(status='enabled').order_by('id').first()
            self.assertIsNotNone(config)
            self.assertFalse(configuration_target_ready(config.created_by, gate, 'config_submit', {'config_id': config.id},
                {'expected_state': config.status, 'expected_version': str(config.version), 'reason': '核查现有配置状态'}))

    def test_configuration_records_preserved(self):
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION READ ONLY')
            self.assertEqual(configuration_fingerprint(), json.loads(BASELINE.read_text()))

    def test_anonymous_configuration_endpoints_require_authentication(self):
        config = ConfigVersion.objects.order_by('id').first()
        approval = ConfigApproval.objects.order_by('id').first()
        self.assertIsNotNone(config)
        self.assertIsNotNone(approval)
        paths = [('POST', '/api/support/configs'), ('GET', f'/api/support/configs/{config.id}'),
            ('POST', f'/api/support/config-approvals/{approval.id}/revoke')]
        paths.extend(('POST', f'/api/support/configs/{config.id}/{action}')
            for action in ('submit', 'approvals', 'validations', 'enable', 'disable'))
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
