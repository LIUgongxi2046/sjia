import json
from uuid import NAMESPACE_URL, uuid5

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.service import write_audit
from configurations.service import compute_checksum
from config.celery import app
from files.disposition_storage import (DISPOSITION_EVENTS, deletion_evidence_state,
    disposition_object_proof, disposition_task_schema)
from files.purpose import runtime_environment
from files.purpose_dependencies import invalidate_resource
from files.purpose_schemas import PurposeDispositionTaskRefs
from files.service import object_store
from retention.disposition import DispositionRejected, execute_local_item, lock_environment, lock_request_access, locked_item, mark_item_awaiting_provider, mark_item_deleted, provider_copy_for_item, scope_sha256, target_file
from retention.models import DispositionItem, DispositionRequest, SimulatedProviderCopy
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt
from tasks.service import acquire_lease, create_attempt, finish_attempt, input_digest, register_task


def disposition_tasks(request):
    from retention.external_deletion import SIMULATION_DELETE_TASK_SCHEMA

    return Task.objects.filter(task_type='disposition', patient_id=request.patient_id).filter(
        Q(schema_version__in=DISPOSITION_EVENTS, input_refs__disposition_id=str(request.id))
        | Q(schema_version=SIMULATION_DELETE_TASK_SCHEMA,
            input_refs__disposition_ref__disposition_id=str(request.id))).order_by('created_at', 'id')


def task_refs(request, item, policy, actor_epoch, previous_task_id=None):
    file, authority_file, copy = target_file(item, request.patient_id)
    return PurposeDispositionTaskRefs(disposition_id=request.id, item_id=item.id, environment=runtime_environment(),
        auth_epoch=actor_epoch, policy_version=str(policy.version), policy_sha256=compute_checksum(policy.rules),
        scope_sha256=scope_sha256(request.scope), approval_sha256=compute_checksum(request.scope['approval_context']),
        target_version=item.target_version, file_identity=disposition_object_proof(object_store, file),
        previous_task_id=previous_task_id)


def validate_refs(task, actor, epoch, *, require_actor_epoch):
    refs = PurposeDispositionTaskRefs.model_validate(task.input_refs)
    if (task.task_type != 'disposition' or task.schema_version not in DISPOSITION_EVENTS
            or disposition_task_schema(object_store, refs.file_identity) != task.schema_version
            or input_digest(task.input_refs) != task.input_sha256):
        raise DispositionRejected('DISPOSITION_TASK_INPUT_CHANGED')
    item, request = locked_item(DispositionItem.objects.get(id=refs.item_id, request_id=refs.disposition_id))
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
        request, actor, epoch, require_approval=True)
    current = task_refs(request, item, policy, epoch, refs.previous_task_id)
    if not require_actor_epoch:
        current.auth_epoch = refs.auth_epoch
    if task.patient_id != request.patient_id or current != refs:
        raise DispositionRejected('DISPOSITION_TASK_INPUT_CHANGED')
    return refs, item, request, policy, receipt_policy


@transaction.atomic
def submit_simulated_provider_disposition(*, item, actor, auth_epoch, payload, request_row=None):
    from retention.external_deletion import (ExternalDeletionRejected, ExternalDeletionRequest,
        SIMULATION_DELETE_TASK_SCHEMA, query_simulated_external_deletion, register_simulated_external_deletion_task)

    item, request = locked_item(item)
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
        request, actor, auth_epoch, require_approval=True)
    if (payload.policy_version != str(policy.version) or payload.expected_scope_sha256 != scope_sha256(request.scope)
            or payload.expected_target_version != item.target_version):
        raise DispositionRejected('VERSION_CONFLICT')
    if item.target_type != 'provider_copy' or item.status not in ('pending', 'failed', 'blocked'):
        raise DispositionRejected('DISPOSITION_ITEM_NOT_PENDING')
    provider_copy = provider_copy_for_item(item, request.patient_id)
    if provider_copy.status != 'available':
        raise DispositionRejected('EXTERNAL_DELETE_ALREADY_REQUESTED')
    request_id = uuid5(NAMESPACE_URL, f'simulation-provider-delete:{request.id}:{item.id}:{item.target_version}')
    deletion_request = ExternalDeletionRequest(schema_version='provider-delete-request-1', request_id=request_id,
        provider_id=provider_copy.provider_id, object_id=provider_copy.object_id, object_version=provider_copy.object_version,
        checksum=provider_copy.checksum, size_bytes=provider_copy.size_bytes, requested_at=timezone.now())
    linked = {'disposition_id': request.id, 'item_id': item.id, 'target_type': item.target_type,
        'target_id': item.target_id, 'target_version': item.target_version, 'auth_epoch': auth_epoch}
    try:
        task = register_simulated_external_deletion_task(actor=actor, request=deletion_request, disposition_ref=linked)
    except ExternalDeletionRejected as exc:
        raise DispositionRejected(exc.reason_code) from exc
    provider_copy.deletion_request_id = deletion_request.request_id
    provider_copy.processing_evidence = {**provider_copy.processing_evidence,
        'deletion_request': deletion_request.model_dump(mode='json'), 'task_id': str(task.id),
        'disposition_scope_sha256': scope_sha256(request.scope), 'policy_version': str(policy.version),
        'authorization_epoch': auth_epoch}
    provider_copy.status = 'deletion_requested'
    provider_copy.save(update_fields=['deletion_request_id', 'processing_evidence', 'status'])
    mark_item_awaiting_provider(item=item)
    request.status = DispositionRequest.Status.RUNNING
    request.save(update_fields=['status'])
    if not OutboxEvent.objects.filter(task=task, event_type='disposition.provider_requested').exists():
        emit_event(event_type='disposition.provider_requested', schema_version=SIMULATION_DELETE_TASK_SCHEMA,
            aggregate_id=request.id, aggregate_version=policy.version, correlation_id=task.id,
            resource_refs={'task_id': str(task.id), 'item_id': str(item.id)}, task=task)
    if request_row is not None:
        task.request_row = request_row
        task.save(update_fields=['request_row'])
    write_audit(actor=actor, patient=request.patient, action='provider_disposition_task_accepted',
        object_type='task', object_id=task.id, object_version=task.input_sha256, outcome='allowed', retention_policy=receipt_policy)
    return task


