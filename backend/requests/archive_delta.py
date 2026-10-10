from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone

from api.base import ApiError
from archives.models import DeltaSummary
from audit.models import AuditEvent
from files.purpose_schemas import PurposeWriteHeaders
from requests.archive_documents import family_identity, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import compute_digest, fail_intent, prepare_intent, retry_intent
from summaries.delta import material_scope
from summaries.delta_schemas import checked_delta
from tasks.models import Task

OPERATIONS = ('archive_delta_create',)


def checked_payload(intent):
    value = checked_delta('Input', intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', value) != intent.payload_sha256:
        raise ApiError('ARCHIVE_DELTA_RECOVERY_INVALID', 409)
    return value


def target_scope(request, actor, epoch, target, payload, frozen=None, *, generating=False):
    if not target.startswith('/') or '?' in target or '#' in target or '://' in target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != 'archive_delta_create' or reverse(route.url_name, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return material_scope(request, actor, epoch, route.kwargs['batch_id'], payload, frozen, generating=generating)


def authorized(request, actor, epoch, intent, *, generating=False):
    patient, retention, context, materials, facts, relations = target_scope(request, actor, epoch, intent.target_key,
        checked_payload(intent), intent.authorization_context, generating=generating)
    if patient.id != intent.patient_id or retention.id != intent.retention_policy_id:
        raise ApiError('ARCHIVE_DELTA_INTENT_SCOPE_MISMATCH', 409)
    return patient, context, materials, facts, relations


def delta_receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.actor_id != intent.actor_id
            or saved.patient_id != intent.patient_id or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.request_id != intent.request_id or saved.payload_sha256 != intent.payload_sha256
            or saved.resource_type != 'archive_delta' or saved.expected_version != 1
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('ARCHIVE_DELTA_RECEIPT_INVALID', 409)
    result = DeltaSummary.objects.get(id=saved.resource_id, owner_id=intent.actor_id, patient_id=intent.patient_id)
    task = Task.objects.get(id=result.task_id, actor_id=intent.actor_id, patient_id=intent.patient_id,
        request_row=saved, schema_version='archive-delta-1', status__in=['completed', 'superseded'])
    if (task.input_refs != intent.authorization_context or compute_digest(intent.operation_id, intent.target_key, '2.0.0', checked_payload(intent)) != intent.payload_sha256
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
                action=intent.operation_id, object_type='archive_delta', object_id=result.id, object_version='1',
                request_id=intent.request_id, occurred_at=saved.completed_at, outcome='committed').exists()):
        raise ApiError('ARCHIVE_DELTA_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': 'archive_delta', 'resource_id': str(result.id), 'version': '1',
        'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id), 'task_id': str(task.id)}


def request_view(intent, context):
    uncertain = intent.write_request_id is None and matching_request(intent)
    state = 'unknown' if uncertain else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status]
    retry = not uncertain and intent.write_request_id is None and (intent.status == 'failed' or intent.status == 'prepared'
        and (intent.expires_at <= timezone.now() or intent.authorization_context != context))
    return checked_delta('Request', {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'state': state, 'may_retry': retry, 'receipt': delta_receipt(intent) if state == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'delta_input': checked_payload(intent)})


def prepare_delta_intent(request):
    actor, epoch = family_identity(request)
    value = checked_delta('Intent', request.data)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=value['operation_id'],
        target_key=value['target_key'], request_id=value['request_id']).first()
    if existing is not None:
        if compute_digest(value['operation_id'], value['target_key'], '2.0.0', value['payload']) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorized(request, actor, epoch, existing)
        if existing.status == 'committed':
            delta_receipt(existing)
        return intent_view(existing)
    patient, retention, context, _, _, _ = target_scope(request, actor, epoch, value['target_key'], value['payload'], generating=True)
    intent = prepare_intent(actor=actor, patient=patient, operation_id=value['operation_id'], target_key=value['target_key'],
        request_id=UUID(value['request_id']), payload=value['payload'], schema_version='2.0.0', retention_policy=retention,
        authorization_context=context, recovery_payload=value['payload'])
    return intent_view(intent)


def read_delta_request(request, intent_id):
    actor, epoch = family_identity(request)
    query = request.query_params
    if query:
        if (set(query) != {'lookup', 'operation_id', 'target_key'} or any(len(query.getlist(key)) != 1 for key in query)
                or query['lookup'] != 'request_id' or query['operation_id'] != 'archive_delta_create'):
            raise ApiError('ARCHIVE_DELTA_QUERY_INVALID', 422)
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, request_id=intent_id,
            operation_id=query['operation_id'], target_key=query['target_key']).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, id=intent_id, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('ARCHIVE_DELTA_REQUEST_NOT_FOUND', 404)
    _, context, _, _, _ = authorized(request, actor, epoch, intent)
    return request_view(intent, context)


def retry_delta_intent(request, intent_id):
    actor, epoch = family_identity(request)
    if not isinstance(request.data, dict) or set(request.data) != {'expected_generation'}:
        raise ApiError('ARCHIVE_DELTA_INPUT_INVALID', 422)
    generation = request.data['expected_generation']
    if not isinstance(generation, str) or not generation.isascii() or not generation.isdecimal() or generation.startswith('0') or len(generation) > 19:
        raise ApiError('ARCHIVE_DELTA_INPUT_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    _, context, _, _, _ = authorized(request, actor, epoch, intent, generating=True)
    if not request_view(intent, context)['may_retry'] or str(intent.generation) != generation:
        raise ApiError('ARCHIVE_DELTA_RETRY_NOT_AVAILABLE', 409)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='ARCHIVE_DELTA_PREPARATION_CHANGED')
    retry_intent(intent=intent, expected_generation=int(generation))
    intent.authorization_context = context
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_delta_intent(request, actor, epoch):
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id='archive_delta_create')
    if intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation or compute_digest(
            intent.operation_id, target, '2.0.0', checked_delta('Input', request.data)) != intent.payload_sha256:
        raise ApiError('ARCHIVE_DELTA_INTENT_MISMATCH', 409)
    patient, context, materials, facts, relations = authorized(request, actor, epoch, intent, generating=intent.status != 'committed')
    if intent.status != 'committed':
        if context != intent.authorization_context:
            raise ApiError('ARCHIVE_DELTA_PREPARATION_CHANGED', 409)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('ARCHIVE_DELTA_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('ARCHIVE_DELTA_RESULT_UNKNOWN', 409)
    return intent, patient, context, materials, facts, relations
