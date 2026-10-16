"""Native PostgreSQL synthetic tests; execute only in the coordinated test DB.

Actual saved static/dynamic replays and source qualification. The dataset freeze
boundary and the existing source-approval API audit/receipt are explicit fixture
inputs, not a claim to cover purpose-copy/freeze or source-approval HTTP routes.
The single-patient fixture intentionally cannot calculate a nondegenerate CI;
positive non-null native persistence is covered in test_clinical_interval_multicluster.
"""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from audit.service import write_audit
from authorization.models import ServiceRoleGrant
from authorization.policy import Denied
from archives.models import EvaluationRun, EvaluationDatasetVersion
from tasks.models import Task, TaskAttempt
from tasks.service import input_digest
from hashlib import sha256
import json
from case_details.clinical_adoption import adopt_clinical_capture, clinical_admission_binding
from case_details.clinical_interval_adoption import qualify_interval_protocol
from case_details.clinical_metrics_v2 import ClinicalCaptureV2, MeasurementPlanV2
from case_details.evaluation_units import EvaluationUnitsRejected
from requests.models import WriteRequest
from sources.service import approve_revision
from tests.synthetic_clinical_capture import qualified_replay_capture
from tests.test_clinical_intervals import protocol_payload


def synthetic_interval_evidence(fixture, plan):
    grant=ServiceRoleGrant.objects.create(principal=fixture.actor,environment=fixture.gate,action='manage_config',
        granted_by=fixture.actor,evidence_ref=fixture.reference,valid_from=timezone.now())
    identity=uuid4()
    reference=f'urn:sjia:source-revision:{identity}'
    payload=protocol_payload(plan['plan_ref'],reference)
    approval_revision,approval_ref=fixture.evidence({'mode':'SYNTHETIC_SOFTWARE_TEST_ONLY',
        'clinical_validation':False,'scope':'candidate algorithm software test'},
        purposes=['configuration_approval','clinical_evaluation'])
    revision,_=fixture.evidence(payload,purposes=['evaluation_protocol','clinical_evaluation'],identity=identity)
    revision.status='pending';revision.rights_evidence_ref=approval_ref
    revision.save(update_fields=['status','rights_evidence_ref'])
    # Exercise the real evidence approval function; the HTTP receipt/audit
    # boundary is supplied explicitly as immutable software fixture evidence.
    approve_revision(revision=revision,evidence_ref=approval_ref)
    revision.refresh_from_db()
    now=timezone.now(); request_id=uuid4()
    receipt=WriteRequest.objects.create(actor=fixture.actor,action='source_revision_approve',
        target_key=f'source_revision:{revision.id}',request_id=request_id,payload_sha256='a'*64,
        normalization_version='synthetic-1',status='committed',resource_type='source_revision',
        resource_id=revision.id,created_at=now,completed_at=now)
    audit=write_audit(actor=fixture.actor,action='source_revision_approve',object_type='source_revision',
        object_id=revision.id,object_version='1',request_id=request_id,outcome='committed',
        retention_policy=fixture.retention,occurred_at=now)
    plan['confidence_interval_protocol_ref']=reference
    plan['confidence_interval_binding']={'protocol_sha256':revision.checksum,
        'approval_evidence_ref':approval_ref,'approval_evidence_sha256':approval_revision.checksum,
        'approval_audit_id':str(audit.id),'purpose':'clinical_evaluation'}
    return SimpleNamespace(revision=revision,reference=reference,payload=payload,
        approval_revision=approval_revision,audit=audit,receipt=receipt,grant=grant)


