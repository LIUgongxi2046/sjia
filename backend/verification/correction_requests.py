"""Controlled, recoverable correction writes with case-scoped read authorization."""
from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.urls import resolve, reverse
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from assessment.provenance import entity_reference
from assessment.qualified_facts import lock_case_permissions
from audit.models import AuditEvent
from audit.service import write_audit
from authorization.policy import Denied, check_service_grant
from family_content.models import EnvironmentGate
from records.correction import CorrectionRejected
from records.models import RecordVersion
from requests.models import WriteIntent, WriteRequest
from requests.service import NORMALIZATION_VERSION, commit_intent, compute_digest, prepare_intent, validate_target_key
from sources.models import DataUseAuthorization
from verification.corrections import (PROTOCOL, MAX_VERSION, approved_retention, correction_payload,
    create_correction_request, locked_record, resolve_correction_request, selected_fields)
from verification.models import CorrectionRequest
from verification.workflow import source_authorization

CORRECTION_OPERATIONS = {'correction_request_create': 'CorrectionRequestInput',
    'correction_request_resolve': 'CorrectionResolution'}
INTENT_VALIDATOR = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/IntentInput'}, format_checker=FormatChecker())
HEADERS_VALIDATOR = Draft202012Validator({'type': 'object', 'additionalProperties': False,
    'required': ['intent_id', 'request_id', 'generation'], 'properties': {
        'intent_id': {'type': 'string', 'format': 'uuid'}, 'request_id': {'type': 'string', 'format': 'uuid'},
        'generation': {'type': 'string', 'pattern': '^[1-9][0-9]*$', 'maxLength': 19}}}, format_checker=FormatChecker())


def target_keys(operation, target):
    if operation not in CORRECTION_OPERATIONS:
        raise CorrectionRejected('CORRECTION_OPERATION_INVALID')
    validate_target_key(target)
    route = resolve(target)
    if route.url_name != operation or target != reverse(operation, kwargs=route.kwargs):
        raise CorrectionRejected('CORRECTION_TARGET_INVALID')
    return route.kwargs


def correction_scope(*, actor, epoch, audience, environment, operation, target, payload, current=False):
    keys = target_keys(operation, target)
    correction_payload(CORRECTION_OPERATIONS[operation], payload)
    expected_audience = 'family' if operation == 'correction_request_create' else 'support'
    if audience != expected_audience:
        raise Denied(f'{expected_audience.upper()}_AUDIENCE_REQUIRED')
    row = None
    if operation == 'correction_request_create':
        record_id, version_id, field_ids = keys['record_id'], payload['record_version_id'], payload['field_ids']
    else:
        row = CorrectionRequest.objects.get(id=keys['correction_request_id'])
        original = RecordVersion.objects.get(id=row.record_version_id, patient_id=row.patient_id)
        record_id, version_id, field_ids = original.record_id, original.id, row.field_ids
    patient, actor, record = locked_record(record_id, actor, epoch, environment)
    version = RecordVersion.objects.select_for_update().get(id=version_id, patient=patient, record_id=record.id)
    if current and operation == 'correction_request_create' and record.current_version != version.version:
        raise CorrectionRejected('VERSION_CONFLICT')
    fields = selected_fields(version, field_ids)
    grants, role, authorization = [], None, None
    if operation == 'correction_request_create':
        author_id = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1).author_id
        grants = lock_case_permissions(actor, patient, 'request_correction', 'family_observation', timezone.now(),
            field_codes=[field.field_code for field in fields], record_id=record.id, author_id=author_id, require_relation=True)
    else:
        row = CorrectionRequest.objects.select_for_update().get(id=row.id, patient_id=patient.id)
        role = check_service_grant(actor, 'verify_field', environment, timezone.now())
        purpose = DataUseAuthorization.objects.get(id=version.use_authorization_id, patient=patient).purpose
        authorization = source_authorization(version, purpose, environment)
        for action in ('verify_field', 'correct_record'):
            grants.extend(lock_case_permissions(actor, patient, action, purpose, timezone.now(),
                record_id=record.id, author_id=version.author_id, field_codes=[field.field_code for field in fields]))
        if current and (row.status not in ('pending', 'in_review') or str(row.version) != payload['expected_version']):
            raise CorrectionRejected('VERSION_CONFLICT')
    retention = approved_retention(version)
    gate = EnvironmentGate.objects.get(environment=environment)
    context = {'audience': audience, 'environment': environment, 'actor_id': str(actor.id),
        'auth_epoch': str(actor.auth_epoch), 'patient_id': str(patient.id), 'data_epoch': str(patient.data_epoch),
        'verification_epoch': str(patient.verification_epoch), 'gate_version': str(gate.version),
        'record': entity_reference(record), 'version': entity_reference(version),
        'fields': [entity_reference(field) for field in fields], 'retention': entity_reference(retention),
        'grants': [entity_reference(grant) for grant in sorted({grant.id: grant for grant in grants}.values(), key=lambda value: str(value.id))],
        'service_grant': entity_reference(role) if role else None,
        'authorization': entity_reference(authorization) if authorization else None,
        'correction_request': entity_reference(row) if row else None}
    return patient, actor, record, version, fields, retention, context