@transaction.atomic
def execute_simulated_provider_disposition(event, owner):
    from retention.external_deletion import (ExternalDeletionRequest, ExternalDeletionRejected,
        SIMULATION_DELETE_TASK_SCHEMA, execute_simulated_external_deletion_task,
        query_simulated_external_deletion)

    task = Task.objects.select_for_update().get(id=event.task_id, schema_version=SIMULATION_DELETE_TASK_SCHEMA,
        task_type=Task.TaskType.DISPOSITION)
    if event.event_type != 'disposition.provider_requested' or event.schema_version != task.schema_version:
        raise DispositionRejected('DISPOSITION_TASK_TYPE_MISMATCH')
    if ConsumerReceipt.objects.filter(consumer='disposition_provider', event=event).exists():
        return
    refs = task.input_refs.get('disposition_ref')
    if refs is None:
        raise DispositionRejected('DISPOSITION_TASK_BINDING_REQUIRED')
    item, request = locked_item(DispositionItem.objects.get(id=refs['item_id'], request_id=refs['disposition_id']))
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
        request, task.actor, refs['auth_epoch'], require_approval=True)
    if (task.patient_id != request.patient_id or task.input_refs.get('request', {}).get('object_id') != item.target_id
            or task.input_refs.get('request', {}).get('object_version') != item.target_version
            or item.status != 'awaiting_provider'):
        raise DispositionRejected('DISPOSITION_TASK_INPUT_CHANGED')
    request_data = task.input_refs['request']
    deletion_request = ExternalDeletionRequest.model_validate_json(json.dumps(request_data))
    stored = provider_copy_for_item(item, request.patient_id).processing_evidence.get('deletion_request')
    if stored != deletion_request.model_dump(mode='json'):
        raise DispositionRejected('EXTERNAL_DELETE_REQUEST_MISMATCH')
    try:
        result = execute_simulated_external_deletion_task(task.id, lease_owner=owner)
    except ExternalDeletionRejected as exc:
        raise DispositionRejected(exc.reason_code) from exc
    provider_result = query_simulated_external_deletion(deletion_request)
    if provider_result['state'] == 'deleted':
        receipt = provider_result['receipt']
        mark_item_deleted(item=item, receipt_ref=str(deletion_request.request_id),
            receipt_sha256=receipt['evidence_sha256'], executed_by=task.actor, auth_epoch=refs['auth_epoch'])
        record_receipt('disposition_provider', event, item.id)
    elif result['provider_state'] == 'unknown':
        record_receipt('disposition_provider', event, task.id)
    return result


@transaction.atomic
def read_simulated_provider_disposition(*, task_id, actor, auth_epoch):
    from retention.external_deletion import read_simulated_external_deletion_task

    task = Task.objects.select_for_update().get(id=task_id, actor=actor,
        schema_version='simulation-provider-deletion-1', task_type=Task.TaskType.DISPOSITION)
    refs = task.input_refs['disposition_ref']
    item, request = locked_item(DispositionItem.objects.get(id=refs['item_id'], request_id=refs['disposition_id']))
    lock_request_access(request, actor, auth_epoch, require_approval=False)
    result = read_simulated_external_deletion_task(task.id)
    if result['provider_state'] == 'deleted':
        receipt = result['receipt']
        mark_item_deleted(item=item, receipt_ref=receipt['request_id'],
            receipt_sha256=receipt['evidence_sha256'], executed_by=actor, auth_epoch=auth_epoch)
    return result


