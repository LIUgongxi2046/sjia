"""No-DB worker orchestration tests with explicit mocked admission boundary.

Real bounded CI/signing code and final state logic; not evidence that a fake
capture is qualified or that the full dataset HTTP flow was executed.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from django.utils import timezone

from case_details.clinical_adoption import clinical_admission_binding
from case_details.clinical_interval_adoption import calculate_interval_adoption
from case_details.clinical_intervals import add_interval_report, evaluate_intervals
from case_details.clinical_metrics import evaluate
from case_details.evaluation_execution import execute_evaluation_run
from case_details.evaluation_units import EvaluationUnitsRejected
from configurations.service import compute_checksum
from tests.test_clinical_intervals import interval_fixture


class IntervalExecutionPhaseTests(SimpleTestCase):
    def setUp(self):
        plan,capture,protocol=interval_fixture()
        report=add_interval_report(evaluate(plan,capture),evaluate_intervals(plan,capture,protocol,compute=False))
        report['execution_steps']=[]
        proofs={'plan':{},'capture':{},'matching':{},'confidence_interval':{
            'body_sha256':report['confidence_intervals']['protocol_sha256']}}
        self.admitted={'report':report,'proofs':proofs,'report_sha256':compute_checksum(report),
            'proofs_sha256':compute_checksum(proofs),'_interval_inputs':{'plan':plan,'capture':capture,'protocol':protocol}}
        self.snapshot={'dataset_id':'synthetic-dataset','input':{'clinical_capture_ref':'synthetic:capture'},
            'denominators':{'unit_ids':{}},'clinical_capture_binding':clinical_admission_binding(self.admitted)}
        self.run=SimpleNamespace(id='synthetic-run',status='pending',save=MagicMock(),result_refs=[],limitations=[],
            denominators={},finished_at=None)
        self.context={'auth_epoch':'1'}
        self.task=SimpleNamespace(id='synthetic-task',status='running',status_version=1,deadline_at=timezone.now()+timedelta(minutes=5),
            lease_owner='worker',lease_generation=1,input_sha256='a'*64,actor=SimpleNamespace(id='synthetic-actor'),
            input_refs={'authorization_context':self.context},request_row=SimpleNamespace(request_id='request'),request_row_id='request',
            save=MagicMock(),refresh_from_db=MagicMock())
        self.depth=0
        @contextmanager
        def atomic():
            self.depth+=1
            try: yield
            finally: self.depth-=1
        names={'transaction.atomic':atomic,'checked_event':lambda event:(self.run,self.task),
            'ConsumerReceipt.objects':MagicMock(),'Task.objects':MagicMock(),'acquire_lease':lambda *a:True,
            'create_attempt':lambda *a:SimpleNamespace(lease_generation=1),'run_snapshot':lambda run:self.snapshot,
            'inventory_results':lambda *a:[], 'record_receipt':MagicMock(), 'write_audit':MagicMock(),
            'WriteIntent.objects':MagicMock()}
        names['ConsumerReceipt.objects'].filter.return_value.exists.return_value=False
        for name,value in names.items():
            mock=patch('case_details.evaluation_execution.'+name,value);mock.start();self.addCleanup(mock.stop)
        self.locked=patch('case_details.evaluation_execution.lock_run_inputs');self.lock=self.locked.start();self.addCleanup(self.locked.stop)
        self.lock.return_value=(SimpleNamespace(),SimpleNamespace(manifest=None,protocol=SimpleNamespace(required_units=[])),
            None,None,self.snapshot,self.context)
        self.finish_patch=patch('case_details.evaluation_execution.finish_attempt');self.finish=self.finish_patch.start();self.addCleanup(self.finish_patch.stop)
        self.adopt_patch=patch('case_details.clinical_adoption.adopt_clinical_capture',return_value=self.admitted)
        self.adopt=self.adopt_patch.start();self.addCleanup(self.adopt_patch.stop)

    def test_actual_candidate_calculation_unlocked_then_requalified_before_save(self):
        def compute(adoption,input_sha256):
            self.assertEqual(self.depth,0)
            return calculate_interval_adoption(adoption,input_sha256)
        with patch('case_details.clinical_interval_adoption.calculate_interval_adoption',side_effect=compute):
            execute_evaluation_run(SimpleNamespace(),'worker')
        self.assertEqual(self.lock.call_count,2)
        self.assertEqual(self.adopt.call_count,2)
        self.assertEqual(self.run.status,'completed')
        saved=self.finish.call_args.kwargs['usage_evidence']['clinical_adoption']
        self.assertEqual(saved['report']['confidence_intervals']['status'],'COMPLETE')
        self.assertIsNotNone(saved['report']['confidence_intervals']['results'][0]['lower'])
        self.assertFalse(self.finish.call_args.kwargs['usage_evidence']['family_notifications'])

    def test_revocation_between_computation_and_save_blocks_result(self):
        self.lock.side_effect=[self.lock.return_value,EvaluationUnitsRejected('REVOKED_DURING_CALCULATION')]
        with self.assertRaisesRegex(EvaluationUnitsRejected,'REVOKED_DURING_CALCULATION'):
            execute_evaluation_run(SimpleNamespace(),'worker')
        self.finish.assert_not_called()
        self.assertEqual(self.run.status,'running')

    def test_changed_proof_between_phases_blocks_save(self):
        changed=deepcopy(self.admitted);changed['proofs']['confidence_interval']['body_sha256']='b'*64
        self.adopt.side_effect=[self.admitted,changed]
        with self.assertRaisesRegex(EvaluationUnitsRejected,'CLINICAL_CAPTURE_BINDING_CHANGED'):
            execute_evaluation_run(SimpleNamespace(),'worker')
        self.finish.assert_not_called()

    def test_required_interval_failure_keeps_run_insufficient_but_task_completed(self):
        self.admitted['_interval_inputs']['protocol']['n_resamples']=100000000
        # Admission is recomputed for this explicit resource-limited protocol.
        inputs=self.admitted['_interval_inputs']
        section=evaluate_intervals(inputs['plan'],inputs['capture'],inputs['protocol'],compute=False)
        self.admitted['report']['confidence_intervals']=section
        self.admitted['proofs']['confidence_interval']['body_sha256']=section['protocol_sha256']
        self.admitted['report_sha256']=compute_checksum(self.admitted['report'])
        self.admitted['proofs_sha256']=compute_checksum(self.admitted['proofs'])
        self.snapshot['clinical_capture_binding']=clinical_admission_binding(self.admitted)
        execute_evaluation_run(SimpleNamespace(),'worker')
        self.assertEqual(self.run.status,'insufficient')
        self.assertEqual(self.task.status,'completed')

    def test_missing_configuration_keeps_new_run_statistically_incomplete(self):
        inputs=self.admitted['_interval_inputs']
        inputs['plan']['confidence_interval_protocol_ref']=None
        inputs['protocol']=None
        self.admitted['report']['confidence_interval_protocol_ref']=None
        self.admitted['report']['confidence_intervals']=evaluate_intervals(inputs['plan'],inputs['capture'],None,compute=False)
        self.admitted['proofs']['confidence_interval']=None
        self.admitted['report_sha256']=compute_checksum(self.admitted['report'])
        self.admitted['proofs_sha256']=compute_checksum(self.admitted['proofs'])
        self.snapshot['clinical_capture_binding']=clinical_admission_binding(self.admitted)
        execute_evaluation_run(SimpleNamespace(),'worker')
        self.assertEqual((self.run.status,self.task.status),('insufficient','completed'))
        saved=self.finish.call_args.kwargs['usage_evidence']['clinical_adoption']
        self.assertEqual(saved['report']['confidence_intervals']['status'],'NOT_CONFIGURED')
        self.assertEqual(saved['report']['confidence_intervals']['results'],[])