def saved_payload(intent):
    if intent.operation_id not in CORRECTION_OPERATIONS or intent.patient_id is None:
        raise CorrectionRejected('CORRECTION_INTENT_INVALID')
    target_keys(intent.operation_id, intent.target_key)
    payload = correction_payload(CORRECTION_OPERATIONS[intent.operation_id], intent.recovery_payload)
    if (intent.normalization_version != NORMALIZATION_VERSION or intent.payload_sha256 !=
            compute_digest(intent.operation_id, intent.target_key, '1.0.0', payload)):
        raise CorrectionRejected('CORRECTION_INTENT_MISMATCH')
    return payload


def receipt(intent):
    payload, saved = saved_payload(intent), intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.request_id != intent.request_id or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version or saved.resource_type != 'correction_request'
            or saved.expected_version is None or saved.expected_version < 1 or saved.completed_at is None
            or saved.completed_at != intent.completed_at):
        raise CorrectionRejected('CORRECTION_RECEIPT_INVALID')
    row = CorrectionRequest.objects.get(id=saved.resource_id, patient_id=intent.patient_id)
    if intent.operation_id == 'correction_request_create':
        if (row.requested_by_id != intent.actor_id or str(row.record_version_id) != payload['record_version_id']
                or {str(value) for value in row.field_ids} != set(payload['field_ids'])
                or row.reason != payload['reason'] or row.proposed_text != payload['proposed_text']):
            raise CorrectionRejected('CORRECTION_RECEIPT_INVALID')
    elif (str(row.id) != str(target_keys(intent.operation_id, intent.target_key)['correction_request_id'])
            or saved.expected_version != int(payload['expected_version']) + 1):
        raise CorrectionRejected('CORRECTION_RECEIPT_INVALID')
    if not AuditEvent.objects.filter(actor_id=intent.actor_id, patient_id=intent.patient_id,
            action=intent.operation_id, object_type='correction_request', object_id=row.id,
            object_version=str(saved.expected_version), request_id=intent.request_id,
            outcome='committed', retention_policy_id=intent.retention_policy_id, occurred_at=saved.completed_at).exists():
        raise CorrectionRejected('CORRECTION_RECEIPT_INVALID')
    return {'resource_type': 'correction_request', 'resource_id': str(row.id), 'version': str(saved.expected_version),
        'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id)}


def scope_for_intent(intent, **identity):
    payload = saved_payload(intent)
    scope = correction_scope(**identity, operation=intent.operation_id, target=intent.target_key, payload=payload)
    if scope[0].id != intent.patient_id or scope[1].id != intent.actor_id:
        raise CorrectionRejected('CORRECTION_INTENT_MISMATCH')
    return scope


def retry_available(intent, context):
    return (intent.status == 'failed' or intent.status == 'prepared' and
        (intent.expires_at <= timezone.now() or intent.authorization_context != context)) and not (
        intent.write_request_id or WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
            target_key=intent.target_key, request_id=intent.request_id).exists())


