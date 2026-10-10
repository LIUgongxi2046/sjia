import json
import unittest
from datetime import datetime, timezone

import httpx
import psycopg
from django.db import connection, transaction

from tests.verify_v2_migration import ROOT, database_args, require, snapshot
from archives.models import ArchiveBatch, ArchivePage
from identity.models import Patient
from files.models import FileObject, FilePage


class ArchiveBatchRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.completed_checks = 0
        with psycopg.connect(**database_args('sjia_dev')) as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            cls.before = snapshot(connection)
        cls.patient = Patient.objects.order_by('id').first()
        require(cls.patient is not None, '当前数据库缺少可核查的患儿身份。')

    @classmethod
    def tearDownClass(cls):
        with psycopg.connect(**database_args('sjia_dev')) as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            require(snapshot(connection) == cls.before, '批次读取检查改变已有业务资料。')
        report = {'checked_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'ACTUAL_ANONYMOUS_ARCHIVE_BATCH_HTTP_AND_EXISTING_SOURCE_REFERENCES',
            'checks': cls.completed_checks,
            'preserved_tables': len(cls.before), 'batch_count': cls.before['archive_batch']['count'],
            'page_count': cls.before['archive_page']['count'], 'clinical_data_created': False,
            'file_page_count': cls.before['file_page']['count'],
            'authenticated_business_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
        (ROOT / '.planning-work/t42-batch-read.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))

    def assert_authentication_required(self, response):
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
        self.assertIn('private', response.headers['cache-control'])
        self.assertIn('no-store', response.headers['cache-control'])
        type(self).completed_checks += 1

    def test_batch_read_authenticates_before_resource_lookup(self):
        batch = ArchiveBatch.objects.order_by('id').first()
        identifier = batch.id if batch is not None else self.patient.id
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.get(f'/api/v2/archive-batches/{identifier}'))

    def test_page_read_authenticates_before_resource_lookup(self):
        page = ArchivePage.objects.order_by('id').first()
        identifier = page.id if page is not None else self.patient.id
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.get(f'/api/v2/archive-pages/{identifier}'))

    def test_batch_directory_authenticates_before_patient_lookup(self):
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            self.assert_authentication_required(client.post(
                f'/api/v2/patients/{self.patient.id}/archive-batches/search', json={}))

    def test_existing_original_page_references_are_exact(self):
        from archives.batches import file_page_source

        count = 0
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            originals = FileObject.objects.filter(purpose='original').values('id')
            for file_page in FilePage.objects.filter(file_id__in=originals).select_related('patient').order_by('id'):
                source, group = file_page_source(file_page, file_page.patient)
                self.assertEqual(source.id, file_page.file_id)
                self.assertEqual(source.file_group_id, file_page.file_group_id)
                self.assertEqual(group.id, file_page.file_group_id)
                self.assertEqual(source.patient_id, file_page.patient_id)
                self.assertEqual(group.patient_id, file_page.patient_id)
                self.assertEqual(source.purpose, 'original')
                count += 1
        self.assertGreater(count, 0)
        type(self).completed_checks += 1


if __name__ == '__main__':
    unittest.main(verbosity=2)
