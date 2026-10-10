from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.conf import settings
from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError
from audit.models import AuditEvent
from requests.evaluation_datasets import EvaluationHeaders, EvaluationRetryInput, evaluation_identity, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import NORMALIZATION_VERSION, compute_digest, prepare_intent
from verification.models import Verification, VerificationField, VerificationRequest
from verification.workflow import prepare_verification_scope, read_verification_resource, verification_payload


OPERATIONS = ('verification_v2_request_create', 'verification_v2_submit', 'verification_v2_revoke')
Operation = Literal['verification_v2_request_create', 'verification_v2_submit', 'verification_v2_revoke']


class VerificationIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Operation
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class VerificationRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Operation
    target_key: str = Field(min_length=1, max_length=256)


class VerificationWriteHeaders(EvaluationHeaders):
    pass


class VerificationRetryInput(EvaluationRetryInput):
    pass


def verification_target(operation, target):
    if operation not in OPERATIONS or not target.startswith('/') or any(part in target for part in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs.get('verification_id')


def checked_payload(intent):
    payload = verification_payload(intent.operation_id, intent.recovery_payload)
    verification_target(intent.operation_id, intent.target_key)
    if (intent.normalization_version != NORMALIZATION_VERSION
            or compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256):
        raise ApiError('VERIFICATION_RECOVERY_CHANGED', 409)
    return payload


def committed_resource(intent):
    saved = intent.write_request
    payload = checked_payload(intent)
    kind = 'verification_request' if intent.operation_id == 'verification_v2_request_create' else 'verification'
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.actor_id != intent.actor_id
            or saved.patient_id != intent.patient_id or saved.request_id != intent.request_id or saved.action != intent.operation_id
            or saved.target_key != intent.target_key or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version or saved.resource_type != kind or saved.expected_version != 1
            or saved.completed_at is None or saved.completed_at != intent.completed_at
            or not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id, action=intent.operation_id,
                object_type=kind, object_id=saved.resource_id, object_version='1', request_id=intent.request_id,
                outcome='committed', occurred_at=saved.completed_at, retention_policy_id=intent.retention_policy_id).exists()):
        raise ApiError('VERIFICATION_RECEIPT_CHANGED', 409)
    row = (VerificationRequest if kind == 'verification_request' else Verification).objects.select_for_update().get(
        id=saved.resource_id, patient_id=intent.patient_id)
    if kind == 'verification_request':
        if (row.requested_by_id != intent.actor_id or str(row.record_version_id) != payload['record_version_id']
                or row.purpose != payload['purpose'] or str(row.assigned_to_id) != payload['assigned_to']):
            raise ApiError('VERIFICATION_RECEIPT_CHANGED', 409)
    elif intent.operation_id == 'verification_v2_submit':
        if (row.verifier_id != intent.actor_id or str(row.record_version_id) != payload['record_version_id']
                or str(row.policy_id) != payload['policy_id'] or row.purpose != payload['purpose']
                or str(row.evidence_source_id) != payload['evidence_source_id'] or row.evidence_ref != payload['evidence_ref']
                or {str(item.field_id): (item.result, item.reason) for item in VerificationField.objects.filter(verification_id=row.id)}
                    != {item['field_id']: (item['result'], item['reason']) for item in payload['fields']}):
            raise ApiError('VERIFICATION_RECEIPT_CHANGED', 409)
    elif (row.id != verification_target(intent.operation_id, intent.target_key) or row.revoked_at is None
            or row.revoked_by_id != intent.actor_id or row.revocation_reason != payload['reason']):
        raise ApiError('VERIFICATION_RECEIPT_CHANGED', 409)
    return row, kind


