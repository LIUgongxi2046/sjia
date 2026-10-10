import json
import os
import unittest
from hashlib import sha256

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import PROTOCOL, REGISTRY
from files.models import FileObject
from records.models import RecordField, RecordVersion
from tasks.models import Task
from tests.verify_v2_migration import ROOT, require, snapshot
from verification.models import FieldVerificationState
from sources.models import DataUseAuthorization
from verification.service import VerificationRejected
from verification.workflow import field_view, verification_query


class VerificationWorkflowRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atomic = transaction.atomic()
        cls.atomic.__enter__()
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cls.before = snapshot(connection.connection)
        cls.counts = {'field_projections': 0, 'anonymous_http_requests': 0, 'actual_source_queries': 0}
        cls.sources = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/verification/workflow.py', 'backend/requests/verifications.py', 'backend/api/views_verifications.py',
            'backend/api/views_support.py', 'backend/api/views_v2_requests.py', 'backend/api/urls_v2.py', 'backend/api/base.py',
            'backend-design/v2-protocol.schema.json', 'backend-design/v2-api-catalog.csv',
            'backend/tests/verify_verification_workflow_runtime.py')}
        cls.passed = True

    def tearDown(self):
        result = self._outcome.result
        if result.failures or result.errors:
            type(self).passed = False

    @classmethod
    def tearDownClass(cls):
        require(snapshot(connection.connection) == cls.before, '核对检查改变已保存资料。')
        require(all(sha256((ROOT / path).read_bytes()).hexdigest() == value for path, value in cls.sources.items()),
            '核对检查期间代码发生变化。')
        report = {'checked_at': timezone.now().isoformat(), 'status': 'PASS' if cls.passed else 'FAIL',
            'scope': 'ACTUAL_FIELD_PROJECTION_AND_ANONYMOUS_HTTP', 'counts': cls.counts,
            'preserved_tables': len(cls.before), 'business_writes': 0, 'source_sha256': cls.sources,
            'positive_verification': 'NOT_RUN', 'clinical_acceptance': 'NOT_RUN', 'visual': 'NOT_RUN'}
        (ROOT / '.planning-work/t49-verification-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        cls.atomic.__exit__(None, None, None)
        print(json.dumps({key: value for key, value in report.items() if key != 'source_sha256'}, ensure_ascii=False))

    def test_actual_saved_fields_preserve_values_and_scope(self):
        validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/VerificationFieldView'}, registry=REGISTRY,
            format_checker=FormatChecker())
        rows = list(RecordField.objects.order_by('id'))
        self.assertTrue(rows)
        for field in rows:
            state = FieldVerificationState.objects.filter(field_id=field.id).order_by('purpose').first()
            value = field_view(field, state)
            self.assertTrue(validator.is_valid(value), list(validator.iter_errors(value)))
            self.assertEqual(value['field_id'], str(field.id))
            self.assertEqual(value['raw_text'], field.raw_text)
            self.assertEqual(value['raw_numeric'], format(field.raw_numeric, 'f') if field.raw_numeric is not None else None)
            self.assertEqual(value['value_state'], field.value_state)
            for key in ('original_name', 'value_type', 'categorical_value', 'qualifier', 'method', 'reference_interval', 'report_flag'):
                self.assertEqual(value[key], getattr(field, key))
            self.assertEqual(value['state_version'], str(state.version) if state is not None else None)
            self.assertEqual(value['status'], state.status if state is not None else 'unverified')
            self.counts['field_projections'] += 1

    def test_live_workflow_rejects_anonymous_access_before_effects(self):
        version = RecordVersion.objects.order_by('id').first()
        task = Task.objects.order_by('id').first()
        self.assertIsNotNone(version)
        self.assertIsNotNone(task)
        targets = [
            ('GET', reverse('verification_v2_creation_scope', kwargs={'record_version_id': version.id}) + '?purpose=clinical_evaluation'),
            ('GET', reverse('verification_v2_originals', kwargs={'resource_kind': 'verification', 'resource_id': task.id})),
            ('GET', reverse('verification_v2_original_content', kwargs={'resource_kind': 'verification', 'resource_id': task.id, 'file_id': version.id})),
            ('POST', reverse('verification_v2_request_create')),
            ('POST', reverse('verification_v2_submit')),
            ('POST', reverse('verification_v2_revoke', kwargs={'verification_id': task.id})),
            ('GET', reverse('verification_v2_request_read', kwargs={'verification_request_id': task.id})),
            ('GET', reverse('verification_v2_read', kwargs={'verification_id': task.id})),
            ('GET', reverse('verification_v2_options', kwargs={'verification_request_id': task.id})),
            ('POST', '/api/v1/support/verification-requests'), ('POST', '/api/v1/support/verifications'),
            ('POST', f'/api/v1/support/verifications/{task.id}/revoke'),
            ('GET', f'/api/v1/support/verification-requests/{task.id}'), ('GET', f'/api/v1/support/verifications/{task.id}'),
            ('POST', '/api/v2/write-intents'), ('GET', f'/api/v2/requests/{task.id}'),
            ('POST', f'/api/v2/write-intents/{task.id}/retry')]
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10) as client:
            for method, path in targets:
                response = client.request(method, path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                self.assertIn('no-store', response.headers['cache-control'])
                self.assertNotIn('fields', response.json())
                self.counts['anonymous_http_requests'] += 1

    def test_actual_source_query_identity_and_repeated_parameters(self):
        authorization = DataUseAuthorization.objects.order_by('id').first()
        original = FileObject.objects.filter(purpose='original', status='available').order_by('id').first()
        self.assertIsNotNone(authorization)
        self.assertIsNotNone(original)
        purpose = QueryDict('', mutable=True)
        purpose['purpose'] = authorization.purpose
        self.assertEqual(verification_query('VerificationCreationQuery', purpose), {'purpose': authorization.purpose})
        purpose.appendlist('purpose', authorization.purpose)
        with self.assertRaises(VerificationRejected) as rejected:
            verification_query('VerificationCreationQuery', purpose)
        self.assertEqual(rejected.exception.reason_code, 'VERIFICATION_QUERY_INVALID')
        source = QueryDict('', mutable=True)
        source['checksum'], source['object_version'] = original.checksum, original.object_version
        self.assertEqual(verification_query('VerificationOriginalQuery', source),
            {'checksum': original.checksum, 'object_version': original.object_version})
        source.appendlist('checksum', original.checksum)
        with self.assertRaises(VerificationRejected):
            verification_query('VerificationOriginalQuery', source)
        self.counts['actual_source_queries'] += 4


if __name__ == '__main__':
    unittest.main(verbosity=2)
