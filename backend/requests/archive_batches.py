from typing import Literal
from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError
from archives.batch_creation import batch_input, batch_write_identity, prepare_sources, received_sources
from archives.batches import batch_grants
from archives.models import ArchiveBatch, ArchivePage
from audit.models import AuditEvent
from authorization.models import AccessGrant
from files.purpose_schemas import PurposeWriteHeaders
from requests.archive_documents import family_identity, intent_view, matching_request
from requests.models import WriteIntent
from relations.models import Relation
from requests.service import NORMALIZATION_VERSION, compute_digest, fail_intent, prepare_intent, retry_intent


OPERATIONS = frozenset({'archive_batch_create', 'archive_batch_process', 'archive_page_retry', 'archive_page_cancel'})


class BatchWriteHeaders(PurposeWriteHeaders):
    pass


class BatchIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['archive_batch_create']
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class BatchRetryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_generation: str = Field(pattern=r'^[1-9][0-9]{0,18}$')


class BatchRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Literal['archive_batch_create']
    target_key: str = Field(min_length=1, max_length=256)


def target_patient(target):
    if not target.startswith('/') or '?' in target or '#' in target or '://' in target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != 'archive_batch_create' or reverse('archive_batch_create', kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['patient_id']


def checked_payload(intent):
    if intent.normalization_version != NORMALIZATION_VERSION or intent.recovery_payload is None:
        raise ApiError('ARCHIVE_BATCH_RECOVERY_UNAVAILABLE', 409)
    payload = batch_input(intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256:
        raise ApiError('ARCHIVE_BATCH_RECOVERY_INVALID', 409)
    return payload


def batch_receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.normalization_version != intent.normalization_version
            or saved.request_id != intent.request_id or saved.resource_type != 'archive_batch'
            or saved.expected_version != 1 or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('ARCHIVE_BATCH_RECEIPT_INVALID', 409)
    batch = ArchiveBatch.objects.get(id=saved.resource_id, patient_id=intent.patient_id)
    pages = list(ArchivePage.objects.filter(batch_id=batch.id, patient_id=intent.patient_id).order_by('ordinal'))
    expected_pages = [page_id for group in intent.authorization_context['source_refs'] for page_id in group['page_ids']]
    if (batch.submitted_by != intent.actor_id or batch.logical_request_id != intent.request_id
            or (str(batch.quota_policy_id) if batch.quota_policy_id is not None else None)
                != intent.authorization_context['configuration']['quota_id']
            or batch.intake_mode != checked_payload(intent).get('intake_mode', 'ocr')
            or not pages or [page.ordinal for page in pages] != list(range(1, len(pages) + 1))
            or [str(page.file_page_id) for page in pages] != expected_pages
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
                action=intent.operation_id, object_type='archive_batch', object_id=batch.id,
                object_version='1', request_id=intent.request_id, occurred_at=saved.completed_at, outcome='committed').exists()):
        raise ApiError('ARCHIVE_BATCH_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': 'archive_batch', 'resource_id': str(batch.id),
        'version': '1', 'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id), 'task_id': None}


def authorized_request(actor, epoch, intent):
    payload = checked_payload(intent)
    patient_id = target_patient(intent.target_key)
    actor, patient, gate = batch_write_identity(actor, epoch, patient_id)
    if patient.id != intent.patient_id:
        raise ApiError('ARCHIVE_BATCH_INTENT_SCOPE_MISMATCH', 409)
    if intent.status == 'committed':
        receipt = batch_receipt(intent)
        batch = ArchiveBatch.objects.get(id=receipt['resource_id'], patient_id=patient.id)
        pages = list(ArchivePage.objects.filter(batch_id=batch.id, patient_id=patient.id).order_by('ordinal'))
        grants = batch_grants(actor, patient, batch, pages)
        if not grants:
            raise ApiError('CASE_ACTION_DENIED', 403)
        list(AccessGrant.objects.select_for_update().filter(id__in=[grant.id for grant in grants]).order_by('id'))
        list(Relation.objects.select_for_update().filter(
            id__in=[grant.relation_id for grant in grants], patient_id=patient.id, guardian_id=actor.id).order_by('id'))
        if not batch_grants(actor, patient, batch, pages):
            raise ApiError('CASE_ACTION_DENIED', 403)
        return intent.authorization_context
    sources, _, context = received_sources(actor, patient, gate, payload, receive=True)
    if sources[0].retention_policy_id != intent.retention_policy_id:
        raise ApiError('ARCHIVE_BATCH_INTENT_SCOPE_MISMATCH', 409)
    context['configuration'] = intent.authorization_context['configuration']
    return context


def request_view(intent, context):
    unlinked = intent.write_request_id is None and matching_request(intent)
    state = 'unknown' if unlinked else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status]
    may_retry = (not unlinked and intent.write_request_id is None and (intent.status == 'failed'
        or (intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != context))))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(), 'state': state,
        'may_retry': may_retry, 'receipt': batch_receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'batch_input': checked_payload(intent)}


