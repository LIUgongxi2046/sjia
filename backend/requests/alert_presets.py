"""Recoverable, compare-and-swap selection of approved preset IDs only."""
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.urls import resolve, reverse
from django.utils import timezone

from api.base import ApiError
from assessment.qualified_facts import QualificationRejected
from alerting.models import AlertPresetChoice
from alerting.preset_policies import PresetIntentInput, PresetSelectionInput, PresetWriteHeaders
from alerting.presets import (PURPOSE, lock_scope, approved_assignment, approved_option, latest_choice, scope_id, intent_ttl_seconds)
from audit.models import AuditEvent
from audit.service import write_audit
from configurations.evidence import artifact_verification_scope
from files.purpose_dependencies import invalidate_resource
from requests.models import WriteIntent, WriteRequest
from requests.service import prepare_intent, commit_intent, compute_digest, NORMALIZATION_VERSION
from tasks.outbox import emit_event

OPERATION = 'alert_preset_select'


def target_patient(target):
    if not isinstance(target, str) or not target.startswith('/api/') or any(item in target for item in ('?', '#', '://')):
        raise ApiError('PRESET_TARGET_INVALID', 422)
    match = resolve(target)
    if match.url_name != OPERATION or reverse(OPERATION, kwargs=match.kwargs) != target:
        raise ApiError('PRESET_TARGET_INVALID', 422)
    return match.kwargs['patient_id']


def payload_value(payload):
    return PresetSelectionInput.model_validate(payload).model_dump(mode='json')


def prepared(actor, patient_id, epoch, audience, payload, *, check_version=True):
    gate, actor, patient, context = lock_scope(actor, patient_id, epoch, audience)
    config, policy, _ = approved_assignment(patient.id, settings.SJIA_ENVIRONMENT, PURPOSE)
    if str(config.id) != payload['assignment_id'] or config.checksum != payload['assignment_checksum']:
        raise ApiError('PRESET_ASSIGNMENT_CHANGED', 409)
    option = next((item for item in policy.options if str(item.schedule_config_id) == payload['schedule_config_id']), None)
    if option is None:
        raise ApiError('PRESET_OPTION_NOT_APPROVED', 422)
    schedule, _, _ = approved_option(policy, option)
    previous = latest_choice(patient.id, settings.SJIA_ENVIRONMENT, PURPOSE)
    version = previous.version if previous else 0
    if check_version and str(version) != payload['expected_version']:
        raise ApiError('PRESET_VERSION_CONFLICT', 409)
    return actor, patient, context, config, schedule, previous


def matching_request(intent):
    return WriteRequest.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
        action=OPERATION, request_id=intent.request_id, target_key=intent.target_key).exists()


def checked_payload(intent):
    payload = payload_value(intent.recovery_payload)
    if (intent.operation_id != OPERATION or intent.patient_id != target_patient(intent.target_key)
            or intent.normalization_version != NORMALIZATION_VERSION
            or compute_digest(OPERATION, intent.target_key, '1.0.0', payload) != intent.payload_sha256):
        raise ApiError('PRESET_RECOVERY_INVALID', 409)
    return payload


def receipt(intent):
    saved = intent.write_request
    payload = checked_payload(intent)
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.action != OPERATION
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.request_id != intent.request_id or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.normalization_version != intent.normalization_version
            or saved.resource_type != 'alert_preset_choice' or saved.expected_version != int(payload['expected_version'])
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('PRESET_RECEIPT_INVALID', 409)
    choice = AlertPresetChoice.objects.select_for_update().get(id=saved.resource_id, write_request=saved,
        patient_id=intent.patient_id, selected_by_id=intent.actor_id, assignment_id=payload['assignment_id'],
        schedule_id=payload['schedule_config_id'], version=int(payload['expected_version']) + 1)
    if not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id, action=OPERATION,
            object_type='alert_preset_choice', object_id=choice.id, object_version=str(choice.version),
            request_id=intent.request_id, outcome='committed', occurred_at=saved.completed_at,
            retention_policy_id=intent.retention_policy_id).exists():
        raise ApiError('PRESET_RECEIPT_INVALID', 409)
    return {'resource_type': 'alert_preset_choice', 'resource_id': str(choice.id), 'version': str(choice.version),
        'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id)}


@transaction.atomic
@artifact_verification_scope()
def prepare_preset_intent(*, actor, auth_epoch, audience, data, retention_policy):
    data = PresetIntentInput.model_validate(data)
    payload = data.payload.model_dump(mode='json')
    patient_id = target_patient(data.target_key)
    _, actor, patient, _ = lock_scope(actor, patient_id, auth_epoch, audience)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=OPERATION,
        request_id=data.request_id, target_key=data.target_key).first()
    if existing is not None:
        if checked_payload(existing) != payload:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        # An unchanged request never creates another generation or business effect.
        if existing.status == 'committed':
            receipt(existing)
        return existing
    actor, patient, context, _, _, _ = prepared(actor, patient_id, auth_epoch, audience, payload)
    return prepare_intent(actor=actor, patient=patient, operation_id=OPERATION, target_key=data.target_key,
        request_id=data.request_id, payload=payload, schema_version='1.0.0', retention_policy=retention_policy,
        authorization_context=context, recovery_payload=payload, ttl_seconds=intent_ttl_seconds(settings.SJIA_ENVIRONMENT))


