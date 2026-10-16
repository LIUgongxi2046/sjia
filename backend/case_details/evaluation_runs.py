from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from archives.models import EvaluationRun
from authorization.models import AccessGrant, ServiceRoleGrant
from authorization.policy import matching_case_grants
from case_details.evaluation_datasets import checked_dataset, current_dataset
from case_details.evaluation_evidence import lock_evaluation_actor
from case_details.evaluation_run_inputs import check_run_configuration_proofs, lock_run_inputs
from case_details.evaluation_units import EvaluationUnitsRejected
from case_details.evaluation_views import evaluation_runtime
from configurations.service import compute_checksum
from identity.models import Patient
from tasks.models import Task, TaskAttempt
from tasks.outbox import emit_event
from tasks.service import input_digest, register_task


@transaction.atomic
def create_evaluation_run(*, dataset_id, payload, actor, auth_epoch):
    row, sources, runtime, policy, snapshot, context = lock_run_inputs(
        dataset_id=dataset_id, payload=payload, actor=actor, auth_epoch=auth_epoch)
    run = EvaluationRun.objects.create(id=uuid4(), dataset_version_id=row.id, operator_id=actor.id,
        implementation_sha256=snapshot['implementation_sha256'], rules_refs=snapshot['configurations'],
        input_refs=[snapshot], result_refs=[], denominators=snapshot['denominators'],
        limitations=[{'unit': unit, 'reason': 'unrecorded'} for unit, count in snapshot['denominators']['counts'].items() if count is None],
        started_at=timezone.now(), finished_at=None, status='pending', mode='replay')
    task = register_task(actor=actor, task_type=Task.TaskType.ASSESSMENT, schema_version='evaluation-run-1',
        input_refs={'evaluation_run_id': str(run.id), 'snapshot': snapshot, 'authorization_context': context},
        deadline_seconds=policy['queue_wait_seconds'] + policy['execution_seconds'])
    emit_event(event_type='evaluation_run.requested', schema_version='evaluation-run-1', aggregate_id=run.id,
        aggregate_version=1, correlation_id=task.id, resource_refs={'evaluation_run_id': str(run.id)}, task=task)
    return run, task


def run_snapshot(run):
    if run.mode != 'replay' or len(run.input_refs) != 1 or not isinstance(run.input_refs[0], dict):
        raise EvaluationUnitsRejected('DATASET_RUN_INPUT_CHANGED')
    snapshot = run.input_refs[0]
    if (snapshot['schema_version'] != 'evaluation-run-inputs-1'
            or snapshot['dataset_version_id'] != str(run.dataset_version_id)
            or snapshot['implementation_sha256'] != run.implementation_sha256
            or snapshot['configurations'] != run.rules_refs or snapshot['denominators'] != run.denominators
            or snapshot['mode'] != run.mode):
        raise EvaluationUnitsRejected('DATASET_RUN_INPUT_CHANGED')
    return snapshot


def run_task(run):
    task = Task.objects.select_for_update().get(task_type='assessment', schema_version='evaluation-run-1',
        input_refs__evaluation_run_id=str(run.id))
    snapshot = run_snapshot(run)
    if (task.actor_id != run.operator_id or task.patient_id is not None
            or input_digest(task.input_refs) != task.input_sha256 or task.input_refs['snapshot'] != snapshot
            or task.input_refs['authorization_context']['snapshot_sha256'] != compute_checksum(snapshot)):
        raise EvaluationUnitsRejected('DATASET_RUN_TASK_CHANGED')
    return task