def authorize_intent(actor, epoch, intent):
    if intent.status == 'committed':
        row, kind = committed_resource(intent)
        read_verification_resource(kind, row.id, actor, epoch, settings.SJIA_ENVIRONMENT,
            action='revoke_verification' if intent.operation_id == 'verification_v2_revoke' else 'verify_field')
        return None, None
    scoped = prepare_verification_scope(intent.operation_id, verification_target(intent.operation_id, intent.target_key),
        checked_payload(intent), actor, epoch, settings.SJIA_ENVIRONMENT)
    if scoped[0].id != intent.patient_id or scoped[4].id != intent.retention_policy_id:
        raise ApiError('VERIFICATION_SCOPE_CHANGED', 409)
    return scoped[5], scoped[6]


def verification_request_view(intent, context):
    unknown = intent.write_request_id is None and matching_request(intent)
    retry = not unknown and intent.write_request_id is None and (intent.status == 'failed'
        or intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != context))
    resource = committed_resource(intent) if intent.status == 'committed' else None
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if unknown else {'prepared': 'pending', 'failed': 'failed', 'committed': 'committed'}[intent.status],
        'may_retry': bool(retry), 'error_code': intent.error_code, 'generation': str(intent.generation),
        'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256,
        'verification_input': checked_payload(intent),
        'receipt': None if resource is None else {'schema_version': '2.0.0', 'resource_type': resource[1],
            'resource_id': str(resource[0].id), 'version': '1', 'saved_at': intent.completed_at.isoformat(),
            'request_id': str(intent.request_id), 'task_id': None}}


def prepare_verification_intent(request):
    actor, epoch = evaluation_identity(request)
    data = VerificationIntentInput.model_validate(request.data)
    payload = verification_payload(data.operation_id, data.payload)
    target = verification_target(data.operation_id, data.target_key)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest(data.operation_id, data.target_key, '2.0.0', payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorize_intent(actor, epoch, existing)
        return intent_view(existing)
    patient, _, _, _, retention, runtime, context = prepare_verification_scope(data.operation_id, target,
        payload, actor, epoch, settings.SJIA_ENVIRONMENT)
    intent = prepare_intent(actor=actor, patient=patient, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=payload, schema_version='2.0.0', retention_policy=retention,
        ttl_seconds=runtime.payload['api']['intent_ttl_seconds'], authorization_context=context, recovery_payload=payload)
    return intent_view(intent)


def read_verification_request(request, intent_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        lookup = VerificationRequestLookup.model_validate(dict(request.query_params.items()))
        verification_target(lookup.operation_id, lookup.target_key)
        intent = WriteIntent.objects.select_for_update().get(actor=actor, request_id=intent_id,
            operation_id=lookup.operation_id, target_key=lookup.target_key)
    else:
        intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    _, context = authorize_intent(actor, epoch, intent)
    return verification_request_view(intent, context)


def retry_verification_intent(request, intent_id):
    actor, epoch = evaluation_identity(request)
    data = VerificationRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    runtime, context = authorize_intent(actor, epoch, intent)
    if not verification_request_view(intent, context)['may_retry'] or str(intent.generation) != data.expected_generation:
        raise ApiError('VERIFICATION_RETRY_UNAVAILABLE', 409)
    if intent.generation >= 9223372036854775807:
        raise ApiError('VERIFICATION_GENERATION_INVALID', 409)
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.generation += 1
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.authorization_context = context
    intent.save(update_fields=['status', 'generation', 'error_code', 'completed_at', 'prepared_at', 'expires_at', 'authorization_context'])
    return intent_view(intent)


def submitted_verification_intent(request, actor, epoch, operation):
    if request.query_params:
        raise ApiError('VERIFICATION_QUERY_INVALID', 422)
    headers = VerificationWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    verification_target(operation, target)
    verification_payload(operation, request.data)
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=operation)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(operation, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('VERIFICATION_INTENT_MISMATCH', 409)
    _, context = authorize_intent(actor, epoch, intent)
    if intent.status != 'committed':
        if intent.authorization_context != context:
            raise ApiError('VERIFICATION_AUTHORIZATION_CHANGED', 403)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('VERIFICATION_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('VERIFICATION_RESULT_UNKNOWN', 409)
    return intent
