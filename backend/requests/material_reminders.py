from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError, get_patient
from api.views_reminders import reminder_identity
from archives.models import ArchivePage, MaterialReminder
from audit.models import AuditEvent
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from files.models import FileObject
from notifications.reminder_schemas import Version, checked_version, reminder_input
from notifications.reminders import current_scope, scoped_reminders, target_snapshot
from requests.models import WriteIntent, WriteRequest
from requests.service import NORMALIZATION_VERSION, compute_digest, prepare_intent
from retention.models import RetentionPolicy


OPERATIONS = ('material_reminder_create', 'material_reminder_change')


class ReminderIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['material_reminder_create', 'material_reminder_change']
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class ReminderRetryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_generation: Version


class ReminderWriteHeaders(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent_id: UUID
    request_id: UUID
    generation: Version


class ReminderRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Literal['material_reminder_create', 'material_reminder_change']
    target_key: str = Field(min_length=1, max_length=256)


def target_route(operation, target):
    if operation not in OPERATIONS or not target.startswith('/') or any(part in target for part in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route


def checked_payload(intent):
    if intent.operation_id not in OPERATIONS or not isinstance(intent.recovery_payload, dict):
        raise ApiError('REMINDER_RECOVERY_INVALID', 409)
    reminder_input(intent.recovery_payload, change=intent.operation_id == OPERATIONS[1],
        now=timezone.now(), check_schedule=False)
    if (intent.normalization_version != NORMALIZATION_VERSION or compute_digest(intent.operation_id,
            intent.target_key, '2.0.0', intent.recovery_payload) != intent.payload_sha256):
        raise ApiError('REMINDER_RECOVERY_INVALID', 409)
    return intent.recovery_payload


def target_scope(actor, epoch, operation, target, payload, *, lock=True):
    value = reminder_input(payload, change=operation == OPERATIONS[1], now=timezone.now(), check_schedule=False)
    route = target_route(operation, target)
    reminder = None
    if operation == OPERATIONS[0]:
        patient = get_patient(route.kwargs['patient_id'])
    else:
        reminder = MaterialReminder.objects.filter(id=route.kwargs['reminder_id'], owner_id=actor.id).first()
        if reminder is None:
            raise ApiError('REMINDER_NOT_FOUND', 404)
        patient = get_patient(reminder.patient_id)
    scope = current_scope(owner=actor, patient=patient, auth_epoch=epoch, lock=lock)
    if reminder is not None:
        rows = scoped_reminders(scope)
        if lock:
            rows = rows.select_for_update()
        reminder = rows.filter(id=reminder.id).first()
        if reminder is None:
            raise ApiError('REMINDER_NOT_FOUND', 404)
    target_type = value.target_type if reminder is None else reminder.target_type
    target_id = value.target_id if reminder is None else reminder.target_ref
    expected = checked_version(value.target_version) if reminder is None or value.action == 'configure' else reminder.target_version
    source, state = target_snapshot(scope, target_type, target_id, expected, lock=lock)
    return scope, reminder, source, state, value


def source_retention(source, target_type):
    if target_type in ('draft', 'record_task'):
        policy_ids = [source.retention_policy_id]
    else:
        pages = list(ArchivePage.objects.select_for_update().filter(batch_id=source.id,
            patient_id=source.patient_id).order_by('id'))
        file_ids = {page.file_id for page in pages}
        files = list(FileObject.objects.select_for_update().filter(id__in=file_ids,
            patient_id=source.patient_id).order_by('id'))
        if not pages or {file.id for file in files} != file_ids:
            raise ApiError('REMINDER_RETENTION_UNAVAILABLE', 409)
        policy_ids = list({file.retention_policy_id for file in files})
    if len(policy_ids) != 1 or policy_ids[0] is None:
        raise ApiError('REMINDER_RETENTION_UNAVAILABLE', 409)
    policy = RetentionPolicy.objects.select_for_update().get(id=policy_ids[0])
    if target_type == 'record_task' and policy.data_class != 'personal_record_task':
        raise ApiError('REMINDER_RETENTION_UNAVAILABLE', 409)
    return policy


def runtime_configuration(scope):
    runtime = ConfigVersion.objects.filter(id=scope.gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None or configuration_qualification_reason(runtime, scope.gate.environment, audience='support'):
        return None
    return runtime


def authorization_context(scope, reminder, source, retention, runtime):
    from archives.models import PersonalRecordTask
    from notifications.personal_record_tasks import task_reference

    return {'audience': 'family', 'auth_epoch': str(scope.owner.auth_epoch),
        'environment': scope.gate.environment, 'gate_version': str(scope.gate.version),
        'patient_id': str(scope.patient.id), 'patient_epoch': str(scope.patient.data_epoch),
        'target_id': str(source.id), 'target_version': str(source.version), 'target_status': source.status,
        'target_payload_sha256': compute_checksum(source.payload) if hasattr(source, 'payload') else
            compute_checksum(task_reference(source)) if isinstance(source, PersonalRecordTask) else None,
        'reminder_id': str(reminder.id) if reminder else None,
        'reminder_version': str(reminder.version) if reminder else None,
        'runtime_id': str(runtime.id) if runtime else None, 'runtime_checksum': runtime.checksum if runtime else None,
        'retention_id': str(retention.id), 'retention_version': str(retention.version),
        'retention_checksum': compute_checksum({'rules': retention.rules, 'status': retention.status,
            'approved_by': str(retention.approved_by_id),
            'approved_at': retention.approved_at.isoformat() if retention.approved_at else None,
            'evidence_ref': retention.evidence_ref}),
        'grants': [{'id': str(row.id), 'scope': row.record_scope, 'record_id': str(row.assigned_record_id),
            'fields': sorted(row.field_codes), 'purpose': row.purpose, 'relation_id': str(row.relation_id),
            'evidence_sha256': compute_checksum({'evidence_ref': row.evidence_ref}),
            'valid_from': row.valid_from.isoformat(), 'valid_until': row.valid_until.isoformat() if row.valid_until else None,
            'revoked_at': row.revoked_at.isoformat() if row.revoked_at else None} for row in scope.grants],
        'relations': [{'id': str(row.id), 'version': str(row.version), 'auth_epoch': str(row.auth_epoch),
            'valid_from': row.valid_from.isoformat(), 'valid_until': row.valid_until.isoformat() if row.valid_until else None,
            'revoked_at': row.revoked_at.isoformat() if row.revoked_at else None} for row in scope.relations]}


def readiness_reason(scope, reminder, source, state, value, retention, runtime):
    if reminder is not None:
        if reminder.version != checked_version(value.expected_version) or reminder.version == 9223372036854775807:
            return 'VERSION_CONFLICT'
        if reminder.status not in ('disabled', 'enabled', 'paused'):
            return 'REMINDER_STATE_CONFLICT'
        if ((value.action == 'pause' and reminder.status != 'enabled')
                or (value.action == 'enable' and reminder.status not in ('disabled', 'paused'))
                or (value.action == 'snooze' and reminder.status not in ('enabled', 'paused'))):
            return 'REMINDER_STATE_CONFLICT'
    requires_target = reminder is None or value.action not in ('disable', 'pause')
    if requires_target and state != 'pending':
        return 'REMINDER_TARGET_NOT_PENDING'
    if requires_target:
        scheduled = value.due_at if value.due_at is not None else reminder.schedule_at
        if scheduled is None or scheduled <= timezone.now():
            return 'REMINDER_SCHEDULE_INVALID'
    if reminder is None and MaterialReminder.objects.filter(owner_id=scope.owner.id, patient_id=scope.patient.id,
            target_type=value.target_type, target_ref=source.id, status__in=['disabled', 'enabled', 'paused']).exists():
        return 'REMINDER_ALREADY_EXISTS'
    if (retention.status != 'active' or retention.approved_by_id is None or retention.approved_at is None
            or not retention.evidence_ref or not retention.evidence_ref.strip()):
        return 'REMINDER_RETENTION_UNAVAILABLE'
    if runtime is None:
        return 'RUNTIME_CONFIGURATION_PENDING'
    requires_channel = value.enabled if reminder is None else (value.action in ('enable', 'snooze')
        or (value.action == 'configure' and (value.enabled is True
            or (value.enabled is None and reminder.status == 'enabled'))))
    if requires_channel:
        from notifications.material_delivery import material_unavailability_reason

        return material_unavailability_reason()
    return None


def prepared_scope(actor, epoch, operation, target, payload):
    scope, reminder, source, state, value = target_scope(actor, epoch, operation, target, payload)
    retention = source_retention(source, value.target_type if reminder is None else reminder.target_type)
    runtime = runtime_configuration(scope)
    context = authorization_context(scope, reminder, source, retention, runtime)
    reason = readiness_reason(scope, reminder, source, state, value, retention, runtime)
    return scope, reminder, retention, runtime, context, reason


def matching_request(intent):
    return WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
        target_key=intent.target_key, request_id=intent.request_id).exists()


def reminder_receipt(intent):
    payload = checked_payload(intent)
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.request_id != intent.request_id or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version
            or saved.resource_type != 'material_reminder' or saved.expected_version is None or saved.expected_version < 1
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('REMINDER_RECEIPT_INVALID', 409)
    reminder = MaterialReminder.objects.filter(id=saved.resource_id, owner_id=intent.actor_id,
        patient_id=intent.patient_id, version__gte=saved.expected_version).first()
    route = target_route(intent.operation_id, intent.target_key)
    if reminder is None or (intent.operation_id == OPERATIONS[0] and (reminder.target_ref != UUID(payload['target_id'])
            or reminder.target_type != payload['target_type'] or route.kwargs['patient_id'] != intent.patient_id
            or saved.expected_version != 1)):
        raise ApiError('REMINDER_RECEIPT_INVALID', 409)
    if intent.operation_id == OPERATIONS[1] and (route.kwargs['reminder_id'] != reminder.id
            or saved.expected_version != checked_version(payload['expected_version']) + 1):
        raise ApiError('REMINDER_RECEIPT_INVALID', 409)
    if not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
            action=intent.operation_id, object_type='material_reminder', object_id=saved.resource_id,
            object_version=str(saved.expected_version), request_id=intent.request_id,
            occurred_at=saved.completed_at, outcome='committed', retention_policy_id=intent.retention_policy_id).exists():
        raise ApiError('REMINDER_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': 'material_reminder', 'resource_id': str(saved.resource_id),
        'version': str(saved.expected_version), 'saved_at': saved.completed_at.isoformat(),
        'request_id': str(intent.request_id), 'task_id': None}


def intent_view(intent):
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation),
        'status': intent.status, 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256}


