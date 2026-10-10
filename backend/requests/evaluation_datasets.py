from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError, current_principal
from archives.models import EvaluationDatasetVersion
from audit.models import AuditEvent
from case_details.evaluation_datasets import checked_dataset, current_dataset, operation_input
from case_details.evaluation_evidence import lock_evaluation_actor, lock_evaluation_sources
from case_details.evaluation_objects import lock_evaluation_objects
from case_details.evaluation_units import Version
from case_details.evaluation_views import evaluation_runtime
from configurations.service import compute_checksum
from requests.models import WriteIntent, WriteRequest
from requests.service import NORMALIZATION_VERSION, compute_digest, prepare_intent
from retention.models import RetentionPolicy


OPERATIONS = ('dataset_create', 'dataset_partition', 'dataset_label', 'dataset_freeze')
Operation = Literal['dataset_create', 'dataset_partition', 'dataset_label', 'dataset_freeze']


class EvaluationIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Operation
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class EvaluationRetryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_generation: Version


class EvaluationHeaders(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent_id: UUID
    request_id: UUID
    generation: Version


class EvaluationRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Operation
    target_key: str = Field(min_length=1, max_length=256)


def evaluation_identity(request):
    actor, epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'support':
        raise ApiError('SUPPORT_AUDIENCE_REQUIRED', 403)
    return actor, epoch


def target_route(operation, target):
    if operation not in OPERATIONS or not target.startswith('/') or any(part in target for part in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route


def target_scope(actor, epoch, operation, target, payload, *, check_version=True, receipt_row=None):
    data = operation_input(operation, payload)
    route = target_route(operation, target)
    actor, gate, _ = lock_evaluation_actor(actor, epoch,
        action='verify_field' if operation == 'dataset_label' else 'manage_data_use')
    if receipt_row is not None:
        row = receipt_row
        sources, objects, _, _ = checked_dataset(row, actor, epoch)
        if operation != 'dataset_create' and row.dataset_id != route.kwargs['dataset_id']:
            raise ApiError('DATASET_INTENT_SCOPE_MISMATCH', 409)
    elif operation == 'dataset_create':
        sources = lock_evaluation_sources(payload=data, actor=actor, auth_epoch=epoch)
        objects = lock_evaluation_objects(sources=sources, actor=actor)
        row = None if data['dataset_id'] is None else current_dataset(data['dataset_id'])
        if row is not None:
            checked_dataset(row, actor, epoch)
    else:
        row = current_dataset(route.kwargs['dataset_id'])
        sources, objects, _, _ = checked_dataset(row, actor, epoch)
    if row is not None and check_version and row.version != int(data['expected_version']):
        raise ApiError('VERSION_CONFLICT', 409)
    if receipt_row is None and operation in ('dataset_partition', 'dataset_label'):
        if data['patient_id'] not in {str(item.id) for item in sources.manifest.entries('patients')}:
            raise ApiError('DATASET_PATIENT_SCOPE_INVALID', 403)
    retention = RetentionPolicy.objects.select_for_update().get(id=sources.proofs['unit_manifest']['retention_policy_id'])
    if retention.status != 'active' or retention.approved_at is None or retention.approved_by_id is None or not retention.evidence_ref:
        raise ApiError('DATASET_RETENTION_UNAVAILABLE', 409)
    runtime = evaluation_runtime(gate)
    context = {'audience': 'support', 'actor_id': str(actor.id), 'auth_epoch': str(actor.auth_epoch),
        'environment': gate.environment, 'gate_version': str(gate.version),
        'dataset_version_id': str(row.id) if row else None, 'dataset_checksum': row.checksum if row else None,
        'source_proofs': sources.proofs, 'object_proofs_sha256': compute_checksum(objects),
        'retention_id': str(retention.id), 'retention_version': str(retention.version),
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum}
    return row, sources, retention, runtime, context


def checked_payload(intent):
    if intent.operation_id not in OPERATIONS or not isinstance(intent.recovery_payload, dict):
        raise ApiError('DATASET_RECOVERY_INVALID', 409)
    payload = operation_input(intent.operation_id, intent.recovery_payload)
    if (intent.normalization_version != NORMALIZATION_VERSION
            or compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256):
        raise ApiError('DATASET_RECOVERY_INVALID', 409)
    return payload


def receipt_row(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id is not None or intent.patient_id is not None
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.request_id != intent.request_id or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version or saved.resource_type != 'evaluation_dataset'
            or saved.expected_version is None or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('DATASET_RECEIPT_INVALID', 409)
    row = EvaluationDatasetVersion.objects.get(dataset_id=saved.resource_id, version=saved.expected_version)
    if (row.created_by != intent.actor_id or not AuditEvent.objects.filter(actor_id=intent.actor_id,
            patient_id=None, action=intent.operation_id, object_type='evaluation_dataset', object_id=row.dataset_id,
            object_version=str(row.version), request_id=intent.request_id,
            occurred_at=saved.completed_at, outcome='committed').exists()):
        raise ApiError('DATASET_RECEIPT_INVALID', 409)
    return row


def dataset_receipt(intent):
    row = receipt_row(intent)
    return {'schema_version': '2.0.0', 'resource_type': 'evaluation_dataset', 'resource_id': str(row.dataset_id),
        'version': str(row.version), 'saved_at': intent.completed_at.isoformat(),
        'request_id': str(intent.request_id), 'task_id': None}


def authorized_intent(actor, epoch, intent, *, check_version=False):
    payload = checked_payload(intent)
    result = target_scope(actor, epoch, intent.operation_id, intent.target_key, payload,
        check_version=check_version and intent.status != 'committed',
        receipt_row=receipt_row(intent) if intent.status == 'committed' else None)
    if result[2].id != intent.retention_policy_id:
        raise ApiError('DATASET_INTENT_SCOPE_MISMATCH', 409)
    return result


def intent_view(intent):
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation),
        'status': intent.status, 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256}


def matching_request(intent):
    return WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
        target_key=intent.target_key, request_id=intent.request_id).exists()


