"""No-DB integrity tests. Signatures never stand in for current qualification."""
from copy import deepcopy
from unittest.mock import patch
from django.test import SimpleTestCase
from case_details.clinical_intervals import evaluate_intervals, add_interval_report
from case_details.clinical_metrics import evaluate
from case_details.clinical_adoption import clinical_admission_binding
from case_details.clinical_interval_adoption import calculate_interval_adoption, verify_saved_interval_adoption
from case_details.evaluation_units import EvaluationUnitsRejected
from configurations.service import compute_checksum
from tests.test_clinical_intervals import interval_fixture


class IntervalIntegrityTests(SimpleTestCase):
    def setUp(self):
        plan,capture,protocol=interval_fixture()
        report=add_interval_report(evaluate(plan,capture),evaluate_intervals(plan,capture,protocol,compute=False))
        proofs={'plan':{'reference':plan['plan_ref']},'capture':{'reference':'synthetic:capture'},
            'matching':{'reference':'synthetic:pairings'},
            'confidence_interval':{'body_sha256':report['confidence_intervals']['protocol_sha256']}}
        self.current={'report':report,'proofs':proofs,'report_sha256':compute_checksum(report),
            'proofs_sha256':compute_checksum(proofs),'_interval_inputs':{'plan':plan,'capture':capture,'protocol':protocol}}

    def test_saved_calculation_signed_reproducible_and_reads_do_not_resample(self):
        saved=calculate_interval_adoption(self.current,'a'*64)
        self.assertNotIn('_interval_inputs',saved)
        self.assertEqual(saved['report']['confidence_intervals']['status'],'COMPLETE')
        with patch('case_details.clinical_interval_adoption.evaluate_intervals',side_effect=AssertionError('read cannot compute')):
            self.assertEqual(verify_saved_interval_adoption(self.current,saved,'a'*64),saved['report'])
        self.assertEqual(calculate_interval_adoption(self.current,'a'*64),saved)

    def test_mutation_forged_hash_cross_task_or_changed_admission_refused(self):
        saved=calculate_interval_adoption(self.current,'a'*64)
        for mutate in ('endpoint','signature','proof','input','current-proof','current-body'):
            current,changed=deepcopy(self.current),deepcopy(saved)
            task='a'*64
            if mutate=='endpoint':
                changed['report']['confidence_intervals']['results'][0]['lower']=.123
                changed['report_sha256']=compute_checksum(changed['report'])
            elif mutate=='signature': changed['interval_integrity']='invalid'
            elif mutate=='proof': changed['proofs']['confidence_interval']['body_sha256']='f'*64
            elif mutate=='input': task='b'*64
            elif mutate=='current-proof': current['proofs']['confidence_interval']['body_sha256']='e'*64
            else: current['report']['confidence_intervals']['protocol']['seed']+=1
            if mutate.startswith('current'):
                current['report_sha256']=compute_checksum(current['report'])
                current['proofs_sha256']=compute_checksum(current['proofs'])
            with self.subTest(mutate=mutate),self.assertRaises(EvaluationUnitsRejected):
                verify_saved_interval_adoption(current,changed,task)

    def test_no_bootstrap_allowed_in_database_transaction(self):
        from types import SimpleNamespace
        with patch('case_details.clinical_interval_adoption.connection',SimpleNamespace(in_atomic_block=True)),self.assertRaisesRegex(
                EvaluationUnitsRejected,'UNLOCKED_PHASE'):
            calculate_interval_adoption(self.current,'a'*64)

    def test_protocol_proof_is_part_of_admission(self):
        self.assertIn('confidence_interval',clinical_admission_binding(self.current))
        self.assertNotIn('_interval_inputs',clinical_admission_binding(self.current))


    def test_structurally_corrupt_saved_result_is_rejected_without_keyerror(self):
        saved=calculate_interval_adoption(self.current,'a'*64)
        for field in ('report','proofs','interval_integrity'):
            for replacement in ('missing',None,42,[]):
                changed=deepcopy(saved)
                if replacement=='missing': del changed[field]
                else: changed[field]=replacement
                with self.subTest(field=field,replacement=replacement),self.assertRaises(EvaluationUnitsRejected):
                    verify_saved_interval_adoption(self.current,changed,'a'*64)
        for field in ('confidence_intervals',):
            changed=deepcopy(saved);del changed['report'][field]
            with self.assertRaises(EvaluationUnitsRejected):
                verify_saved_interval_adoption(self.current,changed,'a'*64)
