import json
import unittest
from datetime import datetime, timezone

import httpx
import psycopg

from tests.verify_v2_migration import ROOT, database_args, require, snapshot
from api.base import ApiError
from archives.batches import batch_runtime
from configurations.models import ConfigVersion
from files.models import FileGroup
from identity.models import Patient
from requests.models import WriteIntent


class ArchiveBatchWriteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.completed_checks = 0
        with psycopg.connect(**database_args('sjia_dev')) as database:
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cls.before = snapshot(database)
        cls.group = FileGroup.objects.order_by('id').first()
        require(cls.group is not None, '当前数据库缺少可核查的原件文件组。')
        cls.patient = Patient.objects.get(id=cls.group.patient_id)
        cls.intent = WriteIntent.objects.order_by('id').first()
        require(cls.intent is not None, '当前数据库缺少可核查的已有请求标识。')
        cls.payload = {'file_group_ids': [str(cls.group.id)]}
        cls.target = f'/patients/{cls.patient.id}/archive-batches'

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(**database_args('sjia_dev')) as database:
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            require(snapshot(database) == cls.before, '批次写入检查改变已有业务资料。')
        report = {'checked_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'ACTUAL_ANONYMOUS_BATCH_CREATE_RECOVERY_AND_SOURCE_PRESERVATION',
            'checks': cls.completed_checks, 'preserved_tables': len(cls.before),
            'batch_count': cls.before['archive_batch']['count'], 'page_count': cls.before['archive_page']['count'],
            'intent_count': cls.before['write_intent']['count'], 'request_count': cls.before['write_request']['count'],
            'clinical_data_created': False, 'authenticated_business_validation': 'NOT_RUN',
            'budget_execution_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
        (ROOT / '.planning-work/t42-batch-write.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))

    def assert_authentication_required(self, response):
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
        self.assertIn('private', response.headers['cache-control'])
        self.assertIn('no-store', response.headers['cache-control'])
        type(self).completed_checks += 1

    def test_create_requires_current_identity_before_source_lookup(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.post('/api/v2' + self.target, json=self.payload))

    def test_prepare_requires_current_identity(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.post('/api/v2/write-intents', json={
                'operation_id': 'archive_batch_create', 'target_key': self.target,
                'request_id': str(self.intent.request_id), 'schema_version': '2.0.0', 'payload': self.payload}))

    def test_original_request_lookup_requires_current_identity(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.get(f'/api/v2/requests/{self.intent.request_id}', params={
                'lookup': 'request_id', 'operation_id': 'archive_batch_create', 'target_key': self.target}))

    def test_intent_lookup_requires_current_identity(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.get(f'/api/v2/requests/{self.intent.id}'))

    def test_retry_requires_current_identity(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.post(f'/api/v2/write-intents/{self.intent.id}/retry',
                json={'expected_generation': str(self.intent.generation)}))

    def test_current_unconfigured_runtime_cannot_authorize_receiving(self):
        self.assertEqual(ConfigVersion.objects.filter(kind='provider').count(), 0)
        with self.assertRaises(ApiError) as rejected:
            batch_runtime()
        self.assertEqual(rejected.exception.code, 'RUNTIME_CONFIGURATION_PENDING')
        self.assertEqual(rejected.exception.http_status, 503)
        type(self).completed_checks += 1


if __name__ == '__main__':
    unittest.main(verbosity=2)