@transaction.atomic
def lock_run_read(*, run_id, actor, auth_epoch):
    actor, gate, grant = lock_evaluation_actor(actor, auth_epoch, action='read_assessment')
    locked_grant = ServiceRoleGrant.objects.select_for_update().get(id=grant.id)
    actor, gate, grant = lock_evaluation_actor(actor, auth_epoch, action='read_assessment')
    if grant.id != locked_grant.id:
        raise EvaluationUnitsRejected('DATASET_RUN_AUTHORIZATION_CHANGED')
    run = EvaluationRun.objects.select_for_update().filter(id=run_id).first()
    if run is None:
        raise EvaluationUnitsRejected('DATASET_RUN_NOT_FOUND')
    snapshot = run_snapshot(run)
    task = run_task(run)
    if run.status in ('completed', 'insufficient'):
        attempt = TaskAttempt.objects.filter(task=task, status='completed', lease_generation=task.lease_generation).first()
        if (attempt is None or not isinstance(attempt.usage_evidence, dict) or run.finished_at is None or task.status != 'completed'
                or attempt.usage_evidence.get('result_sha256') != compute_checksum(run.result_refs)
                or attempt.usage_evidence.get('denominators_sha256') != compute_checksum(run.denominators)
                or attempt.usage_evidence.get('input_sha256') != task.input_sha256):
            raise EvaluationUnitsRejected('DATASET_RUN_RESULT_CHANGED')
    row = current_dataset(snapshot['dataset_id'], version=int(snapshot['dataset_version']))
    if row.id != run.dataset_version_id or row.checksum != snapshot['dataset_checksum'] or row.status != 'frozen':
        raise EvaluationUnitsRejected('DATASET_RUN_INPUT_CHANGED')
    check_run_configuration_proofs(snapshot, row.frozen_at)
    sources, _, partitions, _ = checked_dataset(row, actor, auth_epoch, action='read_assessment')
    patients = {item.patient_id for item in partitions if item.partition == snapshot['input']['partition']}
    if sorted(str(value) for value in patients) != snapshot['denominators']['patient_ids']:
        raise EvaluationUnitsRejected('DATASET_RUN_PARTITION_CHANGED')
    for patient in Patient.objects.select_for_update().filter(id__in=patients).order_by('id'):
        grants = [item for item in matching_case_grants(actor, patient, 'read_assessment', timezone.now(), whole_resource=True)
            if item.purpose == row.purpose and item.evidence_ref.strip()]
        locked = {item.id for item in AccessGrant.objects.select_for_update().filter(id__in=[item.id for item in grants]).order_by('id')}
        if not any(item.id in locked and item.purpose == row.purpose and item.evidence_ref.strip()
                for item in matching_case_grants(actor, patient, 'read_assessment', timezone.now(), whole_resource=True)):
            raise EvaluationUnitsRejected('DATASET_RUN_CASE_SCOPE_DENIED')
    from case_details.clinical_adoption import checked_saved_clinical
    checked_saved_clinical(run, task, row, sources)
    return run, task, row, evaluation_runtime(gate)


def evaluation_task_view(run, task, runtime):
    terminal = task.status in ('completed', 'failed', 'cancelled', 'superseded', 'awaiting_resolution')
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    return {'task_id': str(task.id), 'task_type': task.task_type,
        'request_id': str(task.request_row.request_id) if task.request_row_id else None,
        'accepted_at': task.created_at.isoformat(), 'status': task.status, 'status_version': str(task.status_version),
        'poll_after_ms': None if terminal else runtime.payload['api']['poll_after_ms'], 'allowed_actions': [],
        'result_type': 'evaluation_run' if run.status in ('completed', 'insufficient') else None,
        'result_ref': str(run.id) if run.status in ('completed', 'insufficient') else None,
        'error_code': attempt.error_code if attempt else None}


@transaction.atomic
def read_evaluation_run(*, run_id, actor, auth_epoch):
    run, task, row, runtime = lock_run_read(run_id=run_id, actor=actor, auth_epoch=auth_epoch)
    statuses = {'pending': 'accepted', 'running': 'running', 'completed': 'completed', 'failed': 'failed', 'insufficient': 'insufficient'}
    if run.status not in statuses:
        raise EvaluationUnitsRejected('DATASET_RUN_STATUS_INVALID')
    if run.status in ('completed', 'insufficient') and (task.status != 'completed' or run.finished_at is None):
        raise EvaluationUnitsRejected('DATASET_RUN_STATUS_CHANGED')
    if run.status == 'failed' and (task.status != 'failed' or run.finished_at is None):
        raise EvaluationUnitsRejected('DATASET_RUN_STATUS_CHANGED')
    return {'run_id': str(run.id), 'dataset_id': str(row.dataset_id), 'dataset_version': str(row.version),
        'task': evaluation_task_view(run, task, runtime), 'mode': run.mode, 'status': statuses[run.status],
        'result_ref': f'/support/evaluation-runs/{run.id}/results' if run.status in ('completed', 'insufficient') else None,
        'denominator_ref': f'/support/evaluation-runs/{run.id}/denominators',
        'label_version_ref': run_snapshot(run)['input']['label_version_ref']}
