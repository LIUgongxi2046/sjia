import json
import os
import unittest
import httpx
from hashlib import sha256
from datetime import timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from django.db import connection, transaction
from django.utils import timezone
from jsonschema import Draft202012Validator

from archives.models import ArchiveFactVersion
from assessment.medical_intervals import MedicalIntervalRejected, medical_interval
from assessment.provenance import entity_reference
from assessment.qualified_facts import QualifiedFact, fact_chronology_reasons
from assessment.selection import select_current, strictly_before
from configurations.models import ConfigVersion
from configurations.schemas import VALIDATORS
from configurations.service import SUPPORTED_SCHEMAS
from medical_time.models import MedicalTime
from identity.models import Patient
from tasks.models import Task
from django.urls import resolve, reverse
from records.models import RecordField, RecordVersion
from tests.verify_v2_migration import ROOT, require, snapshot


class StaticQualificationRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.atomic = transaction.atomic()
        cls.atomic.__enter__()
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cls.before = snapshot(connection.connection)
        cls.sources = {path: sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            'backend/assessment/policies.py', 'backend/assessment/medical_intervals.py', 'backend/assessment/qualified_facts.py',
            'backend/assessment/provenance.py', 'backend/assessment/selection.py', 'backend/assessment/static_computation.py',
            'backend/assessment/static_inputs.py', 'backend/configurations/service.py',
            'backend/assessment/service.py', 'backend/assessment/configuration_proofs.py', 'backend/tasks/lease.py',
            'backend/tasks/worker.py', 'backend/tasks/dispatcher.py', 'backend/requests/assessments.py',
            'backend/api/views_assessments.py', 'backend/api/urls_v2.py', 'backend/api/views_v2_requests.py', 'backend/api/views_support2.py',
            'backend-design/v2-protocol.schema.json', 'backend-design/v2-api-catalog.csv',
            'backend/configurations/schemas.py', 'backend/api/base.py', 'backend/tests/verify_static_qualification_runtime.py')}
        cls.counts = {'fact_versions': 0, 'historical_cutoffs': 0, 'medical_times': 0, 'available_date_intervals': 0,
            'available_precise_intervals': 0,
            'unavailable_intervals': 0, 'policy_formats': 0, 'content_references': 0,
            'unqualified_candidates_excluded': 0, 'date_intervals_not_independently_ordered': 0, 'anonymous_http_requests': 0}
        cls.passed = True

    def tearDown(self):
        result = self._outcome.result
        if result.failures or result.errors:
            type(self).passed = False

    @classmethod
    def tearDownClass(cls):
        require(snapshot(connection.connection) == cls.before, '专业资格检查改变已有业务资料。')
        require(all(sha256((ROOT / path).read_bytes()).hexdigest() == digest for path, digest in cls.sources.items()),
            '专业资格检查期间代码发生变化。')
        report = {'checked_at': timezone.now().isoformat(), 'status': 'PASS' if cls.passed else 'FAIL',
            'scope': 'ACTUAL_SAVED_FACT_CHRONOLOGY_AND_MEDICAL_TIME_READ_ONLY', 'counts': cls.counts,
            'preserved_tables': len(cls.before), 'business_writes': 0,
            'source_sha256': cls.sources,
            'qualified_input_computation': 'NOT_RUN', 'clinical_acceptance': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
        (ROOT / '.planning-work/t49-qualification-runtime.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        cls.atomic.__exit__(None, None, None)
        print(json.dumps(report, ensure_ascii=False))

    def test_existing_unknown_facts_preserve_insufficiency(self):
        versions = {value.id: value for value in RecordVersion.objects.all()}
        rows = list(ArchiveFactVersion.objects.order_by('id'))
        self.assertTrue(rows)
        unknown = 0
        for fact in rows:
            with self.subTest(fact_version_id=str(fact.id)):
                reasons = fact_chronology_reasons(fact, versions[fact.record_version_id], known_at=timezone.now(),
                    accepted_context_types=('current', 'actual'))
                if fact.availability_status == 'unknown':
                    self.assertIn('FACT_AVAILABILITY_UNKNOWN', reasons)
                    unknown += 1
                if fact.context_type == 'unknown':
                    self.assertIn('FACT_CONTEXT_INELIGIBLE', reasons)
                if fact.assertion_state == 'unknown':
                    self.assertIn('FACT_ASSERTION_UNKNOWN', reasons)
                type(self).counts['fact_versions'] += 1
        self.assertGreater(unknown, 0)

    def test_actual_recording_events_cannot_enter_earlier_contexts(self):
        versions = {value.id: value for value in RecordVersion.objects.all()}
        for fact in ArchiveFactVersion.objects.order_by('id'):
            version = versions[fact.record_version_id]
            cutoff = min(fact.created_at, version.recorded_at) - timedelta(microseconds=1)
            with self.subTest(fact_version_id=str(fact.id)):
                reasons = fact_chronology_reasons(fact, version, known_at=cutoff, accepted_context_types=('current', 'actual'))
                self.assertIn('FACT_NOT_RECORDED_BY_CUTOFF', reasons)
                type(self).counts['historical_cutoffs'] += 1
        self.assertGreater(type(self).counts['historical_cutoffs'], 0)

    def test_actual_unknown_medical_times_do_not_gain_precise_instants(self):
        rows = list(MedicalTime.objects.order_by('id'))
        self.assertTrue(rows)
        for value in rows:
            with self.subTest(medical_time_id=str(value.id)):
                insufficient = (value.precision not in ('date', 'minute', 'second')
                    or value.resolution_status in ('unknown', 'ambiguous', 'invalid')
                    or value.timezone_name is None and value.utc_offset_minutes is None
                    or value.precision != 'date' and (value.resolution_status != 'resolved' or value.instant is None))
                if insufficient:
                    with self.assertRaises(MedicalIntervalRejected):
                        medical_interval(value)
                    type(self).counts['unavailable_intervals'] += 1
                else:
                    interval = medical_interval(value)
                    self.assertLessEqual(interval.lower, interval.upper)
                    if value.instant is not None:
                        self.assertLessEqual(interval.lower, value.instant)
                        self.assertLessEqual(value.instant, interval.upper)
                    key = 'available_date_intervals' if value.precision == 'date' else 'available_precise_intervals'
                    type(self).counts[key] += 1
                type(self).counts['medical_times'] += 1
        self.assertGreater(type(self).counts['unavailable_intervals'], 0)

    def test_policy_formats_are_registered_without_changing_configurations(self):
        for key in (('rule', 'assessment-rule-2'), ('rule', 'assessment-context-policy-2'),
                ('verification', 'qualification-policy-2'), ('mapping', 'unit-mapping-2')):
            with self.subTest(policy=key):
                validator = VALIDATORS[key]
                self.assertIn(key[1], SUPPORTED_SCHEMAS[key[0]])
                Draft202012Validator.check_schema(validator.schema)
                for row in ConfigVersion.objects.exclude(schema_version=key[1]):
                    self.assertFalse(validator.is_valid(row.payload))
                type(self).counts['policy_formats'] += 1

    def test_actual_object_references_have_distinct_stable_content_identity(self):
        identities = set()
        for model in (ArchiveFactVersion, RecordField, RecordVersion, MedicalTime):
            for row in model.objects.order_by('id'):
                with self.subTest(model=model._meta.label_lower, identity=str(row.id)):
                    first = entity_reference(row)
                    second = entity_reference(model.objects.get(id=row.id))
                    self.assertEqual(first, second)
                    self.assertEqual(set(first), {'model', 'id', 'sha256'})
                    self.assertRegex(first['sha256'], r'^[0-9a-f]{64}$')
                    self.assertNotIn(first['sha256'], identities)
                    identities.add(first['sha256'])
                    type(self).counts['content_references'] += 1
        self.assertGreater(type(self).counts['content_references'], 0)

    def test_actual_unknown_candidates_never_produce_selected_inputs(self):
        versions = {value.id: value for value in RecordVersion.objects.all()}
        values = []
        for fact in ArchiveFactVersion.objects.order_by('id'):
            reasons = fact_chronology_reasons(fact, versions[fact.record_version_id], known_at=timezone.now(),
                accepted_context_types=('current', 'actual'))
            self.assertTrue(reasons)
            values.append(QualifiedFact({'field_id': str(fact.field_id), 'fact_version_id': str(fact.id)}, None, None, tuple(reasons)))
        self.assertTrue(values)
        selection = select_current(values, (), None)
        self.assertIsNone(selection.selected)
        self.assertEqual(selection.reasons, ('NO_ELIGIBLE_FACT',))
        self.assertEqual(len(selection.exclusions), len(values))
        self.assertEqual({item['fact_version_id'] for item in selection.exclusions},
            {item.reference['fact_version_id'] for item in values})
        type(self).counts['unqualified_candidates_excluded'] = len(selection.exclusions)

    def test_actual_date_intervals_cannot_be_reused_as_independent_later_sampling(self):
        for row in MedicalTime.objects.filter(precision='date').order_by('id'):
            if (row.resolution_status in ('unknown', 'ambiguous', 'invalid')
                    or row.timezone_name is None and row.utc_offset_minutes is None):
                continue
            interval = medical_interval(row)
            self.assertFalse(strictly_before(interval, interval))
            type(self).counts['date_intervals_not_independently_ordered'] += 1
        self.assertGreater(type(self).counts['date_intervals_not_independently_ordered'], 0)

    def test_live_assessment_endpoints_reject_anonymous_access_before_business_effects(self):
        patient = Patient.objects.order_by('id').first()
        task = Task.objects.order_by('id').first()
        self.assertIsNotNone(patient)
        self.assertIsNotNone(task)
        create = reverse('assessment_v2_create', kwargs={'patient_id': patient.id})
        self.assertEqual(resolve(create).url_name, 'assessment_v2_create')
        paths = [('POST', create), ('GET', reverse('assessment_v2_read', kwargs={'assessment_id': task.id})),
            ('GET', reverse('assessment_task_read', kwargs={'task_id': task.id})),
            ('POST', f'/api/v1/support/patients/{patient.id}/assessments'),
            ('GET', f'/api/v1/support/assessments/{task.id}'),
            ('POST', '/api/v2/write-intents'), ('GET', f'/api/v2/requests/{task.id}'),
            ('POST', f'/api/v2/write-intents/{task.id}/retry')]
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=10, trust_env=False) as client:
            for method, path in paths:
                with self.subTest(method=method, path=path):
                    response = client.request(method, path)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()['code'], 'AUTH_REQUIRED')
                    self.assertIn('no-store', response.headers['cache-control'])
                    self.assertNotIn('ms_total', response.text)
                    self.assertNotIn('classification_result', response.text)
                    type(self).counts['anonymous_http_requests'] += 1


if __name__ == '__main__':
    unittest.main(verbosity=2)
