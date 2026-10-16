"""Recoverable, exact-once intents for atomic archive regrouping."""
from django.urls import resolve, reverse
from django.utils import timezone

from api.base import ApiError
from archives.fact_candidates import validate_value
from archives.models import ArchiveRegrouping
from archives.regrouping import OPERATION, regroup_input, regroup_scope
from audit.models import AuditEvent
from configurations.service import compute_checksum
from files.purpose_schemas import PurposeWriteHeaders
from requests.archive_documents import family_identity, intent_view, matching_request, DocumentRetryInput
from requests.models import WriteIntent
from requests.service import compute_digest, prepare_intent, retry_intent, fail_intent

OPERATIONS = {OPERATION}


def target_patient(target):
    if not target.startswith('/') or '?' in target or '#' in target or '://' in target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != OPERATION or reverse(OPERATION, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['patient_id']


def checked_payload(intent):
    if intent.recovery_payload is None:
        raise ApiError('REGROUP_RECOVERY_UNAVAILABLE', 409)
    payload = regroup_input(intent.recovery_payload)
    if compute_digest(OPERATION, intent.target_key, '2.0.0', payload) != intent.payload_sha256:
        raise ApiError('REGROUP_RECOVERY_INVALID', 409)
    return payload


def receipt_row(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != OPERATION or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.request_id != intent.request_id
            or saved.resource_type != 'archive_regrouping' or saved.expected_version != 1
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('REGROUP_RECEIPT_INVALID', 409)
    row = ArchiveRegrouping.objects.get(id=saved.resource_id, patient_id=intent.patient_id,
        actor_id=intent.actor_id, request_id=intent.request_id)
    if (row.input_sha256 != intent.payload_sha256 or row.result_sha256 != compute_checksum(row.result_refs)
            or row.result_refs['regrouping_id'] != str(row.id)
            or row.result_refs['patient_id'] != str(row.patient_id)
            or row.result_refs['batch_id'] != str(row.batch_id)
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
                action=OPERATION, object_type='archive_regrouping', object_id=row.id, object_version='1',
                request_id=intent.request_id, occurred_at=saved.completed_at, outcome='committed').exists()):
        raise ApiError('REGROUP_RECEIPT_INVALID', 409)
    validate_value('RegroupResult', row.result_refs)
    return row


def regroup_receipt(intent):
    row = receipt_row(intent)
    return validate_value('RegroupReceipt', {'schema_version': '2.0.0', 'resource_type': 'archive_regrouping',
        'resource_id': str(row.id), 'version': '1', 'saved_at': intent.completed_at.isoformat(),
        'request_id': str(intent.request_id), 'task_id': None, 'result': row.result_refs})


def authorized_intent(request, actor, epoch, intent):
    row = receipt_row(intent) if intent.status == 'committed' else None
    scope = regroup_scope(request, actor, epoch, target_patient(intent.target_key), checked_payload(intent), committed=row)
    if scope['patient'].id != intent.patient_id or (row is None and scope['retention'].id != intent.retention_policy_id):
        raise ApiError('REGROUP_INTENT_SCOPE_MISMATCH', 409)
    return scope


def request_view(intent, scope):
    unlinked = intent.write_request_id is None and matching_request(intent)
    state = 'unknown' if unlinked else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status]
    retryable = not unlinked and intent.write_request_id is None and (intent.status == 'failed' or
        intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != scope['context']))
    return validate_value('RegroupRequestView', {'intent_id': str(intent.id), 'request_id': str(intent.request_id),
        'operation_id': OPERATION, 'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': state, 'may_retry': retryable, 'receipt': regroup_receipt(intent) if state == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'regroup_input': checked_payload(intent)})


def prepare_regroup_intent(request):
    actor, epoch = family_identity(request)
    data = validate_value('RegroupIntentInput', request.data)
    payload = regroup_input(data['payload'])
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=OPERATION,
        target_key=data['target_key'], request_id=data['request_id']).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest(OPERATION, data['target_key'], '2.0.0', payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorized_intent(request, actor, epoch, existing)
        return intent_view(existing)
    scope = regroup_scope(request, actor, epoch, target_patient(data['target_key']), payload)
    intent = prepare_intent(actor=actor, operation_id=OPERATION, target_key=data['target_key'],
        request_id=data['request_id'], payload=payload, schema_version='2.0.0', retention_policy=scope['retention'],
        patient=scope['patient'], authorization_context=scope['context'], recovery_payload=payload)
    return intent_view(intent)


def read_regroup_request(request, intent_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        if (set(request.query_params) != {'lookup', 'operation_id', 'target_key'}
                or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)
                or request.query_params['lookup'] != 'request_id' or request.query_params['operation_id'] != OPERATION):
            raise ApiError('REGROUP_QUERY_INVALID', 422)
        target_patient(request.query_params['target_key'])
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=OPERATION,
            request_id=intent_id, target_key=request.query_params['target_key']).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=OPERATION, id=intent_id).first()
    if intent is None:
        raise ApiError('REGROUP_REQUEST_NOT_FOUND', 404)
    return request_view(intent, authorized_intent(request, actor, epoch, intent))


def retry_regroup_intent(request, intent_id):
    actor, epoch = family_identity(request)
    data = DocumentRetryInput.model_validate(request.data)
    if not data.expected_generation.isascii() or not data.expected_generation.isdecimal() or data.expected_generation.startswith('0'):
        raise ApiError('REGROUP_INPUT_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id=OPERATION)
    scope = authorized_intent(request, actor, epoch, intent)
    if not request_view(intent, scope)['may_retry'] or int(data.expected_generation) != intent.generation:
        raise ApiError('REGROUP_RETRY_NOT_AVAILABLE', 409)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='REGROUP_INTENT_EXPIRED' if intent.expires_at <= timezone.now() else 'REGROUP_AUTHORIZATION_CHANGED')
    retry_intent(intent=intent, expected_generation=int(data.expected_generation))
    intent.authorization_context = scope['context']
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_regroup_intent(request, actor, epoch):
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=OPERATION)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(OPERATION, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('REGROUP_INTENT_MISMATCH', 409)
    scope = authorized_intent(request, actor, epoch, intent)
    if intent.status != 'committed':
        if intent.authorization_context != scope['context']:
            raise ApiError('REGROUP_AUTHORIZATION_CHANGED', 403)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('REGROUP_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('REGROUP_RESULT_UNKNOWN', 409)
    return intent, scope
