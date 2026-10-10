from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone

from api.base import ApiError
from archives.models import ObservationPeriodVersion
from audit.models import AuditEvent
from files.purpose_schemas import PurposeWriteHeaders
from requests.archive_documents import family_identity, intent_view
from requests.models import WriteIntent, WriteRequest
from requests.service import compute_digest, prepare_intent, fail_intent, retry_intent
from records.period_schemas import checked_period
from records.periods import prepared_scope
from records.medical_times import time_values
from medical_time.models import MedicalTime


OPERATIONS = ('observation_period_change',)


def period_target(target):
    if not target.startswith('/') or any(part in target for part in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != OPERATIONS[0] or reverse(route.url_name, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['patient_id']


def original_payload(intent):
    payload = checked_period('Input', intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256:
        raise ApiError('OBSERVATION_PERIOD_RECOVERY_INVALID', 409)
    return payload


def matching_request(intent):
    return WriteRequest.objects.filter(actor=intent.actor, action=intent.operation_id,
        request_id=intent.request_id, target_key=intent.target_key).exists()


def authorized(request, actor, intent):
    patient, current, retention, context = prepared_scope(request, actor, period_target(intent.target_key),
        original_payload(intent), writing=intent.status != 'committed', check_version=False)
    if patient.id != intent.patient_id or retention.id != intent.retention_policy_id:
        raise ApiError('OBSERVATION_PERIOD_INTENT_SCOPE_INVALID', 409)
    return patient, current, context


def period_receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.actor_id != intent.actor_id
            or saved.patient_id != intent.patient_id or saved.request_id != intent.request_id
            or saved.target_key != intent.target_key or saved.action != intent.operation_id
            or saved.payload_sha256 != intent.payload_sha256 or saved.resource_type != 'observation_period'
            or saved.expected_version is None or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('OBSERVATION_PERIOD_RECEIPT_INVALID', 409)
    row = ObservationPeriodVersion.objects.get(id=saved.resource_id, patient_id=intent.patient_id, owner_id=intent.actor_id,
        version=saved.expected_version)
    payload = original_payload(intent)
    for name, identity in (('start', row.start_time_id), ('end', row.end_time_id)):
        actual = MedicalTime.objects.get(id=identity, patient_id=intent.patient_id)
        if any(getattr(actual, key) != value for key, value in time_values(payload[name]).items()):
            raise ApiError('OBSERVATION_PERIOD_RECEIPT_INVALID', 409)
    if (row.label != payload['label'] or row.label_source != 'self_report'
            or row.episode_id != (UUID(payload['episode_id']) if payload['episode_id'] else None)
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
                action=intent.operation_id, object_type='observation_period', object_id=row.id,
                object_version=str(row.version), request_id=intent.request_id, occurred_at=saved.completed_at, outcome='committed').exists()):
        raise ApiError('OBSERVATION_PERIOD_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': 'observation_period', 'resource_id': str(row.id),
        'version': str(row.version), 'saved_at': saved.completed_at.isoformat(), 'request_id': str(intent.request_id), 'task_id': None}


def version_matches(intent, current):
    return original_payload(intent)['expected_version'] == (str(current.version) if current else None)


def request_view(intent, current, context):
    unlinked = intent.write_request_id is None and matching_request(intent)
    if intent.status == 'prepared' and not unlinked and not version_matches(intent, current):
        fail_intent(intent=intent, error_code='VERSION_CONFLICT')
    state = 'unknown' if unlinked else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status]
    retryable = (not unlinked and intent.write_request_id is None and version_matches(intent, current)
        and (intent.status == 'failed' or intent.status == 'prepared'
            and (intent.expires_at <= timezone.now() or intent.authorization_context != context)))
    return checked_period('Request', {'intent_id': str(intent.id), 'request_id': str(intent.request_id),
        'operation_id': intent.operation_id, 'target_key': intent.target_key, 'state': state, 'may_retry': retryable,
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256,
        'receipt': period_receipt(intent) if intent.status == 'committed' else None, 'error_code': intent.error_code,
        'period_input': original_payload(intent)})


def prepare_period_intent(request):
    actor, _ = family_identity(request)
    data = checked_period('Intent', request.data)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data['operation_id'],
        target_key=data['target_key'], request_id=data['request_id']).first()
    if existing is not None:
        if compute_digest(data['operation_id'], data['target_key'], '2.0.0', data['payload']) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorized(request, actor, existing)
        if existing.status == 'committed':
            period_receipt(existing)
        return intent_view(existing)
    patient, _, retention, context = prepared_scope(request, actor, period_target(data['target_key']), data['payload'])
    return intent_view(prepare_intent(actor=actor, operation_id=data['operation_id'], target_key=data['target_key'],
        request_id=UUID(data['request_id']), payload=data['payload'], schema_version='2.0.0', patient=patient,
        retention_policy=retention, authorization_context=context, recovery_payload=data['payload']))


def read_period_request(request, intent_id):
    actor, _ = family_identity(request)
    if request.query_params:
        if (set(request.query_params) != {'lookup', 'operation_id', 'target_key'}
                or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)
                or request.query_params['lookup'] != 'request_id' or request.query_params['operation_id'] not in OPERATIONS):
            raise ApiError('OBSERVATION_PERIOD_QUERY_INVALID', 422)
        period_target(request.query_params['target_key'])
        intent = WriteIntent.objects.select_for_update().get(actor=actor, request_id=intent_id,
            operation_id=request.query_params['operation_id'], target_key=request.query_params['target_key'])
    else:
        intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    _, current, context = authorized(request, actor, intent)
    return request_view(intent, current, context)


def retry_period_intent(request, intent_id):
    actor, _ = family_identity(request)
    payload = request.data
    if (not isinstance(payload, dict) or set(payload) != {'expected_generation'} or not isinstance(payload['expected_generation'], str)
            or not payload['expected_generation'].isascii() or not payload['expected_generation'].isdecimal()
            or payload['expected_generation'].startswith('0') or len(payload['expected_generation']) > 19):
        raise ApiError('OBSERVATION_PERIOD_RETRY_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    _, current, context = authorized(request, actor, intent)
    if not request_view(intent, current, context)['may_retry'] or int(payload['expected_generation']) != intent.generation:
        raise ApiError('OBSERVATION_PERIOD_RETRY_UNAVAILABLE', 409)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='OBSERVATION_PERIOD_INTENT_EXPIRED' if intent.expires_at <= timezone.now() else 'OBSERVATION_PERIOD_SCOPE_CHANGED')
    retry_intent(intent=intent, expected_generation=int(payload['expected_generation']))
    intent.authorization_context = context
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_period_intent(request, actor):
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id__in=OPERATIONS)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(intent.operation_id, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('OBSERVATION_PERIOD_INTENT_MISMATCH', 409)
    patient, current, context = authorized(request, actor, intent)
    if intent.status != 'committed':
        if not version_matches(intent, current):
            raise ApiError('VERSION_CONFLICT', 409)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('OBSERVATION_PERIOD_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('OBSERVATION_PERIOD_RESULT_UNKNOWN', 409)
        if context != intent.authorization_context:
            raise ApiError('OBSERVATION_PERIOD_SCOPE_CHANGED', 409)
    return intent, patient, current
