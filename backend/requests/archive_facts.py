from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone

from api.base import ApiError
from archives.fact_writes import OPERATIONS, fact_input, fact_scope
from archives.fact_candidates import validate_value
from archives.models import ArchiveFactVersion, ArchivePage
from audit.models import AuditEvent
from files.purpose_schemas import PurposeWriteHeaders
from requests.archive_documents import family_identity, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import compute_digest, fail_intent, prepare_intent, retry_intent


def identifier(operation, target):
    if not target.startswith('/') or '?' in target or '#' in target or '://' in target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['page_id' if operation == 'archive_candidates_confirm' else 'fact_id']


def saved_payload(intent):
    value = fact_input(intent.operation_id, intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', value) != intent.payload_sha256:
        raise ApiError('ARCHIVE_FACT_RECOVERY_INVALID', 409)
    return value


def scope(actor, epoch, intent, *, mutating=False):
    result = fact_scope(actor, epoch, intent.operation_id, identifier(intent.operation_id, intent.target_key),
        saved_payload(intent), mutating=mutating)
    if result[1].id != intent.patient_id or result[2].id != intent.retention_policy_id:
        raise ApiError('ARCHIVE_FACT_SCOPE_INVALID', 409)
    return result


def fact_receipt(intent):
    saved = intent.write_request
    expected = identifier(intent.operation_id, intent.target_key)
    kind = 'archive_page' if intent.operation_id == 'archive_candidates_confirm' else 'archive_fact'
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.request_id != intent.request_id or saved.payload_sha256 != intent.payload_sha256
            or saved.resource_id != expected or saved.resource_type != kind or saved.expected_version is None
            or saved.normalization_version != intent.normalization_version
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('ARCHIVE_FACT_RECEIPT_INVALID', 409)
    if kind == 'archive_page':
        page = ArchivePage.objects.get(id=saved.resource_id, patient_id=intent.patient_id)
        if page.status not in ('confirmed', 'archived') or page.status_version < saved.expected_version:
            raise ApiError('ARCHIVE_FACT_RECEIPT_INVALID', 409)
    else:
        ArchiveFactVersion.objects.get(fact_id=saved.resource_id, patient_id=intent.patient_id,
            version=saved.expected_version, reason=saved_payload(intent)['reason'])
    if not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
            action=intent.operation_id, object_type=kind, object_id=saved.resource_id,
            object_version=str(saved.expected_version), request_id=intent.request_id,
            occurred_at=saved.completed_at, outcome='committed').exists():
        raise ApiError('ARCHIVE_FACT_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': kind, 'resource_id': str(saved.resource_id),
        'version': str(saved.expected_version), 'saved_at': saved.completed_at.isoformat(),
        'request_id': str(saved.request_id), 'task_id': None}


def prepare_fact_intent(request):
    actor, epoch = family_identity(request)
    data = request.data
    validate_value('FactIntentInput', data)
    value = fact_input(data['operation_id'], data['payload'])
    request_id = UUID(data['request_id'])
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data['operation_id'],
        target_key=data['target_key'], request_id=request_id).first()
    if existing is not None:
        if compute_digest(data['operation_id'], data['target_key'], '2.0.0', value) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        scope(actor, epoch, existing)
        return intent_view(existing)
    result = fact_scope(actor, epoch, data['operation_id'], identifier(data['operation_id'], data['target_key']), value, mutating=True)
    intent = prepare_intent(actor=actor, patient=result[1], operation_id=data['operation_id'], target_key=data['target_key'],
        request_id=request_id, schema_version='2.0.0', payload=value, recovery_payload=value,
        retention_policy=result[2], ttl_seconds=result[3].payload['api']['intent_ttl_seconds'], authorization_context=result[4])
    return intent_view(intent)


def fact_request_view(intent, context):
    uncertain = intent.write_request_id is None and matching_request(intent)
    retryable = not uncertain and intent.write_request_id is None and (intent.status == 'failed'
        or intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != context))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if uncertain else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': retryable, 'receipt': fact_receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'fact_input': saved_payload(intent)}


def read_fact_request(request, intent_id):
    actor, epoch = family_identity(request)
    query = request.query_params
    if query:
        if (set(query) != {'lookup', 'operation_id', 'target_key'} or any(len(query.getlist(key)) != 1 for key in query)
                or query['lookup'] != 'request_id' or query['operation_id'] not in OPERATIONS):
            raise ApiError('ARCHIVE_FACT_QUERY_INVALID', 422)
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, request_id=intent_id,
            operation_id=query['operation_id'], target_key=query['target_key']).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, id=intent_id, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('ARCHIVE_FACT_REQUEST_NOT_FOUND', 404)
    return fact_request_view(intent, scope(actor, epoch, intent)[4])


def retry_fact_intent(request, intent_id):
    actor, epoch = family_identity(request)
    if not isinstance(request.data, dict) or set(request.data) != {'expected_generation'}:
        raise ApiError('ARCHIVE_FACT_RETRY_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    result = scope(actor, epoch, intent)
    if request.data['expected_generation'] != str(intent.generation) or not fact_request_view(intent, result[4])['may_retry']:
        raise ApiError('ARCHIVE_FACT_RETRY_NOT_AVAILABLE', 409)
    result = scope(actor, epoch, intent, mutating=True)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='ARCHIVE_FACT_PREPARATION_CHANGED')
    retry_intent(intent=intent, expected_generation=int(intent.generation), ttl_seconds=result[3].payload['api']['intent_ttl_seconds'])
    intent.authorization_context = result[4]
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_fact(request, actor, epoch, operation):
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=operation)
    if (intent.target_key != request.path.removeprefix('/api/v2') or intent.request_id != headers.request_id
            or str(intent.generation) != headers.generation
            or compute_digest(operation, intent.target_key, '2.0.0', fact_input(operation, request.data)) != intent.payload_sha256):
        raise ApiError('ARCHIVE_FACT_INTENT_MISMATCH', 409)
    result = scope(actor, epoch, intent, mutating=intent.status != 'committed')
    if intent.status != 'committed':
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('ARCHIVE_FACT_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('ARCHIVE_FACT_RESULT_UNKNOWN', 409)
        if intent.authorization_context != result[4]:
            raise ApiError('ARCHIVE_FACT_AUTHORIZATION_CHANGED', 403)
    return intent, result