@transaction.atomic
def submit_local_disposition(*, item, actor, auth_epoch, payload, previous_task=None):
    item, request = locked_item(item)
    request, policy, receipt_policy, rule, decision, file_grants, file_versions = lock_request_access(
        request, actor, auth_epoch, require_approval=True)
    if (payload.policy_version != str(policy.version) or payload.expected_scope_sha256 != scope_sha256(request.scope)
            or payload.expected_target_version != item.target_version):
        raise DispositionRejected('VERSION_CONFLICT')
    if item.status not in ('pending', 'failed', 'blocked') or request.status == 'completed':
        raise DispositionRejected('DISPOSITION_ITEM_NOT_PENDING')
    active = Task.objects.select_for_update().filter(task_type='disposition', input_refs__item_id=str(item.id)).exclude(
        status__in=['completed', 'failed', 'cancelled', 'superseded'])
    if active.exists():
        raise DispositionRejected('DISPOSITION_TASK_ALREADY_ACTIVE')
    item.reason = None
    item.status = 'pending'
    item.save(update_fields=['status', 'reason'])
    refs = task_refs(request, item, policy, auth_epoch, previous_task.id if previous_task is not None else None)
    task_schema = disposition_task_schema(object_store, refs.file_identity)
    task = register_task(actor=actor, patient=request.patient, task_type=Task.TaskType.DISPOSITION,
        schema_version=task_schema, input_refs=refs.model_dump(mode='json'))
    file, authority_file, copy = target_file(item, request.patient_id)
    file.status = 'quarantined'
    file.save(update_fields=['status'])
    invalidate_resource('file_object', file.id, file.object_version)
    if copy is not None:
        invalidate_resource('purpose_copy', copy.id, str(copy.version))
    request.status = 'running'
    request.save(update_fields=['status'])
    emit_event(event_type=DISPOSITION_EVENTS[task_schema], schema_version=task_schema, aggregate_id=request.id,
        aggregate_version=policy.version, correlation_id=task.id, resource_refs={'task_id': str(task.id)}, task=task)
    write_audit(actor=actor, patient=request.patient, action='disposition_task_accepted', object_type='task', object_id=task.id,
        object_version=task.input_sha256, outcome='allowed', retention_policy=receipt_policy)
    return task


def execute_disposition(event, owner):
    with transaction.atomic():
        lock_environment(require_enabled=False)
        task = Task.objects.select_for_update().get(id=event.task_id)
        if (task.task_type != 'disposition' or task.schema_version not in DISPOSITION_EVENTS
                or event.event_type != DISPOSITION_EVENTS[task.schema_version]
                or event.schema_version != task.schema_version):
            raise DispositionRejected('DISPOSITION_TASK_TYPE_MISMATCH')
        if ConsumerReceipt.objects.filter(consumer='disposition', event=event).exists():
            return
        if task.status in ('completed', 'failed', 'cancelled', 'superseded', 'awaiting_resolution'):
            return
        if not acquire_lease(task.id, owner, task.deadline_at):
            return
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
        attempt.usage_evidence = {'input_sha256': task.input_sha256, 'stage': 'accepted', 'provider_called': False}
        attempt.save(update_fields=['usage_evidence'])
    # TaskAttempt 已经提交；实际删除及业务结果分别留下可重新读取的证明。
    with transaction.atomic():
        lock_environment()
        task = Task.objects.get(id=task.id)
        refs, item, request, policy, receipt_policy = validate_refs(task, task.actor, task.input_refs['auth_epoch'], require_actor_epoch=True)
        task = Task.objects.select_for_update().get(id=task.id)
        if task.status != 'running' or task.lease_generation != attempt.lease_generation or task.lease_owner != owner:
            finish_attempt(attempt, 'superseded', usage_evidence=attempt.usage_evidence)
            return
        if task.deadline_at <= timezone.now():
            raise DispositionRejected('TASK_DEADLINE_EXCEEDED')
        item = execute_local_item(item=item, expected_target_version=refs.target_version, policy_version=refs.policy_version,
            executed_by=task.actor, auth_epoch=refs.auth_epoch)
        request.refresh_from_db()
        task.status = 'completed'
        task.status_version += 1
        task.lease_until = timezone.now()
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        finish_attempt(attempt, TaskAttempt.Status.COMPLETED, usage_evidence={'input_sha256': task.input_sha256,
            'item_id': str(item.id), 'receipt_sha256': item.receipt_sha256, 'receipt_ref': item.receipt_ref,
            'request_status': request.status, 'provider_called': task.schema_version == 's3-delete-1'})
        record_receipt('disposition', event, request.id)


