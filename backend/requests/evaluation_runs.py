from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError
from archives.models import EvaluationRun
from audit.models import AuditEvent
from case_details.evaluation_run_inputs import lock_run_inputs, run_input
from case_details.evaluation_evidence import lock_evaluation_actor
from case_details.evaluation_runs import lock_run_read, run_snapshot, run_task
from requests.evaluation_datasets import EvaluationHeaders, EvaluationRetryInput, evaluation_identity, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import NORMALIZATION_VERSION, compute_digest, prepare_intent
from retention.models import RetentionPolicy


OPERATIONS = ('dataset_run_create',)


class EvaluationRunIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['dataset_run_create']
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class EvaluationRunRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Literal['dataset_run_create']
    target_key: str = Field(min_length=1, max_length=256)


def run_target(target):
    if not target.startswith('/') or any(value in target for value in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != 'dataset_run_create' or reverse('dataset_run_create', kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['dataset_id']


def prepared_run_scope(actor, epoch, target, payload):
    row, sources, runtime, _, snapshot, context = lock_run_inputs(
        dataset_id=run_target(target), payload=payload, actor=actor, auth_epoch=epoch)
    retention = RetentionPolicy.objects.select_for_update().get(id=sources.proofs['unit_manifest']['retention_policy_id'])
    if retention.status != 'active' or retention.approved_at is None or retention.approved_by_id is None or not retention.evidence_ref.strip():
        raise ApiError('DATASET_RETENTION_UNAVAILABLE', 409)
    context = {**context, 'retention_id': str(retention.id), 'retention_version': str(retention.version)}
    return retention, runtime, context


def checked_run_payload(intent):
    if intent.operation_id != 'dataset_run_create' or not isinstance(intent.recovery_payload, dict):
        raise ApiError('DATASET_RUN_RECOVERY_INVALID', 409)
    payload = run_input(intent.recovery_payload)
    if (intent.normalization_version != NORMALIZATION_VERSION
            or compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256):
        raise ApiError('DATASET_RUN_RECOVERY_INVALID', 409)
    return payload


def committed_run(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or intent.patient_id is not None or saved.patient_id is not None or saved.actor_id != intent.actor_id
            or saved.action != 'dataset_run_create' or saved.resource_type != 'evaluation_run' or saved.expected_version != 1
            or saved.request_id != intent.request_id or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.normalization_version != intent.normalization_version
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('DATASET_RUN_RECEIPT_INVALID', 409)
    run = EvaluationRun.objects.select_for_update().get(id=saved.resource_id)
    task = run_task(run)
    if (run.operator_id != intent.actor_id or task.request_row_id != saved.id
            or run_snapshot(run)['input'] != checked_run_payload(intent)
            or run_snapshot(run)['dataset_id'] != str(run_target(intent.target_key))
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, action='dataset_run_create',
                object_type='evaluation_run', object_id=run.id, object_version='1', request_id=intent.request_id,
                occurred_at=saved.completed_at, outcome='committed').exists()):
        raise ApiError('DATASET_RUN_RECEIPT_INVALID', 409)
    return run, task


def run_receipt(intent):
    run, task = committed_run(intent)
    return {'schema_version': '2.0.0', 'resource_type': 'evaluation_run', 'resource_id': str(run.id),
        'version': '1', 'saved_at': intent.completed_at.isoformat(), 'request_id': str(intent.request_id), 'task_id': str(task.id)}


def authorize_run_intent(actor, epoch, intent):
    payload = checked_run_payload(intent)
    if intent.status == 'committed':
        lock_evaluation_actor(actor, epoch, action='read_assessment')
        run, _ = committed_run(intent)
        lock_run_read(run_id=run.id, actor=actor, auth_epoch=epoch)
        return None, None
    retention, runtime, context = prepared_run_scope(actor, epoch, intent.target_key, payload)
    if retention.id != intent.retention_policy_id:
        raise ApiError('DATASET_RUN_INTENT_SCOPE_MISMATCH', 409)
    return runtime, context


def run_request_view(intent, context):
    uncertain = intent.write_request_id is None and matching_request(intent)
    retryable = (not uncertain and intent.write_request_id is None and (intent.status == 'failed'
        or intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != context)))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if uncertain else {'prepared': 'pending', 'failed': 'failed', 'committed': 'committed'}[intent.status],
        'may_retry': retryable, 'receipt': run_receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'evaluation_run_input': checked_run_payload(intent)}


def prepare_run_intent(request):
    actor, epoch = evaluation_identity(request)
    data = EvaluationRunIntentInput.model_validate(request.data)
    payload = run_input(data.payload)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest(data.operation_id, data.target_key, '2.0.0', payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorize_run_intent(actor, epoch, existing)
        return intent_view(existing)
    retention, runtime, context = prepared_run_scope(actor, epoch, data.target_key, payload)
    return intent_view(prepare_intent(actor=actor, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=payload, schema_version='2.0.0', retention_policy=retention,
        authorization_context=context, recovery_payload=payload, ttl_seconds=runtime.payload['api']['intent_ttl_seconds']))


def read_run_request(request, intent_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('DATASET_QUERY_INVALID', 422)
        lookup = EvaluationRunRequestLookup.model_validate(dict(request.query_params.items()))
        run_target(lookup.target_key)
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, request_id=intent_id,
            operation_id=lookup.operation_id, target_key=lookup.target_key).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=actor, operation_id='dataset_run_create').first()
    if intent is None:
        raise ApiError('DATASET_REQUEST_NOT_FOUND', 404)
    _, context = authorize_run_intent(actor, epoch, intent)
    return run_request_view(intent, context)


def retry_run_intent(request, intent_id):
    actor, epoch = evaluation_identity(request)
    data = EvaluationRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id='dataset_run_create')
    runtime, context = authorize_run_intent(actor, epoch, intent)
    if not run_request_view(intent, context)['may_retry'] or str(intent.generation) != data.expected_generation:
        raise ApiError('DATASET_RETRY_UNAVAILABLE', 409)
    if intent.generation >= 9223372036854775807:
        raise ApiError('DATASET_GENERATION_INVALID', 409)
    now = timezone.now()
    intent.status, intent.generation, intent.error_code, intent.completed_at = 'prepared', intent.generation + 1, None, None
    intent.prepared_at, intent.expires_at = now, now + timedelta(seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.authorization_context = context
    intent.save(update_fields=['status', 'generation', 'error_code', 'completed_at', 'prepared_at', 'expires_at', 'authorization_context'])
    return intent_view(intent)


def submitted_run_intent(request, actor, epoch):
    headers = EvaluationHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    run_target(target)
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id='dataset_run_create')
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest('dataset_run_create', target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('DATASET_RUN_INTENT_MISMATCH', 409)
    _, context = authorize_run_intent(actor, epoch, intent)
    if intent.status != 'committed':
        if intent.authorization_context != context:
            raise ApiError('DATASET_RUN_AUTHORIZATION_CHANGED', 403)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('DATASET_RUN_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('DATASET_RUN_RESULT_UNKNOWN', 409)
    return intent
