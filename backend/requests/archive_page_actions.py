from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone

from api.base import ApiError
from archives.models import ArchiveBatch, ArchivePage
from archives.page_actions import OPERATIONS, action_input, action_scope
from audit.models import AuditEvent
from files.purpose_schemas import PurposeWriteHeaders
from requests.archive_documents import family_identity, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import compute_digest, fail_intent, prepare_intent, retry_intent
from tasks.models import Task


def target_identifier(operation, target):
    if not target.startswith('/') or '?' in target or '#' in target or '://' in target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['batch_id' if operation == 'archive_batch_process' else 'page_id']


def payload(intent):
    value = action_input(intent.operation_id, intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', value) != intent.payload_sha256:
        raise ApiError('ARCHIVE_PAGE_ACTION_RECOVERY_INVALID', 409)
    return value


def scope(actor, epoch, intent, *, mutating=False):
    result = action_scope(actor, epoch, intent.operation_id, target_identifier(intent.operation_id, intent.target_key),
        payload(intent), mutating=mutating)
    if result[1].id != intent.patient_id:
        raise ApiError('ARCHIVE_PAGE_ACTION_SCOPE_INVALID', 409)
    return result


def receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.request_id != intent.request_id or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('ARCHIVE_PAGE_ACTION_RECEIPT_INVALID', 409)
    expected_type = 'archive_batch' if intent.operation_id == 'archive_batch_process' else 'archive_page'
    if saved.resource_type != expected_type or saved.resource_id != target_identifier(intent.operation_id, intent.target_key):
        raise ApiError('ARCHIVE_PAGE_ACTION_RECEIPT_INVALID', 409)
    original = payload(intent)
    expected_version = int(original['expected_version' if expected_type == 'archive_batch' else 'expected_status_version']) + 1
    if saved.expected_version != expected_version:
        raise ApiError('ARCHIVE_PAGE_ACTION_RECEIPT_INVALID', 409)
    if intent.operation_id != 'archive_page_cancel':
        tasks = list(Task.objects.filter(request_row=saved, patient_id=intent.patient_id, actor_id=intent.actor_id,
            schema_version='archive-page-1', task_type='ocr'))
        pages = original['page_ids'] if intent.operation_id == 'archive_batch_process' else [str(saved.resource_id)]
        if len(tasks) != len(pages) or {task.input_refs['archive_page_id'] for task in tasks} != set(pages):
            raise ApiError('ARCHIVE_PAGE_ACTION_RECEIPT_INVALID', 409)
    if not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
            action=intent.operation_id, object_type=expected_type, object_id=saved.resource_id,
            object_version=str(saved.expected_version), request_id=intent.request_id,
            occurred_at=saved.completed_at, outcome='committed').exists():
        raise ApiError('ARCHIVE_PAGE_ACTION_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': expected_type, 'resource_id': str(saved.resource_id),
        'version': str(saved.expected_version), 'saved_at': saved.completed_at.isoformat(),
        'request_id': str(saved.request_id), 'task_id': None}


def prepare_action_intent(request):
    actor, epoch = family_identity(request)
    data = request.data
    if not isinstance(data, dict) or set(data) != {'operation_id', 'target_key', 'request_id', 'schema_version', 'payload'}:
        raise ApiError('ARCHIVE_PAGE_ACTION_INPUT_INVALID', 422)
    if data['operation_id'] not in OPERATIONS or data['schema_version'] != '2.0.0':
        raise ApiError('ARCHIVE_PAGE_ACTION_INPUT_INVALID', 422)
    value = action_input(data['operation_id'], data['payload'])
    request_id = UUID(data['request_id'])
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data['operation_id'],
        target_key=data['target_key'], request_id=request_id).first()
    if existing:
        if compute_digest(data['operation_id'], data['target_key'], '2.0.0', value) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        scope(actor, epoch, existing)
        return intent_view(existing)
    actor, patient, batch, pages, _, runtime, context = action_scope(actor, epoch, data['operation_id'],
        target_identifier(data['operation_id'], data['target_key']), value, mutating=True)
    from files.models import FileObject

    retention = FileObject.objects.get(id=pages[0].file_id, patient=patient).retention_policy
    intent = prepare_intent(actor=actor, patient=patient, operation_id=data['operation_id'], target_key=data['target_key'],
        request_id=request_id, schema_version='2.0.0', payload=value, recovery_payload=value,
        authorization_context=context, retention_policy=retention, ttl_seconds=runtime.payload['api']['intent_ttl_seconds'])
    return intent_view(intent)


def request_view(intent, context):
    uncertain = intent.write_request_id is None and matching_request(intent)
    retry = not uncertain and intent.write_request_id is None and (intent.status == 'failed'
        or intent.status == 'prepared' and (intent.expires_at <= timezone.now()
            or any(context.get(key) != intent.authorization_context.get(key) for key in context)))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'prepared_at': intent.prepared_at.isoformat(),
        'target_key': intent.target_key, 'state': 'unknown' if uncertain else
            {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': retry, 'receipt': receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'batch_action_input': payload(intent)}


def read_action_request(request, intent_id):
    actor, epoch = family_identity(request)
    query = request.query_params
    if query:
        if (set(query) != {'lookup', 'operation_id', 'target_key'} or any(len(query.getlist(key)) != 1 for key in query)
                or query['lookup'] != 'request_id' or query['operation_id'] not in OPERATIONS):
            raise ApiError('ARCHIVE_PAGE_ACTION_QUERY_INVALID', 422)
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, request_id=intent_id,
            operation_id=query['operation_id'], target_key=query['target_key']).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, id=intent_id, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('ARCHIVE_PAGE_ACTION_REQUEST_NOT_FOUND', 404)
    return request_view(intent, scope(actor, epoch, intent)[-1])


def retry_action_intent(request, intent_id):
    actor, epoch = family_identity(request)
    if not isinstance(request.data, dict) or set(request.data) != {'expected_generation'}:
        raise ApiError('ARCHIVE_PAGE_ACTION_INPUT_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    context = scope(actor, epoch, intent)[-1]
    if request.data['expected_generation'] != str(intent.generation) or not request_view(intent, context)['may_retry']:
        raise ApiError('ARCHIVE_PAGE_ACTION_RETRY_NOT_AVAILABLE', 409)
    result = scope(actor, epoch, intent, mutating=True)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='ARCHIVE_PAGE_ACTION_PREPARATION_CHANGED')
    retry_intent(intent=intent, expected_generation=int(intent.generation), ttl_seconds=result[-2].payload['api']['intent_ttl_seconds'])
    intent.authorization_context = result[-1]
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_action(request, actor, epoch, operation):
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=operation)
    if (intent.target_key != request.path.removeprefix('/api/v2') or intent.request_id != headers.request_id
            or str(intent.generation) != headers.generation
            or compute_digest(operation, intent.target_key, '2.0.0', action_input(operation, request.data)) != intent.payload_sha256):
        raise ApiError('ARCHIVE_PAGE_ACTION_INTENT_MISMATCH', 409)
    result = scope(actor, epoch, intent, mutating=intent.status != 'committed')
    if intent.status != 'committed':
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('ARCHIVE_PAGE_ACTION_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('ARCHIVE_PAGE_ACTION_RESULT_UNKNOWN', 409)
        if intent.authorization_context != result[-1]:
            raise ApiError('ARCHIVE_PAGE_ACTION_AUTHORIZATION_CHANGED', 403)
    return intent, result