def authorized_intent(actor, epoch, intent):
    payload = checked_payload(intent)
    scope, reminder, _, _, _ = target_scope(actor, epoch, intent.operation_id, intent.target_key, payload)
    if scope.patient.id != intent.patient_id:
        raise ApiError('REMINDER_INTENT_SCOPE_MISMATCH', 409)
    locked = WriteIntent.objects.select_for_update().get(id=intent.id, actor_id=actor.id, operation_id__in=OPERATIONS)
    if checked_payload(locked) != payload or locked.patient_id != scope.patient.id:
        raise ApiError('REMINDER_INTENT_SCOPE_MISMATCH', 409)
    return locked, scope, reminder


def require_ready(reason):
    if reason is not None:
        status = 503 if reason in ('RUNTIME_CONFIGURATION_PENDING', 'NOTIFICATION_PROVIDER_UNAVAILABLE',
            'NOTIFICATION_CONFIGURATION_UNAVAILABLE', 'NOTIFICATION_PROVIDER_PROTOCOL_UNAVAILABLE') else 409
        raise ApiError(reason, status)


def prepare_reminder_intent(request):
    actor, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    data = ReminderIntentInput.model_validate(request.data)
    target_scope(actor, epoch, data.operation_id, data.target_key, data.payload)
    existing = WriteIntent.objects.select_for_update().filter(actor_id=actor.id, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None:
        if compute_digest(data.operation_id, data.target_key, '2.0.0', data.payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        existing, _, _ = authorized_intent(actor, epoch, existing)
        if existing.status == 'committed':
            reminder_receipt(existing)
        elif matching_request(existing) or existing.write_request_id is not None:
            raise ApiError('REMINDER_RESULT_UNKNOWN', 409)
        return intent_view(existing)
    scope, _, retention, runtime, context, reason = prepared_scope(actor, epoch, data.operation_id, data.target_key, data.payload)
    require_ready(reason)
    if WriteRequest.objects.filter(actor_id=actor.id, action=data.operation_id,
            target_key=data.target_key, request_id=data.request_id).exists():
        raise ApiError('REMINDER_RESULT_UNKNOWN', 409)
    intent = prepare_intent(actor=actor, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=data.payload, schema_version='2.0.0', patient=scope.patient,
        retention_policy=retention, ttl_seconds=runtime.payload['api']['intent_ttl_seconds'],
        authorization_context=context, recovery_payload=data.payload)
    return intent_view(intent)


def request_view(intent, context, reason):
    unknown = intent.status != 'committed' and (intent.write_request_id is not None or matching_request(intent))
    retryable = (not unknown and intent.status != 'committed' and reason is None and
        intent.error_code != 'REMINDER_INTENT_CANCELLED' and
        (intent.status == 'failed' or intent.expires_at <= timezone.now() or intent.authorization_context != context))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if unknown else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': retryable, 'receipt': reminder_receipt(intent) if intent.status == 'committed' else None,
        'error_code': 'REMINDER_RESULT_UNKNOWN' if unknown else intent.error_code if intent.error_code == 'REMINDER_INTENT_CANCELLED' else reason or intent.error_code,
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'reminder_input': checked_payload(intent)}


def read_reminder_request(request, intent_id):
    actor, epoch = reminder_identity(request)
    rows = WriteIntent.objects.filter(actor_id=actor.id, operation_id__in=OPERATIONS)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('REMINDER_QUERY_INVALID', 422)
        lookup = ReminderRequestLookup.model_validate(dict(request.query_params.items()))
        intent = rows.filter(request_id=intent_id, operation_id=lookup.operation_id, target_key=lookup.target_key).first()
    else:
        intent = rows.filter(id=intent_id).first()
    if intent is None:
        raise ApiError('REMINDER_REQUEST_NOT_FOUND', 404)
    intent, _, _ = authorized_intent(actor, epoch, intent)
    if intent.status == 'committed':
        return request_view(intent, None, None)
    _, _, _, _, context, reason = prepared_scope(actor, epoch, intent.operation_id, intent.target_key, checked_payload(intent))
    return request_view(intent, context, reason)


def retry_reminder_intent(request, intent_id):
    actor, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    data = ReminderRetryInput.model_validate(request.data)
    generation = checked_version(data.expected_generation)
    intent = WriteIntent.objects.filter(id=intent_id, actor_id=actor.id, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('REMINDER_REQUEST_NOT_FOUND', 404)
    intent, _, _ = authorized_intent(actor, epoch, intent)
    if (intent.status == 'committed' or intent.generation != generation or generation == 9223372036854775807
            or intent.error_code == 'REMINDER_INTENT_CANCELLED'):
        raise ApiError('REMINDER_RETRY_NOT_AVAILABLE', 409)
    _, _, retention, runtime, context, reason = prepared_scope(actor, epoch, intent.operation_id,
        intent.target_key, checked_payload(intent))
    require_ready(reason)
    if retention.id != intent.retention_policy_id or not request_view(intent, context, reason)['may_retry']:
        raise ApiError('REMINDER_RETRY_NOT_AVAILABLE', 409)
    intent.generation += 1
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.authorization_context = context
    intent.save(update_fields=['generation', 'status', 'error_code', 'completed_at', 'prepared_at',
        'expires_at', 'authorization_context'])
    return intent_view(intent)


def submitted_reminder_intent(request, actor, epoch, operation):
    if request.query_params:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    headers = ReminderWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    checked_version(headers.generation)
    target = request.path.removeprefix('/api/v2')
    intent = WriteIntent.objects.filter(id=headers.intent_id, actor_id=actor.id, operation_id=operation).first()
    if intent is None:
        raise ApiError('REMINDER_REQUEST_NOT_FOUND', 404)
    intent, scope, reminder = authorized_intent(actor, epoch, intent)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(operation, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('REMINDER_INTENT_MISMATCH', 409)
    if intent.status != 'committed':
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('REMINDER_INTENT_EXPIRED', 409)
        if intent.write_request_id is not None or matching_request(intent):
            raise ApiError('REMINDER_RESULT_UNKNOWN', 409)
        _, _, retention, _, context, reason = prepared_scope(actor, epoch, operation, target, checked_payload(intent))
        require_ready(reason)
        if retention.id != intent.retention_policy_id or intent.authorization_context != context:
            raise ApiError('REMINDER_AUTHORIZATION_CHANGED', 403)
    return intent, scope, reminder


def cancel_reminder_intent(request, intent_id):
    from audit.service import write_audit

    actor, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    generation = checked_version(ReminderRetryInput.model_validate(request.data).expected_generation)
    intent = WriteIntent.objects.filter(id=intent_id, actor_id=actor.id, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('REMINDER_REQUEST_NOT_FOUND', 404)
    intent, scope, _ = authorized_intent(actor, epoch, intent)
    if (intent.generation != generation or intent.status == 'committed' or intent.write_request_id is not None
            or matching_request(intent)):
        raise ApiError('REMINDER_CANCEL_NOT_AVAILABLE', 409)
    if intent.error_code != 'REMINDER_INTENT_CANCELLED':
        policy = RetentionPolicy.objects.select_for_update().get(id=intent.retention_policy_id)
        if policy.status != 'active' or policy.approved_at is None or policy.approved_by_id is None or not policy.evidence_ref:
            raise ApiError('REMINDER_RETENTION_UNAVAILABLE', 409)
        intent.status, intent.error_code, intent.completed_at = 'failed', 'REMINDER_INTENT_CANCELLED', timezone.now()
        intent.save(update_fields=['status', 'error_code', 'completed_at'])
        write_audit(actor=actor, patient=scope.patient, action='material_reminder_intent_cancel',
            object_type='write_intent', object_id=intent.id, object_version=str(intent.generation), request_id=intent.request_id,
            outcome='cancelled', retention_policy=policy, occurred_at=intent.completed_at)
    return request_view(intent, None, None)