def prepare_batch_intent(request):
    if request.data.get('operation_id') != 'archive_batch_create':
        from requests.archive_page_actions import prepare_action_intent

        return prepare_action_intent(request)
    actor, epoch = family_identity(request)
    data = BatchIntentInput.model_validate(request.data)
    payload = batch_input(data.payload)
    patient_id = target_patient(data.target_key)
    actor, _, _ = batch_write_identity(actor, epoch, patient_id)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest(data.operation_id, data.target_key, '2.0.0', payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorized_request(actor, epoch, existing)
        return intent_view(existing)
    actor, patient, sources, _, _, runtime, context = prepare_sources(actor, epoch, patient_id, payload)
    intent = prepare_intent(actor=actor, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=payload, schema_version='2.0.0', retention_policy=sources[0].retention_policy,
        patient=patient, ttl_seconds=runtime.payload['api']['intent_ttl_seconds'],
        authorization_context=context, recovery_payload=payload)
    return intent_view(intent)


def read_batch_request(request, intent_id):
    actor, epoch = family_identity(request)
    operation = request.query_params.get('operation_id') if request.query_params else WriteIntent.objects.get(id=intent_id, actor=actor).operation_id
    if operation != 'archive_batch_create':
        from requests.archive_page_actions import read_action_request

        return read_action_request(request, intent_id)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('ARCHIVE_BATCH_QUERY_INVALID', 422)
        lookup = BatchRequestLookup.model_validate(dict(request.query_params.items()))
        intent = WriteIntent.objects.filter(request_id=intent_id, actor=actor,
            operation_id=lookup.operation_id, target_key=lookup.target_key).first()
    else:
        intent = WriteIntent.objects.filter(id=intent_id, actor=actor, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('ARCHIVE_BATCH_REQUEST_NOT_FOUND', 404)
    batch_write_identity(actor, epoch, target_patient(intent.target_key))
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor, operation_id__in=OPERATIONS)
    return request_view(intent, authorized_request(actor, epoch, intent))


def retry_batch_intent(request, intent_id):
    actor, epoch = family_identity(request)
    if WriteIntent.objects.get(id=intent_id, actor=actor).operation_id != 'archive_batch_create':
        from requests.archive_page_actions import retry_action_intent

        return retry_action_intent(request, intent_id)
    data = BatchRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    batch_write_identity(actor, epoch, target_patient(intent.target_key))
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor, operation_id__in=OPERATIONS)
    context = authorized_request(actor, epoch, intent)
    if (not request_view(intent, context)['may_retry'] or int(data.expected_generation) != intent.generation
            or intent.generation >= 9223372036854775807):
        raise ApiError('ARCHIVE_BATCH_RETRY_NOT_AVAILABLE', 409)
    _, _, sources, _, _, runtime, context = prepare_sources(actor, epoch, intent.patient_id, checked_payload(intent))
    if sources[0].retention_policy_id != intent.retention_policy_id:
        raise ApiError('ARCHIVE_BATCH_INTENT_SCOPE_MISMATCH', 409)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='ARCHIVE_BATCH_INTENT_EXPIRED' if intent.expires_at <= timezone.now()
            else 'ARCHIVE_BATCH_AUTHORIZATION_CHANGED')
    retry_intent(intent=intent, expected_generation=int(data.expected_generation),
        ttl_seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.authorization_context = context
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_batch_intent(request, actor, epoch):
    operation = 'archive_batch_create'
    headers = BatchWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    actor, patient, _ = batch_write_identity(actor, epoch, target_patient(target))
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=operation)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(operation, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('ARCHIVE_BATCH_INTENT_MISMATCH', 409)
    if intent.status == 'committed':
        authorized_request(actor, epoch, intent)
        return intent, patient, None, None
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise ApiError('ARCHIVE_BATCH_INTENT_EXPIRED', 409)
    if matching_request(intent):
        raise ApiError('ARCHIVE_BATCH_RESULT_UNKNOWN', 409)
    _, patient, _, pages, quota, _, context = prepare_sources(actor, epoch, patient.id, checked_payload(intent))
    if intent.patient_id != patient.id or intent.authorization_context != context:
        raise ApiError('ARCHIVE_BATCH_AUTHORIZATION_CHANGED', 403)
    return intent, patient, pages, quota