class ClinicalIntervalAdoptionTests(TestCase):
    def setUp(self):
        network=patch('urllib.request.urlopen',side_effect=AssertionError('External calls forbidden'))
        network.start();self.addCleanup(network.stop)
        self.flow=qualified_replay_capture(self,interval_factory=synthetic_interval_evidence)

    def adopt(self):
        f=self.flow
        return adopt_clinical_capture(row=f.row,sources=f.sources,snapshot=f.snapshot,capture_ref=f.capture_ref)

    def persist_result(self, admitted, *, status):
        from case_details.clinical_interval_adoption import calculate_interval_adoption
        snapshot={**self.flow.snapshot,'input':{**self.flow.snapshot['input'],'clinical_capture_ref':self.flow.capture_ref},
            'clinical_capture_binding':clinical_admission_binding(admitted)}
        now=timezone.now()
        # The existing capture fixture supplies an explicit frozen-dataset
        # boundary. Materialize that boundary as a real FK target; this is not
        # a claim that the separate dataset freeze HTTP workflow ran here.
        boundary=self.flow.row
        EvaluationDatasetVersion.objects.get_or_create(id=boundary.id, defaults={
            'dataset_id':uuid4(),'version':1,'purpose':boundary.purpose,'environment':boundary.environment,
            'created_by':self.flow.base.actor.id,'created_at':boundary.frozen_at,'frozen_at':boundary.frozen_at,
            'protocol_ref':self.flow.plan['plan_ref'],'coverage':deepcopy(boundary.coverage),
            'checksum':boundary.checksum,'status':'frozen'})
        run=EvaluationRun.objects.create(dataset_version_id=self.flow.row.id,operator_id=self.flow.base.actor.id,
            implementation_sha256=self.flow.capture['implementation_sha256'],rules_refs=self.flow.snapshot['configurations'],
            input_refs=[snapshot],result_refs=[],denominators=self.flow.snapshot['denominators'],limitations=[],
            started_at=now,finished_at=now,status=status,mode='replay')
        task_inputs={'evaluation_run_id':str(run.id),'snapshot':snapshot}
        task=Task.objects.create(actor=self.flow.base.actor,task_type='assessment',schema_version='evaluation-run-1',
            input_refs=task_inputs,input_sha256=input_digest(task_inputs),status='completed',status_version=1,
            lease_generation=1,created_at=now,deadline_at=now+timedelta(minutes=1))
        # TestCase owns a wrapping rollback transaction. Only the no-ORM pure
        # calculation phase is invoked under this isolated guard substitution;
        # unlocked worker orchestration is covered separately. All reads below
        # use real Task/TaskAttempt/EvaluationRun rows and live replay services.
        with patch('case_details.clinical_interval_adoption.connection',SimpleNamespace(in_atomic_block=False)):
            saved=calculate_interval_adoption(admitted,task.input_sha256)
        TaskAttempt.objects.create(task=task,attempt_number=1,lease_generation=1,started_at=now,
            finished_at=timezone.now(),status='completed',usage_evidence={'clinical_adoption':saved})
        return run,task,saved

    def test_actual_replay_admission_binds_exact_body_approval_plan_freeze_and_purpose(self):
        with patch('case_details.clinical_intervals.np.random.Generator',side_effect=AssertionError('admission must not bootstrap')):
            result=self.adopt()
            self.assertEqual(result['report']['confidence_intervals']['status'],'PENDING')
            self.assertEqual(result['report']['schema_version'],'clinical-metrics-report-3')
            proof=result['proofs']['confidence_interval']
            self.assertEqual(proof['body_sha256'],self.flow.interval.revision.checksum)
            self.assertEqual(proof['approval']['audit_id'],str(self.flow.interval.audit.id))
            self.assertEqual(proof['approval']['receipt_id'],str(self.flow.interval.receipt.id))
            self.assertEqual(proof['frozen_at'],self.flow.row.frozen_at.isoformat())
            self.assertEqual(proof['purpose'],'clinical_evaluation')
            self.assertEqual(clinical_admission_binding(result)['confidence_interval'],proof)
            self.assertFalse(result['report']['clinical_validation'])

    def test_missing_or_late_recorded_approval_cannot_use_capture_time_as_approval(self):
        interval=self.flow.interval
        interval.audit.occurred_at=self.flow.row.frozen_at+timedelta(seconds=1)
        interval.audit.save(update_fields=['occurred_at'])
        with self.assertRaisesRegex(EvaluationUnitsRejected,'INTERVAL_PRE_FREEZE_APPROVAL_REQUIRED'):
            self.adopt()
        interval.audit.delete()
        with self.assertRaisesRegex(EvaluationUnitsRejected,'INTERVAL_PRE_FREEZE_APPROVAL_REQUIRED'):
            self.adopt()

    def test_protocol_revocation_and_approval_evidence_revocation_rechecked(self):
        self.adopt()
        for revision in (self.flow.interval.revision,self.flow.interval.approval_revision):
            revision.status='revoked';revision.save(update_fields=['status'])
            with self.subTest(revision=revision.id),self.assertRaises(EvaluationUnitsRejected):
                self.adopt()
            revision.status='approved';revision.save(update_fields=['status'])

    def test_current_purpose_and_exact_artifact_body_are_required(self):
        interval=self.flow.interval
        interval.revision.allowed_use=['evaluation_protocol']
        interval.revision.save(update_fields=['allowed_use'])
        with self.assertRaisesRegex(EvaluationUnitsRejected,'PURPOSE_DENIED'):
            self.adopt()
        interval.revision.allowed_use.append('clinical_evaluation')
        interval.revision.save(update_fields=['allowed_use'])
        Path(interval.revision.source.location).write_text('{}')
        with self.assertRaisesRegex(EvaluationUnitsRejected,'CHECKSUM_MISMATCH'):
            self.adopt()

    def test_receipt_or_approver_change_does_not_retain_qualified_interval(self):
        self.adopt()
        self.flow.interval.receipt.completed_at+=timedelta(microseconds=1)
        self.flow.interval.receipt.save(update_fields=['completed_at'])
        with self.assertRaisesRegex(EvaluationUnitsRejected,'INTERVAL_APPROVAL_UNAVAILABLE'):
            self.adopt()

    def test_saved_read_rechecks_then_validates_signed_result_without_bootstrap(self):
        from case_details.clinical_adoption import checked_saved_clinical
        run,task,saved=self.persist_result(self.adopt(),status='insufficient')
        self.assertEqual(saved['report']['confidence_intervals']['results'][0]['status'],'INSUFFICIENT_CLUSTERS')
        self.assertFalse(saved['report']['confidence_intervals']['required_complete'])
        with patch('case_details.clinical_intervals.np.random.Generator',side_effect=AssertionError('read cannot bootstrap')):
            self.assertEqual(checked_saved_clinical(run,task,self.flow.row,self.flow.sources),saved['report'])
            self.flow.interval.revision.status='revoked'
            self.flow.interval.revision.save(update_fields=['status'])
            with self.assertRaises(EvaluationUnitsRejected):
                checked_saved_clinical(run,task,self.flow.row,self.flow.sources)

    def test_legacy_report_two_binding_remains_readable_without_adding_intervals(self):
        from case_details.clinical_adoption import checked_saved_clinical
        legacy=adopt_clinical_capture(row=self.flow.row,sources=self.flow.sources,snapshot=self.flow.snapshot,
            capture_ref=self.flow.capture_ref,include_intervals=False)
        self.assertEqual(legacy['report']['schema_version'],'clinical-metrics-report-2')
        self.assertNotIn('confidence_intervals',legacy['report'])
        self.assertNotIn('confidence_interval',clinical_admission_binding(legacy))
        run,task,saved=self.persist_result(legacy,status='completed')
        with patch('case_details.clinical_intervals.np.random.Generator',side_effect=AssertionError('legacy read cannot bootstrap')):
            self.assertEqual(checked_saved_clinical(run,task,self.flow.row,self.flow.sources),saved['report'])

    def test_frozen_plan_rejects_replaced_protocol_bytes_even_with_updated_revision_checksum(self):
        interval=self.flow.interval
        content=json.dumps({**interval.payload,'seed':18},sort_keys=True).encode()
        Path(interval.revision.source.location).write_bytes(content)
        interval.revision.checksum=sha256(content).hexdigest()
        interval.revision.save(update_fields=['checksum'])
        with self.assertRaisesRegex(EvaluationUnitsRejected,'CHECKSUM_MISMATCH'):
            self.adopt()

    def test_frozen_plan_rejects_swapped_approval_evidence_and_changed_body(self):
        interval=self.flow.interval
        original=interval.revision.rights_evidence_ref
        interval.revision.rights_evidence_ref=self.flow.base.fixture.reference
        interval.revision.save(update_fields=['rights_evidence_ref'])
        with self.assertRaisesRegex(EvaluationUnitsRejected,'INTERVAL_APPROVAL_EVIDENCE_CHANGED'):
            self.adopt()
        interval.revision.rights_evidence_ref=original
        interval.revision.save(update_fields=['rights_evidence_ref'])
        content=b'{"mode":"different-synthetic-approval"}'
        revision=interval.approval_revision
        Path(revision.source.location).write_bytes(content)
        revision.checksum=sha256(content).hexdigest()
        revision.save(update_fields=['checksum'])
        with self.assertRaisesRegex(EvaluationUnitsRejected,'CHECKSUM_MISMATCH'):
            self.adopt()

    def test_approver_authority_revocation_expiry_and_account_state_rechecked(self):
        self.adopt()
        grant=self.flow.interval.grant
        for field in ('revoked_at','valid_until'):
            setattr(grant,field,timezone.now());grant.save(update_fields=[field])
            with self.subTest(field=field),self.assertRaisesRegex(EvaluationUnitsRejected,'INTERVAL_APPROVAL_AUTHORITY_UNAVAILABLE'):
                self.adopt()
            setattr(grant,field,None);grant.save(update_fields=[field])
        actor=self.flow.base.actor
        actor.account_status='disabled';actor.save(update_fields=['account_status'])
        # The same fixture actor also owns the actual replay; its existing
        # reader rejects the inactive account before CI-specific qualification.
        with self.assertRaisesRegex(Denied,'ACCOUNT_NOT_ACTIVE'):
            self.adopt()
