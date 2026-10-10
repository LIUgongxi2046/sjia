import json
import os
import unittest
from hashlib import sha256
from urllib.parse import urlencode

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django
import httpx

django.setup()

from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import PROTOCOL, REGISTRY
from archives.models import ArchiveFactVersion
from assessment.options import configuration_view, directory_record_view, selection_candidate_view
from configurations.models import ConfigVersion
from identity.models import Patient
from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion
from records.service import medical_time_view
from tasks.models import Task
from tests.verify_v2_migration import ROOT, require, snapshot
from sources.models import DataUseAuthorization


def validator(name):
    return Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, registry=REGISTRY, format_checker=FormatChecker())


class AssessmentCreationRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atomic = transaction.atomic()
        cls.atomic.__enter__()
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cls.before = snapshot(connection.connection)
        cls.counts = {'record_directory_projections': 0, 'configuration_projections': 0,
            'medical_time_projections': 0, 'anonymous_http_requests': 0, 'selection_candidate_projections': 0}
        cls.sources = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/assessment/options.py', 'backend/assessment/service.py', 'backend/api/views_assessments.py',
            'backend/assessment/clinical_selection.py', 'backend/assessment/static_inputs.py',
            'backend/api/urls_v2.py', 'backend/requests/assessments.py', 'backend/verification/workflow.py',
            'backend-design/v2-protocol.schema.json', 'backend-design/v2-api-catalog.csv',
            'backend/tests/verify_assessment_creation_runtime.py')}
        cls.passed = True

    def tearDown(self):
        if self._outcome.result.failures or self._outcome.result.errors:
            type(self).passed = False

    @classmethod
    def tearDownClass(cls):
        require(snapshot(connection.connection) == cls.before, '评估创建检查改变已保存资料。')
        require(all(sha256((ROOT / path).read_bytes()).hexdigest() == value for path, value in cls.sources.items()),
            '评估创建检查期间代码发生变化。')
        report = {'checked_at': timezone.now().isoformat(), 'status': 'PASS' if cls.passed else 'FAIL',
            'scope': 'ACTUAL_DIRECTORY_CONFIGURATION_TIME_PROJECTIONS_AND_ANONYMOUS_HTTP', 'counts': cls.counts,
            'preserved_tables': len(cls.before), 'business_writes': 0, 'source_sha256': cls.sources,
            'positive_creation': 'NOT_RUN', 'clinical_acceptance': 'NOT_RUN', 'visual': 'NOT_RUN'}
        (ROOT / '.planning-work/t49-creation-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        cls.atomic.__exit__(None, None, None)
        print(json.dumps({key: value for key, value in report.items() if key != 'source_sha256'}, ensure_ascii=False))

    def test_actual_record_directory_preserves_version_source_and_purpose(self):
        schema = validator('ClinicalDirectoryRecord')
        versions = list(RecordVersion.objects.order_by('id'))
        self.assertTrue(versions)
        for version in versions:
            record = Record.objects.get(id=version.record_id, patient=version.patient)
            authorization = DataUseAuthorization.objects.get(id=version.use_authorization_id, patient=version.patient)
            value = directory_record_view(record, version, authorization.purpose)
            self.assertTrue(schema.is_valid(value), list(schema.iter_errors(value)))
            self.assertEqual(value['record_id'], str(record.id))
            self.assertEqual(value['record_version_id'], str(version.id))
            self.assertEqual(value['version'], str(version.version))
            self.assertEqual(value['source_organization'], version.source_organization)
            self.assertEqual(value['purpose'], authorization.purpose)
            self.assertEqual(value['recorded_at'], version.recorded_at.isoformat())
            self.counts['record_directory_projections'] += 1

    def test_actual_configuration_identity_keeps_exact_version_checksum(self):
        schema = validator('AssessmentConfigurationOption')
        rows = list(ConfigVersion.objects.order_by('id'))
        self.assertTrue(rows)
        for row in rows:
            value = configuration_view(row)
            self.assertTrue(schema.is_valid(value), list(schema.iter_errors(value)))
            self.assertEqual(value, {'config_id': str(row.id), 'config_key': row.config_key,
                'version': str(row.version), 'checksum': row.checksum})
            self.counts['configuration_projections'] += 1

    def test_actual_medical_time_projection_keeps_precision_and_instant(self):
        schema = validator('AssessmentObservationTime')
        rows = list(MedicalTime.objects.order_by('id'))
        self.assertTrue(rows)
        for row in rows:
            value = medical_time_view(row.id, row.patient)
            self.assertTrue(schema.is_valid(value), list(schema.iter_errors(value)))
            self.assertEqual(value['precision'], row.precision)
            self.assertEqual(value['instant'], row.instant.isoformat() if row.instant is not None else None)
            self.assertEqual(value['raw_text'], row.raw_text)
            self.counts['medical_time_projections'] += 1

    def test_actual_selection_candidate_keeps_original_value_identity_and_time(self):
        schema = validator('AssessmentSelectionCandidate')
        facts = list(ArchiveFactVersion.objects.order_by('id'))
        self.assertTrue(facts)
        for fact in facts:
            patient = Patient.objects.get(id=fact.patient_id)
            field = RecordField.objects.get(id=fact.field_id, patient=patient)
            value = selection_candidate_view(fact, field, patient)
            self.assertTrue(schema.is_valid(value), list(schema.iter_errors(value)))
            self.assertEqual(value['fact_version_id'], str(fact.id))
            self.assertEqual(value['field_id'], str(field.id))
            self.assertEqual(value['record_version_id'], str(field.record_version_id))
            self.assertEqual(value['fact_version'], str(fact.version))
            self.assertEqual(value['context_type'], fact.context_type)
            self.assertEqual(value['raw_unit'], field.raw_unit)
            self.assertEqual(value['qualifier'], field.qualifier)
            self.assertEqual(value['available_at'], fact.available_at.isoformat() if fact.available_at else None)
            self.assertEqual(value['raw_value'], field.raw_text if field.raw_text is not None
                else format(field.raw_numeric, 'f') if field.raw_numeric is not None else field.categorical_value)
            if fact.medical_time_id:
                time = MedicalTime.objects.get(id=fact.medical_time_id, patient=patient)
                self.assertEqual(value['medical_time']['precision'], time.precision)
            self.counts['selection_candidate_projections'] += 1

    def test_current_creation_and_affected_task_reads_require_authenticated_identity(self):
        version = RecordVersion.objects.order_by('id').first()
        task = Task.objects.order_by('id').first()
        self.assertIsNotNone(version)
        self.assertIsNotNone(task)
        targets = [('GET', reverse('clinical_directory_read') + '?action=verify_field'),
            ('GET', reverse('clinical_directory_read') + '?action=execute_assessment'),
            ('GET', reverse('assessment_creation_options', kwargs={'patient_id': version.patient_id})
                + '?' + urlencode({'mode': 'current', 'known_at': version.recorded_at.isoformat()})),
            ('POST', reverse('assessment_v2_create', kwargs={'patient_id': version.patient_id})),
            ('GET', reverse('assessment_task_read', kwargs={'task_id': task.id})),
            ('GET', reverse('assessment_v2_read', kwargs={'assessment_id': task.id}))]
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10) as client:
            for method, path in targets:
                response = client.request(method, path)
                self.assertEqual(response.status_code, 401, path)
                self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                self.assertIn('no-store', response.headers['cache-control'])
                self.assertNotIn('patients', response.json())
                self.assertNotIn('plans', response.json())
                self.counts['anonymous_http_requests'] += 1


if __name__ == '__main__':
    unittest.main(verbosity=2)