@transaction.atomic
def prepare_correction_intent(*, data, **identity):
    if not INTENT_VALIDATOR.is_valid(data):
        raise CorrectionRejected('CORRECTION_INTENT_INPUT_INVALID')
    operation, target, payload = data['operation_id'], data['target_key'], data['payload']
    patient, actor, _, _, _, retention, context = correction_scope(**identity,
        operation=operation, target=target, payload=payload)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=operation,
        target_key=target, request_id=data['request_id']).first()
    if existing:
        if saved_payload(existing) != payload or existing.patient_id != patient.id:
            raise CorrectionRejected('CORRECTION_INTENT_MISMATCH')
        if existing.status == 'committed':
            receipt(existing)
        return existing
    correction_scope(**identity, operation=operation, target=target, payload=payload, current=True)
    return prepare_intent(actor=actor, patient=patient, operation_id=operation, target_key=target,
        request_id=UUID(data['request_id']), payload=payload, schema_version='1.0.0', retention_policy=retention,
        authorization_context=context, recovery_payload=payload)


@transaction.atomic
def submit_correction(*, operation, target, payload, headers, **identity):
    if not HEADERS_VALIDATOR.is_valid(headers):
        raise CorrectionRejected('CORRECTION_HEADERS_INVALID')
    scope = correction_scope(**identity, operation=operation, target=target, payload=payload)
    patient, actor, _, _, _, _, context = scope
    intent = WriteIntent.objects.select_for_update().get(id=headers['intent_id'], actor=actor, patient=patient,
        operation_id=operation, target_key=target)
    if (str(intent.request_id) != headers['request_id'] or str(intent.generation) != headers['generation']
            or saved_payload(intent) != payload):
        raise CorrectionRejected('CORRECTION_INTENT_MISMATCH')
    if intent.status == 'committed':
        return receipt(intent)
    if intent.authorization_context != context:
        raise CorrectionRejected('CORRECTION_AUTHORIZATION_CHANGED')
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise CorrectionRejected('CORRECTION_INTENT_EXPIRED')
    keys = target_keys(operation, target)
    service_identity = {key: value for key, value in identity.items() if key != 'audience'}
    if operation == 'correction_request_create':
        row = create_correction_request(record_id=keys['record_id'], payload=payload, **service_identity)
    else:
        row = resolve_correction_request(request_id=keys['correction_request_id'], payload=payload, **service_identity)
    saved = commit_intent(intent=intent, action=operation, resource_type='correction_request',
        resource_id=row.id, expected_version=row.version)
    write_audit(actor=actor, patient=patient, action=operation, object_type='correction_request', object_id=row.id,
        object_version=str(row.version), request_id=intent.request_id, outcome='committed',
        retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return receipt(intent)


@transaction.atomic
def correction_request_view(intent, **identity):
    scope = scope_for_intent(intent, **identity)
    may_retry = retry_available(intent, scope[-1])
    if may_retry:
        try:
            correction_scope(**identity, operation=intent.operation_id, target=intent.target_key,
                payload=intent.recovery_payload, current=True)
        except CorrectionRejected:
            may_retry = False
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': may_retry, 'receipt': receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'correction_input': intent.recovery_payload}


@transaction.atomic
def retry_correction_intent(intent, *, data, **identity):
    if (not isinstance(data, dict) or set(data) != {'expected_generation'} or not isinstance(data['expected_generation'], str)
            or data['expected_generation'] != str(intent.generation)):
        raise CorrectionRejected('GENERATION_MISMATCH')
    scope = scope_for_intent(intent, **identity)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=scope[1])
    if str(intent.generation) != data['expected_generation'] or not retry_available(intent, scope[-1]):
        raise CorrectionRejected('CORRECTION_RETRY_UNAVAILABLE')
    correction_scope(**identity, operation=intent.operation_id, target=intent.target_key,
        payload=intent.recovery_payload, current=True)
    if intent.generation >= MAX_VERSION:
        raise CorrectionRejected('GENERATION_RANGE_EXCEEDED')
    intent.generation += 1
    intent.prepared_at, intent.status, intent.error_code, intent.completed_at = timezone.now(), 'prepared', None, None
    intent.expires_at = intent.prepared_at + timedelta(seconds=300)
    intent.authorization_context = scope[-1]
    intent.save(update_fields=['generation', 'prepared_at', 'expires_at', 'status', 'error_code', 'completed_at', 'authorization_context'])
    return intent
