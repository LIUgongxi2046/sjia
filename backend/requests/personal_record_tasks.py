from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError, get_patient
from api.views_reminders import reminder_identity
from archives.models import PersonalRecordTask
from audit.models import AuditEvent
from audit.service import write_audit
from configurations.service import compute_checksum
from notifications.personal_record_tasks import VALIDATORS, personal_tasks, require_personal_scope
from notifications.personal_record_tasks import task_input, task_reference, task_retention
from notifications.reminder_schemas import Version, checked_version
from notifications.reminders import current_scope, scope_binding
from requests.models import WriteIntent, WriteRequest
from requests.service import NORMALIZATION_VERSION, compute_digest, prepare_intent


OPERATIONS = ('personal_record_task_create', 'personal_record_task_change')


class TaskIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['personal_record_task_create', 'personal_record_task_change']
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class TaskGenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_generation: Version


class TaskWriteHeaders(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent_id: UUID
    request_id: UUID
    generation: Version


class TaskLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Literal['personal_record_task_create', 'personal_record_task_change']
    target_key: str = Field(min_length=1, max_length=256)


def task_route(operation, target):
    if operation not in OPERATIONS or not target.startswith('/') or any(part in target for part in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route


def task_scope(actor, epoch, operation, target, payload):
    task_input(payload, change=operation == OPERATIONS[1])
    route = task_route(operation, target)
    initial = None
    if operation == OPERATIONS[0]:
        patient = get_patient(route.kwargs['patient_id'])
    else:
        initial = PersonalRecordTask.objects.filter(id=route.kwargs['task_id'], owner_id=actor.id).first()
        if initial is None:
            raise ApiError('PERSONAL_RECORD_TASK_NOT_FOUND', 404)
        patient = get_patient(initial.patient_id)
    scope = current_scope(owner=actor, patient=patient, auth_epoch=epoch, lock=True)
    require_personal_scope(scope)
    task = personal_tasks(scope, lock=True).get(id=initial.id) if initial is not None else None
    return scope, task


def recovery_payload(intent):
    if intent.operation_id not in OPERATIONS:
        raise ApiError('PERSONAL_RECORD_TASK_RECOVERY_INVALID', 409)
    payload = task_input(intent.recovery_payload, change=intent.operation_id == OPERATIONS[1])
    if (intent.normalization_version != NORMALIZATION_VERSION
            or compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256):
        raise ApiError('PERSONAL_RECORD_TASK_RECOVERY_INVALID', 409)
    return payload


def prepared_task_scope(actor, epoch, operation, target, payload):
    scope, task = task_scope(actor, epoch, operation, target, payload)
    policy, runtime = task_retention(scope,
        payload['retention_policy_id'] if task is None else task.retention_policy_id, lock=True)
    reason = None
    if task is not None:
        if task.version != checked_version(payload['expected_version']) or task.version == 9223372036854775807:
            reason = 'VERSION_CONFLICT'
        elif task.status != 'pending':
            reason = 'PERSONAL_RECORD_TASK_STATE_CONFLICT'
    context = {**scope_binding(scope), 'operation_id': operation,
        'task_sha256': compute_checksum(task_reference(task)) if task else None,
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'retention_id': str(policy.id), 'retention_version': str(policy.version),
        'retention_sha256': compute_checksum({'rules': policy.rules, 'status': policy.status,
            'approved_by': str(policy.approved_by_id), 'approved_at': policy.approved_at.isoformat(),
            'evidence_ref': policy.evidence_ref}),
        'grant_evidence': [[str(row.id), compute_checksum({'evidence_ref': row.evidence_ref})] for row in scope.grants]}
    return scope, task, policy, runtime, context, reason


def require_ready(reason):
    if reason is not None:
        raise ApiError(reason, 409)


def has_write(intent):
    return intent.write_request_id is not None or WriteRequest.objects.filter(actor_id=intent.actor_id,
        action=intent.operation_id, target_key=intent.target_key, request_id=intent.request_id).exists()


def authorized_task_intent(actor, epoch, intent):
    payload = recovery_payload(intent)
    scope, task = task_scope(actor, epoch, intent.operation_id, intent.target_key, payload)
    if scope.patient.id != intent.patient_id:
        raise ApiError('PERSONAL_RECORD_TASK_INTENT_SCOPE_MISMATCH', 409)
    locked = WriteIntent.objects.select_for_update().get(id=intent.id, actor_id=actor.id, operation_id__in=OPERATIONS)
    if recovery_payload(locked) != payload or locked.patient_id != scope.patient.id:
        raise ApiError('PERSONAL_RECORD_TASK_INTENT_SCOPE_MISMATCH', 409)
    return locked, scope, task


def task_receipt(intent):
    payload, saved = recovery_payload(intent), intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.request_id != intent.request_id or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version or saved.resource_type != 'personal_record_task'
            or saved.expected_version is None or saved.expected_version < 1
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('PERSONAL_RECORD_TASK_RECEIPT_INVALID', 409)
    task = PersonalRecordTask.objects.filter(id=saved.resource_id, owner_id=intent.actor_id,
        patient_id=intent.patient_id, version__gte=saved.expected_version).first()
    route = task_route(intent.operation_id, intent.target_key)
    if task is None:
        raise ApiError('PERSONAL_RECORD_TASK_RECEIPT_INVALID', 409)
    if intent.operation_id == OPERATIONS[0]:
        if (route.kwargs['patient_id'] != task.patient_id or saved.expected_version != 1
                or task.title != payload['title'] or task.description != payload['description']
                or task.retention_policy_id != UUID(payload['retention_policy_id'])):
            raise ApiError('PERSONAL_RECORD_TASK_RECEIPT_INVALID', 409)
    elif (route.kwargs['task_id'] != task.id or saved.expected_version != checked_version(payload['expected_version']) + 1
            or task.status != {'complete': 'completed', 'cancel': 'cancelled'}[payload['action']]):
        raise ApiError('PERSONAL_RECORD_TASK_RECEIPT_INVALID', 409)
    if not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
            action=intent.operation_id, object_type='personal_record_task', object_id=task.id,
            object_version=str(saved.expected_version), request_id=intent.request_id,
            occurred_at=saved.completed_at, outcome='committed', retention_policy_id=intent.retention_policy_id).exists():
        raise ApiError('PERSONAL_RECORD_TASK_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': 'personal_record_task', 'resource_id': str(task.id),
        'version': str(saved.expected_version), 'saved_at': saved.completed_at.isoformat(),
        'request_id': str(intent.request_id), 'task_id': None}


def intent_view(intent):
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation),
        'status': intent.status, 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256}


