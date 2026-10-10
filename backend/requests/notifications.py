from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.db import transaction
from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError
from api.views_notifications import notification_identity, notification_scope
from audit.models import AuditEvent
from audit.service import write_audit
from assessment.provenance import entity_reference
from assessment.qualified_facts import lock_case_permissions
from notifications.models import NotificationAttempt
from notifications.native import authorize_native, checked_permit, checked_receipts, locked_scope
from notifications.reading import notification_record
from requests.evaluation_datasets import EvaluationHeaders, EvaluationRetryInput, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import NORMALIZATION_VERSION, commit_intent, compute_digest, prepare_intent


OPERATIONS = ('notification_v2_send',)


class NotificationSendInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_snapshot: str = Field(pattern=r'^[0-9a-f]{64}$')


class NotificationIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['notification_v2_send']
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: NotificationSendInput


class NotificationRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Literal['notification_v2_send']
    target_key: str = Field(min_length=1, max_length=256)


class NotificationCancelInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_generation: str = Field(pattern=r'^[1-9][0-9]*$')
    reason: str = Field(min_length=1, max_length=4096, pattern=r'\S')


def target_id(target):
    if not target.startswith('/') or any(part in target for part in ('?', '#', '://')):
        raise ApiError('NOTIFICATION_TARGET_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != 'notification_v2_send' or reverse('notification_v2_send', kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('NOTIFICATION_TARGET_INVALID', 422)
    return route.kwargs['recipient_id']


def read_scope(request, recipient_id):
    from alerting.models import AlertRecipient

    actor, gate, role = notification_identity(request, True)
    recipient = AlertRecipient.objects.select_for_update().get(id=recipient_id)
    patient, grants, rows = notification_scope(request, recipient.patient_id, True, actor, role)
    if not rows.filter(id=recipient.id).exists():
        raise ApiError('NOTIFICATION_RECIPIENT_SCOPE_DENIED', 403)
    return actor, gate, patient, recipient, role, grants


def send_scope(request, recipient_id, data):
    actor, gate, patient, recipient, role, _ = read_scope(request, recipient_id)
    record = notification_record(recipient)
    if data.expected_snapshot != record['snapshot_version']:
        raise ApiError('NOTIFICATION_RECORD_CHANGED', 409)
    _, _, _, _, _, runtime, _, policy, retention, _, _, context = locked_scope(recipient.id)
    grants = lock_case_permissions(actor, patient, 'read_case', policy.purpose, timezone.now(), whole_resource=True)
    return actor, patient, recipient, runtime, retention, {**context, 'initiator': entity_reference(actor),
        'initiator_role': entity_reference(role), 'initiator_grants': [entity_reference(row) for row in grants]}


def checked_input(intent):
    data = NotificationSendInput.model_validate(intent.recovery_payload)
    if (intent.normalization_version != NORMALIZATION_VERSION or intent.operation_id != 'notification_v2_send'
            or compute_digest(intent.operation_id, intent.target_key, '2.0.0', data.model_dump()) != intent.payload_sha256):
        raise ApiError('NOTIFICATION_REQUEST_PROOF_MISMATCH', 409)
    return data


def committed_attempt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.actor_id != intent.actor_id
            or saved.patient_id != intent.patient_id or saved.request_id != intent.request_id or saved.action != intent.operation_id
            or saved.target_key != intent.target_key or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version or saved.resource_type != 'notification_attempt'
            or saved.completed_at != intent.completed_at or saved.expected_version is None):
        raise ApiError('NOTIFICATION_REQUEST_PROOF_MISMATCH', 409)
    checked_input(intent)
    attempt = NotificationAttempt.objects.select_for_update().get(id=saved.resource_id, recipient_id=target_id(intent.target_key), patient_id=intent.patient_id)
    task = checked_permit(attempt)
    checked_receipts(attempt, task)
    if (saved.expected_version != attempt.attempt_number or not AuditEvent.objects.filter(actor_id=intent.actor_id,
            patient_id=intent.patient_id, action=intent.operation_id, object_type='notification_attempt', object_id=attempt.id,
            object_version=str(attempt.attempt_number), request_id=intent.request_id, outcome='committed',
            retention_policy_id=intent.retention_policy_id, occurred_at=saved.completed_at).exists()):
        raise ApiError('NOTIFICATION_REQUEST_PROOF_MISMATCH', 409)
    return attempt


def request_value(intent):
    unknown = intent.status != 'committed' and (intent.write_request_id is not None or matching_request(intent))
    attempt = committed_attempt(intent) if intent.status == 'committed' else None
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'state': 'unknown' if unknown else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256,
        'may_retry': not unknown and intent.error_code != 'NOTIFICATION_INTENT_CANCELLED'
            and intent.write_request_id is None and (intent.status == 'failed' or intent.expires_at <= timezone.now()),
        'error_code': 'NOTIFICATION_RESULT_UNCERTAIN' if unknown else intent.error_code,
        'notification_input': checked_input(intent).model_dump(), 'receipt': None if attempt is None else {
            'resource_type': 'notification_attempt', 'resource_id': str(attempt.id), 'version': str(attempt.attempt_number),
            'saved_at': intent.completed_at.isoformat(), 'request_id': str(intent.request_id), 'recipient_id': str(attempt.recipient_id)}}