@transaction.atomic
@artifact_verification_scope()
def preset_request_view(*, actor, auth_epoch, audience, intent):
    _, actor, _, context = lock_scope(actor, intent.patient_id, auth_epoch, audience)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor, operation_id=OPERATION)
    payload = checked_payload(intent)
    saved = receipt(intent) if intent.status == 'committed' else None
    unknown = saved is None and matching_request(intent)
    may_retry = (not unknown and saved is None and (intent.status == 'failed'
        or intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != context)))
    superseded = False
    if not unknown and saved is None:
        try:
            prepared(actor, intent.patient_id, auth_epoch, audience, payload)
        except (ApiError, QualificationRejected):
            superseded, may_retry = True, False
    return {'patient_id': str(intent.patient_id), 'intent_id': str(intent.id), 'request_id': str(intent.request_id),
        'operation_id': OPERATION, 'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if unknown else 'failed' if superseded else {'prepared': 'pending', 'failed': 'failed', 'committed': 'committed'}[intent.status],
        'may_retry': may_retry, 'receipt': saved, 'error_code': 'PRESET_SELECTION_SUPERSEDED' if superseded else intent.error_code, 'generation': str(intent.generation),
        'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256, 'preset_input': payload}


@transaction.atomic
@artifact_verification_scope()
def retry_preset_intent(*, actor, auth_epoch, audience, intent, expected_generation):
    payload = checked_payload(intent)
    actor, _, context, _, _, _ = prepared(actor, intent.patient_id, auth_epoch, audience, payload)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor, operation_id=OPERATION)
    state = preset_request_view(actor=actor, auth_epoch=auth_epoch, audience=audience, intent=intent)
    if (not state['may_retry'] or str(intent.generation) != expected_generation or intent.generation >= 9223372036854775807):
        raise ApiError('PRESET_RETRY_UNAVAILABLE', 409)
    intent.generation += 1
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.authorization_context = context
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=intent_ttl_seconds(settings.SJIA_ENVIRONMENT))
    intent.save(update_fields=['generation', 'status', 'error_code', 'completed_at', 'authorization_context', 'prepared_at', 'expires_at'])
    return intent


@transaction.atomic
@artifact_verification_scope()
def submit_preset(*, actor, patient_id, auth_epoch, audience, target, data, headers):
    _, actor, patient, context = lock_scope(actor, patient_id, auth_epoch, audience)
    payload, headers = payload_value(data), PresetWriteHeaders.model_validate(headers)
    if target_patient(target) != patient.id:
        raise ApiError('PRESET_TARGET_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, patient=patient,
        operation_id=OPERATION, target_key=target)
    if (intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or checked_payload(intent) != payload):
        raise ApiError('PRESET_INTENT_MISMATCH', 409)
    if intent.status == 'committed':
        return receipt(intent)
    if intent.authorization_context != context:
        raise ApiError('PRESET_AUTHORIZATION_CHANGED', 403)
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise ApiError('PRESET_INTENT_EXPIRED', 409)
    if matching_request(intent):
        raise ApiError('PRESET_RESULT_UNKNOWN', 409)
    _, _, context, assignment, schedule, previous = prepared(actor, patient.id, auth_epoch, audience, payload)
    version = (previous.version if previous else 0) + 1
    if version > 9223372036854775807:
        raise ApiError('PRESET_VERSION_EXHAUSTED', 409)
    choice = AlertPresetChoice.objects.create(patient=patient, environment=settings.SJIA_ENVIRONMENT, purpose=PURPOSE,
        version=version, assignment=assignment, schedule=schedule, selected_by=actor, selected_at=timezone.now(),
        authorization_context=context)
    saved = commit_intent(intent=intent, action=OPERATION, resource_type='alert_preset_choice', resource_id=choice.id,
        expected_version=version - 1)
    choice.write_request = saved
    choice.save(update_fields=['write_request'])
    write_audit(actor=actor, patient=patient, action=OPERATION, object_type='alert_preset_choice', object_id=choice.id,
        object_version=str(version), request_id=intent.request_id, outcome='committed',
        occurred_at=saved.completed_at, retention_policy=intent.retention_policy)
    invalidate_resource('alert_preset_scope', scope_id(patient.id, settings.SJIA_ENVIRONMENT, PURPOSE), str(version - 1))
    emit_event(event_type='clinical.context_changed', schema_version='clinical-change-2', aggregate_id=patient.id,
        aggregate_version=patient.data_epoch, correlation_id=choice.id, resource_refs={'environment': settings.SJIA_ENVIRONMENT,
            'patient_id': str(patient.id), 'resource_type': 'alert_preset_choice', 'resource_id': str(choice.id),
            'resource_version': str(version), 'invalidated_node_ids': []})
    return receipt(intent)
