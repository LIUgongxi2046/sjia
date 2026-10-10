from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.conf import settings
from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError
from assessment.service import assessment_payload, checked_task, prepare_assessment_scope, read_assessment_task
from audit.models import AuditEvent
from configurations.models import ConfigVersion
from requests.evaluation_datasets import EvaluationHeaders, EvaluationRetryInput, evaluation_identity, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import NORMALIZATION_VERSION, compute_digest, prepare_intent
from retention.models import RetentionPolicy
from tasks.models import Task


OPERATIONS = ('assessment_v2_create',)


class AssessmentIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['assessment_v2_create']
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class AssessmentRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Literal['assessment_v2_create']
    target_key: str = Field(min_length=1, max_length=256)


def assessment_target(target):
    if not target.startswith('/') or any(value in target for value in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != 'assessment_v2_create' or reverse('assessment_v2_create', kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['patient_id']


def prepared_scope(actor, epoch, target, payload):
    patient, _, runtime, _, _, context = prepare_assessment_scope(patient_id=assessment_target(target), actor=actor,
        auth_epoch=epoch, environment=settings.SJIA_ENVIRONMENT, payload=payload)
    rule = ConfigVersion.objects.select_for_update().get(id=payload['rule_id'])
    if rule.source_revision_id is None or rule.source_revision.retention_policy_id is None:
        raise ApiError('ASSESSMENT_RETENTION_UNAVAILABLE', 409)
    retention = RetentionPolicy.objects.select_for_update().get(id=rule.source_revision.retention_policy_id)
    if (retention.status != 'active' or retention.approved_at is None or retention.approved_by_id is None
            or not retention.evidence_ref.strip()):
        raise ApiError('ASSESSMENT_RETENTION_UNAVAILABLE', 409)
    return patient, retention, runtime, {**context, 'retention_id': str(retention.id), 'retention_version': str(retention.version)}


def checked_payload(intent):
    payload = assessment_payload(intent.recovery_payload)
    if (intent.operation_id != 'assessment_v2_create' or intent.patient_id != assessment_target(intent.target_key)
            or intent.normalization_version != NORMALIZATION_VERSION
            or compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256):
        raise ApiError('ASSESSMENT_RECOVERY_INVALID', 409)
    return payload


def committed_task(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.actor_id != intent.actor_id
            or saved.patient_id != intent.patient_id or saved.request_id != intent.request_id or saved.action != 'assessment_v2_create'
            or saved.target_key != intent.target_key or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version or saved.resource_type != 'task'
            or saved.expected_version != 1 or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('ASSESSMENT_RECEIPT_INVALID', 409)
    task = Task.objects.select_for_update().get(id=saved.resource_id, actor_id=intent.actor_id, patient_id=intent.patient_id,
        request_row_id=saved.id, schema_version='static-assessment-task-2')
    checked_task(task)
    if (task.input_refs['payload'] != checked_payload(intent)
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id, action='assessment_v2_create',
                object_type='task', object_id=task.id, object_version='1', request_id=intent.request_id,
                outcome='committed', occurred_at=saved.completed_at, retention_policy_id=intent.retention_policy_id).exists()):
        raise ApiError('ASSESSMENT_RECEIPT_INVALID', 409)
    return task


def authorize_intent(actor, epoch, intent):
    if intent.status == 'committed':
        task = committed_task(intent)
        read_assessment_task(task_id=task.id, actor=actor, auth_epoch=epoch, environment=settings.SJIA_ENVIRONMENT, action='execute_assessment')
        return None, None
    _, retention, runtime, context = prepared_scope(actor, epoch, intent.target_key, checked_payload(intent))
    if retention.id != intent.retention_policy_id:
        raise ApiError('ASSESSMENT_RETENTION_CHANGED', 409)
    return runtime, context


def request_view(intent, context):
    unknown = intent.write_request_id is None and matching_request(intent)
    retry = (not unknown and intent.write_request_id is None and (intent.status == 'failed'
        or intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != context)))
    task = committed_task(intent) if intent.status == 'committed' else None
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if unknown else {'prepared': 'pending', 'failed': 'failed', 'committed': 'committed'}[intent.status],
        'may_retry': retry, 'error_code': intent.error_code, 'generation': str(intent.generation),
        'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256,
        'assessment_input': checked_payload(intent),
        'receipt': {'schema_version': '2.0.0', 'resource_type': 'task', 'resource_id': str(task.id), 'version': '1',
            'saved_at': intent.completed_at.isoformat(), 'request_id': str(intent.request_id), 'task_id': str(task.id)} if task is not None else None}


def prepare_assessment_intent(request):
    actor, epoch = evaluation_identity(request)
    data = AssessmentIntentInput.model_validate(request.data)
    payload = assessment_payload(data.payload)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id='assessment_v2_create',
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest('assessment_v2_create', data.target_key, '2.0.0', payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorize_intent(actor, epoch, existing)
        return intent_view(existing)
    patient, retention, runtime, context = prepared_scope(actor, epoch, data.target_key, payload)
    return intent_view(prepare_intent(actor=actor, patient=patient, operation_id='assessment_v2_create', target_key=data.target_key,
        request_id=data.request_id, payload=payload, schema_version='2.0.0', retention_policy=retention,
        authorization_context=context, recovery_payload=payload, ttl_seconds=runtime.payload['api']['intent_ttl_seconds']))


def read_assessment_request(request, intent_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('ASSESSMENT_QUERY_INVALID', 422)
        lookup = AssessmentRequestLookup.model_validate(dict(request.query_params.items()))
        assessment_target(lookup.target_key)
        intent = WriteIntent.objects.select_for_update().filter(actor=actor, request_id=intent_id,
            operation_id='assessment_v2_create', target_key=lookup.target_key).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=actor, operation_id='assessment_v2_create').first()
    if intent is None:
        raise ApiError('ASSESSMENT_REQUEST_NOT_FOUND', 404)
    _, context = authorize_intent(actor, epoch, intent)
    return request_view(intent, context)


def retry_assessment_intent(request, intent_id):
    actor, epoch = evaluation_identity(request)
    data = EvaluationRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id='assessment_v2_create')
    runtime, context = authorize_intent(actor, epoch, intent)
    if not request_view(intent, context)['may_retry'] or str(intent.generation) != data.expected_generation:
        raise ApiError('ASSESSMENT_RETRY_UNAVAILABLE', 409)
    if intent.generation >= 9223372036854775807:
        raise ApiError('ASSESSMENT_GENERATION_INVALID', 409)
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.generation += 1
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.authorization_context = context
    intent.save(update_fields=['status', 'generation', 'error_code', 'completed_at', 'prepared_at', 'expires_at', 'authorization_context'])
    return intent_view(intent)


def submitted_intent(request, actor, epoch):
    headers = EvaluationHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    assessment_target(target)
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id='assessment_v2_create')
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest('assessment_v2_create', target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('ASSESSMENT_INTENT_MISMATCH', 409)
    _, context = authorize_intent(actor, epoch, intent)
    if intent.status != 'committed':
        if intent.authorization_context != context:
            raise ApiError('ASSESSMENT_AUTHORIZATION_CHANGED', 403)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('ASSESSMENT_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('ASSESSMENT_RESULT_UNKNOWN', 409)
    return intent