def prepare_notification_intent(request):
    notification_identity(request, True)
    data = NotificationIntentInput.model_validate(request.data)
    actor, _, patient, _, _, _ = read_scope(request, target_id(data.target_key))
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None:
        if existing.payload_sha256 != compute_digest(data.operation_id, data.target_key, '2.0.0', data.payload.model_dump()):
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        checked_input(existing)
        if existing.status == 'committed':
            committed_attempt(existing)
        return intent_view(existing)
    actor, patient, _, runtime, retention, context = send_scope(request, target_id(data.target_key), data.payload)
    return intent_view(prepare_intent(actor=actor, patient=patient, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, schema_version='2.0.0', payload=data.payload.model_dump(), recovery_payload=data.payload.model_dump(),
        authorization_context=context, retention_policy=retention, ttl_seconds=runtime.payload['api']['intent_ttl_seconds']))


def read_notification_request(request, intent_id):
    actor, _, _ = notification_identity(request, True)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('NOTIFICATION_QUERY_INVALID', 422)
        query = NotificationRequestLookup.model_validate(dict(request.query_params.items()))
        intent = WriteIntent.objects.select_for_update().select_related('write_request').filter(actor=actor,
            request_id=intent_id, operation_id=query.operation_id, target_key=query.target_key).first()
    else:
        intent = WriteIntent.objects.select_for_update().select_related('write_request').filter(id=intent_id, actor=actor,
            operation_id='notification_v2_send').first()
    if intent is None:
        raise ApiError('NOTIFICATION_REQUEST_NOT_FOUND', 404)
    read_scope(request, target_id(intent.target_key))
    return request_value(intent)


def retry_notification_intent(request, intent_id):
    actor, _, _ = notification_identity(request, True)
    data = EvaluationRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id='notification_v2_send')
    _, _, _, runtime, retention, context = send_scope(request, target_id(intent.target_key), checked_input(intent))
    if (str(intent.generation) != data.expected_generation or not request_value(intent)['may_retry']
            or retention.id != intent.retention_policy_id or intent.generation >= 9223372036854775807):
        raise ApiError('NOTIFICATION_RETRY_UNAVAILABLE', 409)
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.generation += 1
    intent.authorization_context = context
    intent.prepared_at, intent.expires_at = timezone.now(), timezone.now() + timedelta(seconds=runtime.payload['api']['intent_ttl_seconds'])
    intent.save(update_fields=['status', 'error_code', 'completed_at', 'generation', 'authorization_context', 'prepared_at', 'expires_at'])
    return intent_view(intent)


def cancel_notification_intent(request, intent_id):
    actor, _, _ = notification_identity(request, True)
    data = NotificationCancelInput.model_validate(request.data)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id='notification_v2_send')
    _, _, patient, _, _, _ = read_scope(request, target_id(intent.target_key))
    checked_input(intent)
    if str(intent.generation) != data.expected_generation or intent.write_request_id is not None or matching_request(intent):
        raise ApiError('NOTIFICATION_CANCEL_UNAVAILABLE', 409)
    if intent.status == 'failed' and intent.error_code == 'NOTIFICATION_INTENT_CANCELLED':
        return request_value(intent)
    if intent.status != 'prepared' or intent.retention_policy.status != 'active':
        raise ApiError('NOTIFICATION_CANCEL_UNAVAILABLE', 409)
    intent.status, intent.error_code, intent.completed_at = 'failed', 'NOTIFICATION_INTENT_CANCELLED', timezone.now()
    intent.save(update_fields=['status', 'error_code', 'completed_at'])
    write_audit(actor=actor, patient=patient, action='notification_intent_cancel', object_type='write_intent',
        object_id=intent.id, object_version=str(intent.generation), request_id=intent.request_id, outcome='cancelled',
        retention_policy=intent.retention_policy, occurred_at=intent.completed_at, reason_code=data.reason)
    return request_value(intent)


@transaction.atomic
def submit_notification(request, recipient_id):
    actor, _, patient, recipient, _, _ = read_scope(request, recipient_id)
    headers = EvaluationHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    intent = WriteIntent.objects.select_for_update().select_related('write_request').get(id=headers.intent_id,
        actor=actor, patient=patient, operation_id='notification_v2_send')
    data = NotificationSendInput.model_validate(request.data)
    if (intent.target_key != request.path.removeprefix('/api/v2') or target_id(intent.target_key) != recipient.id
            or str(intent.generation) != headers.generation or intent.request_id != headers.request_id or checked_input(intent) != data):
        raise ApiError('NOTIFICATION_INTENT_MISMATCH', 409)
    if intent.status == 'committed':
        committed_attempt(intent)
        return notification_record(recipient)
    _, _, _, _, retention, context = send_scope(request, recipient_id, data)
    if (intent.status != 'prepared' or intent.expires_at <= timezone.now() or intent.authorization_context != context
            or intent.retention_policy_id != retention.id or matching_request(intent)):
        raise ApiError('NOTIFICATION_INTENT_CHANGED', 409)
    attempt, _ = authorize_native(recipient.id, initiator=actor)
    saved = commit_intent(intent=intent, action=intent.operation_id, resource_type='notification_attempt',
        resource_id=attempt.id, expected_version=attempt.attempt_number)
    write_audit(actor=actor, patient=patient, action=intent.operation_id, object_type='notification_attempt', object_id=attempt.id,
        object_version=str(attempt.attempt_number), request_id=intent.request_id, outcome='committed', retention_policy=retention, occurred_at=saved.completed_at)
    recipient.refresh_from_db()
    return notification_record(recipient)
