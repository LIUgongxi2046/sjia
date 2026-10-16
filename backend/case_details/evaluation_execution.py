from django.db import transaction
from django.utils import timezone

from archives.models import EvaluationRun
from audit.service import write_audit
from case_details.evaluation_run_inputs import lock_run_inputs, partition_units
from case_details.evaluation_runs import run_snapshot, run_task
from case_details.evaluation_units import EvaluationUnitsRejected, UNIT_NAMES
from configurations.service import compute_checksum
from requests.models import WriteIntent
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import record_receipt
from tasks.service import acquire_lease, create_attempt, finish_attempt


def checked_event(event):
    if (event.event_type != 'evaluation_run.requested' or event.schema_version != 'evaluation-run-1'
            or event.aggregate_version != 1 or event.task_id is None
            or event.resource_refs != {'evaluation_run_id': str(event.aggregate_id)}):
        raise EvaluationUnitsRejected('DATASET_RUN_EVENT_INVALID')
    run = EvaluationRun.objects.select_for_update().get(id=event.aggregate_id)
    task = run_task(run)
    if task.id != event.task_id or event.correlation_id != task.id:
        raise EvaluationUnitsRejected('DATASET_RUN_EVENT_INVALID')
    return run, task


def inventory_results(run, sources, snapshot, input_sha256):
    selected = partition_units(sources.manifest, {item.id for item in sources.manifest.entries('patients')
        if str(item.id) in snapshot['denominators']['patient_ids']})
    results = []
    for name in UNIT_NAMES:
        coverage = getattr(sources.manifest, name)
        results.append({'schema_version': 'evaluation-unit-result-1', 'scope': 'inventory',
            'unit': name, 'state': 'unrecorded' if coverage is None else coverage.state,
            'registered_items': None if coverage is None else len(selected[name]),
            'unit_ids': sorted(str(item.id) for item in selected[name]),
            'evidence_refs': [] if coverage is None else coverage.evidence_refs,
            'limitations': [] if coverage is None else coverage.limitations,
            'dataset_version_id': snapshot['dataset_version_id'], 'dataset_checksum': snapshot['dataset_checksum'],
            'protocol_ref': sources.proofs['protocol']['reference'],
            'source_manifest_ref': sources.proofs['unit_manifest']['reference'],
            'input_sha256': input_sha256,
            'result_ref': f'/support/evaluation-runs/{run.id}/results#{name}'})
    return results


