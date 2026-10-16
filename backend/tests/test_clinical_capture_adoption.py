"""Qualified saved-replay adoption, with explicit frozen-dataset boundary inputs.

Uses actual source qualification, static/dynamic workers and readback. Does not
claim the separate purpose-copy/freeze API path is covered by this fixture.
"""
from copy import deepcopy
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from case_details.clinical_adoption import adopt_clinical_capture, clinical_admission_binding
from case_details.evaluation_units import EvaluationUnitsRejected
from configurations.service import compute_checksum
from tests.synthetic_clinical_capture import qualified_replay_capture


class ClinicalCaptureAdoptionTest(TestCase):
    def setUp(self):
        network = patch('urllib.request.urlopen', side_effect=AssertionError('External calls forbidden'))
        network.start()
        self.addCleanup(network.stop)
        self.flow = qualified_replay_capture(self)

    def adopt(self, reference=None):
        f = self.flow
        return adopt_clinical_capture(row=f.row, sources=f.sources, snapshot=f.snapshot, capture_ref=reference or f.capture_ref)

    def capture_reference(self, changed):
        return self.flow.base.fixture.evidence(changed, purposes=['evaluation_capture', 'clinical_evaluation'])[1]

    def test_actual_static_and_dynamic_saved_replays_adopt_exact_metrics(self):
        result = self.adopt()
        report = result['report']
        self.assertEqual(report['status'], 'CALCULATED_FROM_VERIFIED_REPLAYS')
        self.assertEqual(report['evidence_qualification'], 'QUALIFIED_SAVED_REPLAY_BINDINGS')
        self.assertFalse(report['clinical_validation'])
        self.assertEqual(report['static']['sensitivity'], 1)
        self.assertEqual(report['dynamic']['sensitivity'], 1)
        self.assertEqual(report['dynamic']['prompt_counts'], {'total': 1, 'first': 1, 'repeat': 0})
        self.assertEqual(len(result['proofs']['executions']), 2)
        self.assertNotEqual(report['execution_steps'][0]['execution_input_sha256'], report['execution_steps'][1]['execution_input_sha256'])
        self.assertEqual(report['execution_steps'][0]['corpus_sha256'], report['execution_steps'][1]['corpus_sha256'])
        self.assertEqual(result['report_sha256'], compute_checksum(report))
        self.assertEqual(clinical_admission_binding(result)['capture']['reference'], self.flow.capture_ref)

    def test_requalified_tampered_capture_does_not_override_actual_task_results(self):
        mutations = [
            ('result_sha256', 'f' * 64, 'CLINICAL_REPLAY_RESULT_CHANGED'),
            ('execution_input_sha256', 'f' * 64, 'CLINICAL_REPLAY_EXECUTION_MISMATCH'),
            ('execution_ref', 'urn:sjia:assessment:00000000-0000-4000-8000-000000000000', 'CLINICAL_REPLAY_RESULT_CHANGED'),
            ('used_fact_refs', [], 'CLINICAL_REPLAY_USED_FACTS_MISMATCH'),
        ]
        for name, value, reason in mutations:
            changed = deepcopy(self.flow.capture)
            changed['periods'][0]['steps'][0]['static'][name] = value
            with self.subTest(name=name), self.assertRaisesRegex(EvaluationUnitsRejected, reason):
                self.adopt(self.capture_reference(changed))
        changed = deepcopy(self.flow.capture)
        changed['periods'][0]['steps'][0]['dynamic']['prompts'][0]['outcome'] = 'unknown'
        with self.assertRaises(EvaluationUnitsRejected):
            self.adopt(self.capture_reference(changed))

    def test_frozen_partition_and_configuration_changes_fail_closed(self):
        for key, value, reason in [('implementation_sha256', 'f' * 64, 'CLINICAL_FROZEN_BINDING_MISMATCH'),
                ('dataset_checksum', 'f' * 64, 'REPLAY_CORPUS_DIGEST_MISMATCH')]:
            changed = deepcopy(self.flow.capture)
            changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(EvaluationUnitsRejected, reason):
                self.adopt(self.capture_reference(changed))
        self.flow.snapshot['input']['partition'] = 'validation'
        with self.assertRaisesRegex(EvaluationUnitsRejected, 'CLINICAL_PARTITION_MISMATCH'):
            self.adopt()

    def test_revoked_source_and_verification_are_rechecked_on_read(self):
        self.adopt()
        source = self.flow.capture_source
        source.status = 'revoked'
        source.save(update_fields=['status'])
        with self.assertRaises(EvaluationUnitsRejected):
            self.adopt()
        source.status = 'approved'
        source.save(update_fields=['status'])
        verification = self.flow.base.verification
        verification.revoked_at = timezone.now()
        verification.save(update_fields=['revoked_at'])
        with self.assertRaisesRegex(EvaluationUnitsRejected, 'CLINICAL_CORPUS_PROOF_CHANGED'):
            self.adopt()

    def test_saved_task_cannot_change_predeclared_policy_role(self):
        task = self.flow.tasks['static']
        changed = deepcopy(task.input_refs)
        changed['payload']['window_policy_id'] = str(self.flow.base.configs['window'].id)
        task.input_refs = changed
        task.save(update_fields=['input_refs'])
        with self.assertRaisesRegex(EvaluationUnitsRejected, 'CLINICAL_REPLAY_POLICY_MISMATCH'):
            self.adopt()


class ClinicalApplicabilityAdoptionTest(TestCase):
    def test_completed_not_applicable_replay_cannot_enter_a_negative_denominator(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('External calls forbidden')):
            flow = qualified_replay_capture(self, applicable=False)
            self.assertEqual(flow.capture['periods'][0]['steps'][0]['static']['status'], 'completed')
            self.assertEqual(flow.capture['periods'][0]['steps'][0]['static']['prompts'], [])
            with self.assertRaisesRegex(EvaluationUnitsRejected, 'CLINICAL_APPLICABILITY_POLICY_UNSUPPORTED'):
                adopt_clinical_capture(row=flow.row, sources=flow.sources, snapshot=flow.snapshot, capture_ref=flow.capture_ref)


class ClinicalApplicabilityContractTest(SimpleTestCase):
    def test_unknown_insufficient_and_applicable_negative_remain_distinct(self):
        from types import SimpleNamespace
        from case_details.clinical_adoption import criterion_met
        plan = SimpleNamespace(static_criterion='classification_meets')
        self.assertFalse(criterion_met(plan, 'static', {'kind': 'classification', 'computation_status': 'insufficient',
            'applicability': 'insufficient', 'output': {'classification_result': 'insufficient'}}))
        self.assertFalse(criterion_met(plan, 'static', {'kind': 'classification', 'computation_status': 'completed',
            'applicability': 'applicable', 'output': {'classification_result': 'does_not_meet'}}))
        plan.static_criterion = 'ms_threshold_true'
        with self.assertRaisesRegex(EvaluationUnitsRejected, 'CLINICAL_APPLICABILITY_POLICY_UNSUPPORTED'):
            criterion_met(plan, 'static', {'kind': 'ms', 'computation_status': 'completed',
                'applicability': 'not_applicable', 'output': {'ms_threshold_result': None}})