def request_view(intent, row, context):
    payload = checked_payload(intent)
    uncertain = intent.write_request_id is None and matching_request(intent)
    stale_version = row is not None and payload.get('expected_version') != str(row.version)
    retryable = (not uncertain and not stale_version and intent.write_request_id is None
        and (intent.status == 'failed' or (intent.status == 'prepared'
            and (intent.expires_at <= timezone.now() or intent.authorization_context != context))))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if uncertain else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': retryable, 'receipt': dataset_receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'dataset_input': payload}


def prepare_evaluation_intent(request):
    actor, epoch = evaluation_identity(request)
    data = EvaluationIntentInput.model_validate(request.data)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest(data.operation_id, data.target_key, '2.0.0', data.payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorized_intent(actor, epoch, existing)
        return intent_view(existing)
    _, _, retention, runtime, context = target_scope(actor, epoch, data.operation_id, data.target_key, data.payload)
    intent = prepare_intent(actor=actor, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=data.payload, schema_version='2.0.0', retention_policy=retention,
        authorization_context=context, recovery_payload=data.payload,
        ttl_seconds=runtime.payload['api']['intent_ttl_seconds'])
    return intent_view(intent)


def read_evaluation_request(request, intent_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('DATASET_QUERY_INVALID', 422)
        lookup = EvaluationRequestLookup.model_validate(dict(request.query_params.items()))
        target_route(lookup.operation_id, lookup.target_key)
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, request_id=intent_id,
            operation_id=lookup.operation_id, target_key=lookup.target_key).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=actor, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('DATASET_REQUEST_NOT_FOUND', 404)
    row, _, _, _, context = authorized_intent(actor, epoch, intent)
    return request_view(intent, row, context)


def retry_evaluation_intent(request, intent_id):
    actor, epoch = evaluation_identity(request)
    data = EvaluationRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    row, _, _, runtime, context = authorized_intent(actor, epoch, intent, check_version=True)
    if not request_view(intent, row, context)['may_retry'] or str(intent.generation) != data.expected_generation:
        raise ApiError('DATASET_RETRY_UNAVAILABLE', 409)
    if intent.generation >= 9223372036854775807:
        raise ApiError('DATASET_GENERATION_INVALID', 409)
    now = timezone.now()
    intent.status, intent.generation, intent.error_code, intent.completed_at = 'prepared', intent.generation + 1, None, None
    intent.prepared_at, intent.expires_at = now, now + timedelta(seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.authorization_context = context
    intent.save(update_fields=['status', 'generation', 'error_code', 'completed_at', 'prepared_at', 'expires_at', 'authorization_context'])
    return intent_view(intent)


def submitted_evaluation_intent(request, actor, epoch, operation):
    headers = EvaluationHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    target_route(operation, target)
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=operation)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(operation, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('DATASET_INTENT_MISMATCH', 409)
    _, _, _, _, context = authorized_intent(actor, epoch, intent, check_version=True)
    if intent.status != 'committed':
        if intent.authorization_context != context:
            raise ApiError('DATASET_AUTHORIZATION_CHANGED', 403)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('DATASET_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('DATASET_RESULT_UNKNOWN', 409)
    return intent