def task_request_view(intent, context=None, reason=None):
    unknown = intent.status != 'committed' and has_write(intent)
    cancelled = intent.error_code == 'PERSONAL_RECORD_TASK_INTENT_CANCELLED'
    retryable = (not unknown and not cancelled and intent.status != 'committed' and reason is None
        and (intent.status == 'failed' or intent.expires_at <= timezone.now() or intent.authorization_context != context))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if unknown else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': retryable, 'receipt': task_receipt(intent) if intent.status == 'committed' else None,
        'error_code': 'PERSONAL_RECORD_TASK_RESULT_UNKNOWN' if unknown else intent.error_code if cancelled else reason or intent.error_code,
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'record_task_input': recovery_payload(intent)}


def prepare_task_intent(request):
    actor, epoch = reminder_identity(request)
    if request.query_params or next(VALIDATORS['IntentInput'].iter_errors(request.data), None) is not None:
        raise ApiError('PERSONAL_RECORD_TASK_INPUT_INVALID', 422)
    data = TaskIntentInput.model_validate(request.data)
    task_scope(actor, epoch, data.operation_id, data.target_key, data.payload)
    existing = WriteIntent.objects.select_for_update().filter(actor_id=actor.id, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None:
        if compute_digest(data.operation_id, data.target_key, '2.0.0', data.payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        existing, _, _ = authorized_task_intent(actor, epoch, existing)
        if existing.status == 'committed':
            task_receipt(existing)
        elif has_write(existing):
            raise ApiError('PERSONAL_RECORD_TASK_RESULT_UNKNOWN', 409)
        return intent_view(existing)
    scope, _, policy, runtime, context, reason = prepared_task_scope(actor, epoch, data.operation_id, data.target_key, data.payload)
    require_ready(reason)
    if WriteRequest.objects.filter(actor_id=actor.id, action=data.operation_id, target_key=data.target_key,
            request_id=data.request_id).exists():
        raise ApiError('PERSONAL_RECORD_TASK_RESULT_UNKNOWN', 409)
    return intent_view(prepare_intent(actor=actor, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=data.payload, schema_version='2.0.0', patient=scope.patient,
        retention_policy=policy, ttl_seconds=runtime.payload['api']['intent_ttl_seconds'],
        authorization_context=context, recovery_payload=data.payload))


def read_task_request(request, intent_id):
    actor, epoch = reminder_identity(request)
    rows = WriteIntent.objects.filter(actor_id=actor.id, operation_id__in=OPERATIONS)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('PERSONAL_RECORD_TASK_QUERY_INVALID', 422)
        lookup = TaskLookup.model_validate(dict(request.query_params.items()))
        intent = rows.filter(request_id=intent_id, operation_id=lookup.operation_id, target_key=lookup.target_key).first()
    else:
        intent = rows.filter(id=intent_id).first()
    if intent is None:
        raise ApiError('PERSONAL_RECORD_TASK_REQUEST_NOT_FOUND', 404)
    intent, scope, task = authorized_task_intent(actor, epoch, intent)
    payload = recovery_payload(intent)
    task_retention(scope, payload['retention_policy_id'] if task is None else task.retention_policy_id, lock=True)
    if intent.status == 'committed' or intent.error_code == 'PERSONAL_RECORD_TASK_INTENT_CANCELLED':
        return task_request_view(intent)
    _, _, _, _, context, reason = prepared_task_scope(actor, epoch, intent.operation_id, intent.target_key, payload)
    return task_request_view(intent, context, reason)


def retry_task_intent(request, intent_id):
    actor, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('PERSONAL_RECORD_TASK_QUERY_INVALID', 422)
    generation = checked_version(TaskGenerationInput.model_validate(request.data).expected_generation)
    intent = WriteIntent.objects.filter(id=intent_id, actor_id=actor.id, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('PERSONAL_RECORD_TASK_REQUEST_NOT_FOUND', 404)
    intent, _, _ = authorized_task_intent(actor, epoch, intent)
    if (intent.status == 'committed' or intent.generation != generation or generation == 9223372036854775807
            or intent.error_code == 'PERSONAL_RECORD_TASK_INTENT_CANCELLED'):
        raise ApiError('PERSONAL_RECORD_TASK_RETRY_NOT_AVAILABLE', 409)
    _, _, policy, runtime, context, reason = prepared_task_scope(actor, epoch,
        intent.operation_id, intent.target_key, recovery_payload(intent))
    require_ready(reason)
    if policy.id != intent.retention_policy_id or not task_request_view(intent, context, reason)['may_retry']:
        raise ApiError('PERSONAL_RECORD_TASK_RETRY_NOT_AVAILABLE', 409)
    intent.generation += 1
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.authorization_context = context
    intent.save(update_fields=['generation', 'status', 'error_code', 'completed_at', 'prepared_at', 'expires_at', 'authorization_context'])
    return intent_view(intent)


def cancel_task_intent(request, intent_id):
    actor, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('PERSONAL_RECORD_TASK_QUERY_INVALID', 422)
    generation = checked_version(TaskGenerationInput.model_validate(request.data).expected_generation)
    intent = WriteIntent.objects.filter(id=intent_id, actor_id=actor.id, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('PERSONAL_RECORD_TASK_REQUEST_NOT_FOUND', 404)
    intent, scope, task = authorized_task_intent(actor, epoch, intent)
    if intent.generation != generation or intent.status == 'committed' or has_write(intent):
        raise ApiError('PERSONAL_RECORD_TASK_CANCEL_NOT_AVAILABLE', 409)
    if intent.error_code != 'PERSONAL_RECORD_TASK_INTENT_CANCELLED':
        payload = recovery_payload(intent)
        policy, _ = task_retention(scope, payload['retention_policy_id'] if task is None else task.retention_policy_id, lock=True)
        if policy.id != intent.retention_policy_id:
            raise ApiError('PERSONAL_RECORD_TASK_AUTHORIZATION_CHANGED', 403)
        intent.status, intent.error_code, intent.completed_at = 'failed', 'PERSONAL_RECORD_TASK_INTENT_CANCELLED', timezone.now()
        intent.save(update_fields=['status', 'error_code', 'completed_at'])
        write_audit(actor=actor, patient=scope.patient, action='personal_record_task_intent_cancel',
            object_type='write_intent', object_id=intent.id, object_version=str(intent.generation),
            request_id=intent.request_id, outcome='cancelled', retention_policy=policy, occurred_at=intent.completed_at)
    return task_request_view(intent)


def submitted_task_intent(request, actor, epoch, operation):
    if request.query_params:
        raise ApiError('PERSONAL_RECORD_TASK_QUERY_INVALID', 422)
    headers = TaskWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    checked_version(headers.generation)
    target = request.path.removeprefix('/api/v2')
    intent = WriteIntent.objects.filter(id=headers.intent_id, actor_id=actor.id, operation_id=operation).first()
    if intent is None:
        raise ApiError('PERSONAL_RECORD_TASK_REQUEST_NOT_FOUND', 404)
    intent, scope, task = authorized_task_intent(actor, epoch, intent)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(operation, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('PERSONAL_RECORD_TASK_INTENT_MISMATCH', 409)
    if intent.status != 'committed':
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('PERSONAL_RECORD_TASK_INTENT_EXPIRED', 409)
        if has_write(intent):
            raise ApiError('PERSONAL_RECORD_TASK_RESULT_UNKNOWN', 409)
        _, _, policy, _, context, reason = prepared_task_scope(actor, epoch, operation, target, recovery_payload(intent))
        require_ready(reason)
        if policy.id != intent.retention_policy_id or intent.authorization_context != context:
            raise ApiError('PERSONAL_RECORD_TASK_AUTHORIZATION_CHANGED', 403)
    return intent, scope, task