def execute_evaluation_run(event, owner):
    with transaction.atomic():
        run, task = checked_event(event)
        if ConsumerReceipt.objects.filter(consumer='evaluation_run', event=event).exists():
            return
        if task.status in ('completed', 'failed', 'cancelled', 'superseded', 'awaiting_resolution'):
            record_receipt('evaluation_run', event, run.id)
            return
        if task.deadline_at <= timezone.now():
            raise EvaluationUnitsRejected('TASK_DEADLINE_EXCEEDED')
        Task.objects.filter(id=task.id, status='running', lease_until__lte=timezone.now()).update(
            status='queued', lease_owner=None)
        if not acquire_lease(task.id, owner, task.deadline_at):
            return
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
        run.status = 'running'
        run.save(update_fields=['status'])
    with transaction.atomic():
        run, task = checked_event(event)
        if task.status != 'running' or task.lease_owner != owner or task.lease_generation != attempt.lease_generation:
            finish_attempt(attempt, 'superseded')
            return
        snapshot = run_snapshot(run)
        row, sources, runtime, policy, current, context = lock_run_inputs(dataset_id=snapshot['dataset_id'],
            payload=snapshot['input'], actor=task.actor,
            auth_epoch=int(task.input_refs['authorization_context']['auth_epoch']))
        if current != snapshot or context != task.input_refs['authorization_context']:
            raise EvaluationUnitsRejected('DATASET_RUN_INPUT_CHANGED')
        if task.deadline_at <= timezone.now():
            raise EvaluationUnitsRejected('TASK_DEADLINE_EXCEEDED')
        run.result_refs = inventory_results(run, sources, current, task.input_sha256)
        incomplete = any(getattr(sources.manifest, name).state != 'complete'
            or not snapshot['denominators']['unit_ids'][name] for name in sources.protocol.required_units)
        clinical = None
        if current['input'].get('clinical_capture_ref') is not None:
            from case_details.clinical_adoption import adopt_clinical_capture, clinical_admission_binding
            clinical = adopt_clinical_capture(row=row, sources=sources, snapshot=current,
                capture_ref=current['input']['clinical_capture_ref'])
            if clinical_admission_binding(clinical) != current['clinical_capture_binding']:
                raise EvaluationUnitsRejected('CLINICAL_CAPTURE_BINDING_CHANGED')
            incomplete = incomplete or any(item['status'] != 'completed' for item in clinical['report']['execution_steps'])
        elif sources.protocol.evaluation_kind == 'clinical':
            incomplete = True
            run.limitations = [*run.limitations, {'unit': 'confirmed_labels', 'reason': 'clinical_metrics_not_run'}]
        prepared_clinical = clinical
        prepared_results, prepared_limitations = run.result_refs, run.limitations
        prepared_input_sha256 = task.input_sha256
    # The bounded estimator never runs while holding admission/read DB locks.
    if clinical is not None:
        from case_details.clinical_interval_adoption import calculate_interval_adoption
        clinical = calculate_interval_adoption(clinical, prepared_input_sha256)
    with transaction.atomic():
        run, task = checked_event(event)
        if (task.status != 'running' or task.lease_owner != owner
                or task.lease_generation != attempt.lease_generation):
            finish_attempt(attempt, 'superseded')
            return
        snapshot = run_snapshot(run)
        row, sources, runtime, policy, current, context = lock_run_inputs(dataset_id=snapshot['dataset_id'],
            payload=snapshot['input'], actor=task.actor,
            auth_epoch=int(task.input_refs['authorization_context']['auth_epoch']))
        if (current != snapshot or context != task.input_refs['authorization_context']
                or task.input_sha256 != prepared_input_sha256):
            raise EvaluationUnitsRejected('DATASET_RUN_INPUT_CHANGED')
        if task.deadline_at <= timezone.now():
            raise EvaluationUnitsRejected('TASK_DEADLINE_EXCEEDED')
        if prepared_clinical is not None:
            from case_details.clinical_adoption import adopt_clinical_capture, clinical_admission_binding
            fresh = adopt_clinical_capture(row=row, sources=sources, snapshot=current,
                capture_ref=current['input']['clinical_capture_ref'])
            if clinical_admission_binding(fresh) != clinical_admission_binding(prepared_clinical):
                raise EvaluationUnitsRejected('CLINICAL_CAPTURE_BINDING_CHANGED')
            if '_interval_inputs' in fresh:
                from case_details.clinical_interval_adoption import verify_saved_interval_adoption
                verify_saved_interval_adoption(fresh, clinical, task.input_sha256)
                incomplete = incomplete or not clinical['report']['confidence_intervals']['required_complete']
        run.result_refs, run.limitations = prepared_results, prepared_limitations
        run.status, run.finished_at = ('insufficient' if incomplete else 'completed'), timezone.now()
        run.save(update_fields=['result_refs', 'limitations', 'status', 'finished_at'])
        task.status, task.status_version, task.lease_until = 'completed', task.status_version + 1, run.finished_at
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        finish_attempt(attempt, TaskAttempt.Status.COMPLETED, usage_evidence={
            'scope': 'inventory', 'result_sha256': compute_checksum(run.result_refs),
            'denominators_sha256': compute_checksum(run.denominators), 'input_sha256': task.input_sha256,
            'clinical_metrics': 'CALCULATED' if clinical else 'NOT_RUN', 'clinical_adoption': clinical, 'family_notifications': False})
        record_receipt('evaluation_run', event, run.id)
        write_audit(actor=task.actor, action='dataset_run_execute', object_type='evaluation_run',
            object_id=run.id, object_version='1', request_id=task.request_row.request_id,
            outcome=run.status, retention_policy=WriteIntent.objects.select_for_update().get(
                write_request_id=task.request_row_id).retention_policy, occurred_at=run.finished_at)


@transaction.atomic
def record_evaluation_failure(event, owner, reason):
    run, task = checked_event(event)
    now = timezone.now()
    if task.status not in ('accepted', 'queued') and not (task.status == 'running'
            and (task.lease_owner == owner or task.lease_until is not None and task.lease_until <= now)):
        return
    task.status, task.status_version, task.lease_until = 'failed', task.status_version + 1, now
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    run.status, run.finished_at = 'failed', now
    run.save(update_fields=['status', 'finished_at'])
    attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
    if attempts.exists():
        attempts.update(status='failed', finished_at=now, error_code=reason)
    else:
        TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
            lease_generation=task.lease_generation, started_at=now, finished_at=now, status='failed', error_code=reason)
    if not ConsumerReceipt.objects.filter(consumer='evaluation_run', event=event).exists():
        record_receipt('evaluation_run', event, run.id)