@transaction.atomic
def record_disposition_failure(event, owner, error_code):
    lock_environment(require_enabled=False)
    task = Task.objects.select_for_update().get(id=event.task_id)
    if task.status != 'running' or task.lease_owner != owner:
        return
    refs = PurposeDispositionTaskRefs.model_validate(task.input_refs)
    request = DispositionRequest.objects.select_for_update().get(id=refs.disposition_id)
    item = DispositionItem.objects.select_for_update().get(id=refs.item_id, request=request)
    if (task.schema_version not in DISPOSITION_EVENTS
            or event.event_type != DISPOSITION_EVENTS[task.schema_version]
            or event.schema_version != task.schema_version
            or disposition_task_schema(object_store, refs.file_identity) != task.schema_version):
        raise DispositionRejected('DISPOSITION_TASK_INPUT_CHANGED')
    evidence = deletion_evidence_state(object_store, proof=refs.file_identity, operation_id=item.id)
    prepared = evidence['journal_present']
    receipt_present = evidence['receipt_present']
    task.status = 'awaiting_resolution' if prepared or receipt_present else 'failed'
    task.status_version += 1
    task.lease_until = timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
    for attempt in attempts:
        finish_attempt(attempt, 'uncertain' if prepared or receipt_present else 'failed', error_code=error_code,
            usage_evidence={'input_sha256': task.input_sha256, 'journal_present': prepared,
                'receipt_present': receipt_present, 'execution_owner': owner,
                **({'storage_kind': 's3', 'provider_call_state': 'unknown'} if task.schema_version == 's3-delete-1'
                    else {'provider_called': False})})
    if item.status in ('pending', 'failed', 'blocked'):
        item.status = 'failed'
        item.reason = error_code
        item.save(update_fields=['status', 'reason'])


@app.task(name='retention.jobs.reconcile_local_dispositions')
def reconcile_local_dispositions():
    count = 0
    unstarted = Task.objects.filter(task_type='disposition', schema_version__in=DISPOSITION_EVENTS, status__in=['accepted', 'queued'],
        deadline_at__lte=timezone.now()).values_list('id', flat=True)
    for identity in unstarted:
        with transaction.atomic():
            lock_environment(require_enabled=False)
            task = Task.objects.select_for_update().get(id=identity)
            if task.status not in ('accepted', 'queued') or task.deadline_at > timezone.now():
                continue
            task.status = 'running'
            task.status_version += 1
            task.lease_owner = 'local-disposition-reconciler'
            task.lease_until = timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_owner', 'lease_until'])
            create_attempt(task, task.lease_generation)
            record_disposition_failure(task_event(task), task.lease_owner, 'DISPOSITION_QUEUE_DEADLINE_EXCEEDED')
            count += 1
    identities = Task.objects.filter(task_type='disposition', schema_version__in=DISPOSITION_EVENTS, status='running',
        lease_until__lte=timezone.now(), deadline_at__lte=timezone.now()).values_list('id', flat=True)
    for identity in identities:
        task = Task.objects.get(id=identity)
        event = task_event(task)
        record_disposition_failure(event, task.lease_owner, 'DISPOSITION_WORKER_RESULT_UNCONFIRMED')
        count += 1
    return count


def task_event(task):
    return OutboxEvent.objects.get(task=task, event_type=DISPOSITION_EVENTS[task.schema_version])


@transaction.atomic
def retry_local_disposition(*, task, actor, auth_epoch, expected_status_version):
    lock_environment()
    refs, item, request, policy, receipt_policy = validate_refs(task, actor, auth_epoch, require_actor_epoch=False)
    task = Task.objects.select_for_update().get(id=task.id, actor=actor)
    if str(task.status_version) != expected_status_version:
        raise DispositionRejected('VERSION_CONFLICT')
    if task.status not in ('failed', 'awaiting_resolution'):
        raise DispositionRejected('DISPOSITION_TASK_NOT_RETRYABLE')
    if task.status == 'awaiting_resolution':
        task.status = 'superseded'
        task.status_version += 1
        task.save(update_fields=['status', 'status_version'])
    from files.purpose_schemas import PurposeDispositionExecuteInput

    payload = PurposeDispositionExecuteInput(policy_version=str(policy.version), expected_scope_sha256=scope_sha256(request.scope),
        expected_target_version=item.target_version)
    return submit_local_disposition(item=item, actor=actor, auth_epoch=auth_epoch, payload=payload, previous_task=task)


def disposition_task_result(task):
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    receipt = ConsumerReceipt.objects.filter(consumer='disposition', event__task=task).first()
    if task.status == 'completed' and receipt is None:
        raise DispositionRejected('DISPOSITION_TASK_RECEIPT_REQUIRED')
    return {'result_type': 'disposition' if receipt is not None else None,
        'result_ref': str(receipt.effect_resource_id) if receipt is not None else None,
        'error_code': attempt.error_code if attempt is not None else None}
