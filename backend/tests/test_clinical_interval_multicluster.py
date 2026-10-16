"""Native, committed synthetic two-patient CI persistence and reread.

The independent dataset-freeze/source-approval boundary is explicit fixture
evidence. Actual replay eligibility, interval qualification/calculation, signed
TaskAttempt persistence and saved-report verification are never substituted.
Run only with the coordinated isolated PostgreSQL test runner.
"""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.db import connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from alerting.models import AlertEvent, AlertDecision
from archives.models import EvaluationRun, EvaluationPatientPartition, EvaluationLabel
from case_details.clinical_adoption import (
    adopt_clinical_capture, checked_saved_clinical, clinical_admission_binding,
)
from case_details.clinical_interval_adoption import (
    calculate_interval_adoption, verify_saved_interval_adoption,
)
from case_details.clinical_metrics_v2 import ClinicalCaptureV2
from case_details.evaluation_datasets import dataset_checksum
from case_details.evaluation_units import EvaluationUnitsRejected
from configurations.service import compute_checksum
from notifications.models import NotificationAttempt
from tasks.models import Task, TaskAttempt
from tasks.service import input_digest, create_attempt, finish_attempt
from tests.isolated_rows import IsolatedSchemaRows
from tests.synthetic_interval_multicluster import qualified_multicluster_capture
from tests.test_clinical_interval_adoption import synthetic_interval_evidence


class ClinicalIntervalMulticlusterTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        rows = IsolatedSchemaRows(require_empty=True)
        network = patch('urllib.request.urlopen', side_effect=AssertionError('External calls forbidden'))
        network.start()
        self.addCleanup(network.stop)
        try:
            with transaction.atomic():
                self.flow = qualified_multicluster_capture(self, synthetic_interval_evidence)
        finally:
            # Django flush does not discover schema-qualified application tables.
            # LIFO cleanup removes only this fixture's exact primary keys before
            # its temporary evidence roots/settings disappear, retaining FKs.
            self.addCleanup(rows.cleanup)

    def adopt(self):
        f = self.flow
        return adopt_clinical_capture(row=f.row, sources=f.sources,
            snapshot=f.snapshot, capture_ref=f.capture_ref)

    def test_real_two_patient_nonnull_interval_is_saved_and_read_without_resampling(self):
        f = self.flow
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(dataset_checksum(f.row), f.row.checksum)
        self.assertEqual(EvaluationPatientPartition.objects.filter(dataset_version_id=f.row.id).count(), 2)
        self.assertEqual(EvaluationLabel.objects.count(), 2)
        self.assertEqual(Task.objects.filter(id__in=[task.id for task in f.tasks], status='completed').count(), 4)
        with transaction.atomic():
            admitted = self.adopt()
            self.assertEqual(admitted['report']['confidence_intervals']['status'], 'PENDING')
            snapshot = {**f.snapshot,
                'input': {**f.snapshot['input'], 'clinical_capture_ref': f.capture_ref},
                'clinical_capture_binding': clinical_admission_binding(admitted)}
            now = timezone.now()
            run = EvaluationRun.objects.create(dataset_version_id=f.row.id, operator_id=f.base.actor.id,
                implementation_sha256=f.capture['implementation_sha256'], rules_refs=f.snapshot['configurations'],
                input_refs=[snapshot], result_refs=[], denominators=f.snapshot['denominators'], limitations=[],
                started_at=now, status='running', mode='replay')
            task_inputs = {'evaluation_run_id': str(run.id), 'snapshot': snapshot}
            task = Task.objects.create(actor=f.base.actor, task_type='assessment', schema_version='evaluation-run-1',
                input_refs=task_inputs, input_sha256=input_digest(task_inputs), status='running', status_version=1,
                lease_generation=1, lease_owner='synthetic-ci-multicluster', lease_until=now + timedelta(minutes=1),
                created_at=now, deadline_at=now + timedelta(minutes=1))
            attempt = create_attempt(task, task.lease_generation)
        # This is a real unlocked phase, with the real RNG and signing code.
        self.assertFalse(connection.in_atomic_block)
        saved = calculate_interval_adoption(admitted, task.input_sha256)
        section = saved['report']['confidence_intervals']
        result = section['results'][0]
        self.assertEqual((section['status'], section['required_complete']), ('COMPLETE', True))
        self.assertEqual((result['status'], result['clusters'], result['contributing_clusters']), ('CALCULATED', 2, 2))
        self.assertEqual((result['draws_valid'], result['draws_invalid']), (200, 0))
        self.assertIsNotNone(result['lower'])
        self.assertIsNotNone(result['upper'])
        self.assertLess(result['lower'], result['upper'])
        # Independent arithmetic: each real patient contributes one prompt.
        # The declared PCG64 seed 17 / B=200 gives AA=54, AB=47,
        # BA=47, BB=52. Both tails exceed 25%, so linear q=.25/.75
        # are exactly the extreme per-patient rates, in either ID order.
        periods = ClinicalCaptureV2.model_validate(f.capture).periods
        hours = [(period.end - period.start).total_seconds() / 3600 for period in periods]
        self.assertAlmostEqual(result['numerator'], 2)
        self.assertAlmostEqual(result['denominator'], sum(hours))
        self.assertAlmostEqual(result['estimate'], 2 / sum(hours))
        self.assertAlmostEqual(result['lower'], min(1 / value for value in hours))
        self.assertAlmostEqual(result['upper'], max(1 / value for value in hours))
        self.assertFalse(saved['report']['clinical_validation'])
        self.assertNotIn('_interval_inputs', saved)
        with transaction.atomic():
            current = self.adopt()
            self.assertEqual(clinical_admission_binding(current), snapshot['clinical_capture_binding'])
            self.assertEqual(verify_saved_interval_adoption(current, saved, task.input_sha256), saved['report'])
            run.status = 'completed'; run.finished_at = timezone.now()
            run.save(update_fields=['status', 'finished_at'])
            task.status = 'completed'; task.status_version += 1
            task.save(update_fields=['status', 'status_version'])
            finish_attempt(attempt, TaskAttempt.Status.COMPLETED, usage_evidence={'clinical_adoption': saved})
        run.refresh_from_db(); task.refresh_from_db(); attempt.refresh_from_db()
        self.assertEqual(attempt.usage_evidence['clinical_adoption']['report']['confidence_intervals'], section)
        with patch('case_details.clinical_intervals.np.random.Generator',
                side_effect=AssertionError('A saved read must never bootstrap')):
            self.assertEqual(checked_saved_clinical(run, task, f.row, f.sources), saved['report'])
            self.assertEqual(checked_saved_clinical(run, task, f.row, f.sources), saved['report'])
            # Even a rewritten plain hash cannot replace the signed result.
            tampered = deepcopy(saved)
            tampered['report']['confidence_intervals']['results'][0]['lower'] += 0.01
            tampered['report_sha256'] = compute_checksum(tampered['report'])
            attempt.usage_evidence = {'clinical_adoption': tampered}
            attempt.save(update_fields=['usage_evidence'])
            with self.assertRaisesRegex(EvaluationUnitsRejected, 'INTERVAL_SAVED_INTEGRITY_INVALID'):
                checked_saved_clinical(run, task, f.row, f.sources)
            attempt.usage_evidence = {'clinical_adoption': saved}
            attempt.save(update_fields=['usage_evidence'])
            f.interval.revision.status = 'revoked'
            f.interval.revision.save(update_fields=['status'])
            with self.assertRaises(EvaluationUnitsRejected):
                checked_saved_clinical(run, task, f.row, f.sources)
        self.assertFalse(AlertEvent.objects.exists())
        self.assertFalse(AlertDecision.objects.exists())
        self.assertFalse(NotificationAttempt.objects.exists())
