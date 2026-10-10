import json
import unittest
from datetime import datetime, timezone

import httpx
import psycopg

from tests.verify_v2_migration import ROOT, database_args, require, snapshot
from tests.verify_v2_migration import object_manifest, sha256
from tests.verify_workflow_evaluation_migration import check_schema
from django.conf import settings
from identity.models import Patient
from requests.models import WriteIntent


class EvaluationRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checked = 0
        with psycopg.connect(**database_args('sjia_dev')) as database:
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cls.before = snapshot(database)
        cls.patient = Patient.objects.order_by('id').first()
        cls.intent = WriteIntent.objects.order_by('id').first()
        require(cls.patient is not None and cls.intent is not None, '当前检查需要已有主体和请求标识。')

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(**database_args('sjia_dev')) as database:
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            require(snapshot(database) == cls.before, '评价入口检查改变已有业务资料。')
        report = {'checked_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'ACTUAL_ANONYMOUS_EVALUATION_API_AND_SOURCE_PRESERVATION',
            'checks': cls.checked, 'preserved_tables': len(cls.before),
            'dataset_count': cls.before['evaluation_dataset_version']['count'],
            'authenticated_business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
        (ROOT / '.planning-work/t48-routes.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))

    def check_auth(self, method, path, **kwargs):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            response = client.request(method, '/api/v2' + path, **kwargs)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
        self.assertIn('private', response.headers['cache-control'])
        self.assertIn('no-store', response.headers['cache-control'])
        type(self).checked += 1

    def test_dataset_search_requires_identity(self):
        self.check_auth('POST', '/support/datasets/search', json={})

    def test_dataset_create_requires_identity(self):
        self.check_auth('POST', '/support/datasets', json={})

    def test_dataset_read_requires_identity_before_object_lookup(self):
        self.check_auth('GET', f'/support/datasets/{self.patient.id}')

    def test_partition_requires_identity_before_object_lookup(self):
        self.check_auth('POST', f'/support/datasets/{self.patient.id}/partitions', json={})

    def test_label_requires_identity_before_object_lookup(self):
        self.check_auth('POST', f'/support/datasets/{self.patient.id}/labels', json={})

    def test_freeze_requires_identity_before_object_lookup(self):
        self.check_auth('POST', f'/support/datasets/{self.patient.id}/freeze', json={})

    def test_run_create_requires_identity_before_object_lookup(self):
        self.check_auth('POST', f'/support/datasets/{self.patient.id}/runs', json={})

    def test_run_read_requires_identity_before_object_lookup(self):
        self.check_auth('GET', f'/support/evaluation-runs/{self.patient.id}')

    def test_run_denominators_require_identity_before_object_lookup(self):
        self.check_auth('GET', f'/support/evaluation-runs/{self.patient.id}/denominators')

    def test_run_results_require_identity_before_object_lookup(self):
        self.check_auth('GET', f'/support/evaluation-runs/{self.patient.id}/results')

    def test_prepare_requires_identity(self):
        self.check_auth('POST', '/write-intents', json={'operation_id': 'dataset_create'})

    def test_request_lookup_requires_identity(self):
        self.check_auth('GET', f'/requests/{self.intent.request_id}', params={
            'lookup': 'request_id', 'operation_id': 'dataset_create', 'target_key': '/support/datasets'})

    def test_retry_requires_identity(self):
        self.check_auth('POST', f'/write-intents/{self.intent.id}/retry', json={
            'expected_generation': str(self.intent.generation)})

    def test_workflow_migration_restored_sources_match_current(self):
        report = json.loads((ROOT / '.planning-work/t48-workflow-migration.json').read_text())
        self.assertEqual(report['status'], 'PASS')
        self.assertIs(report['source_applied'], True)
        backup = ROOT / report['backup_path']
        self.assertEqual(sha256(backup), report['backup_sha256'])
        self.assertEqual(sha256(backup.with_name('objects.zip')), report['objects_sha256'])
        with psycopg.connect(**database_args('sjia_dev')) as source:
            source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            self.assertEqual(check_schema(source), report['constraint'])
            objects = object_manifest(source, settings.FILE_OBJECT_ROOT)
        with psycopg.connect(**database_args(report['restored_database'])) as restored:
            restored.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            self.assertEqual(snapshot(restored), self.before)
            self.assertEqual(check_schema(restored), report['constraint'])
            self.assertEqual(object_manifest(restored, backup.parent / 'restored-objects'), objects)
        type(self).checked += 1


if __name__ == '__main__':
    unittest.main(verbosity=2)
