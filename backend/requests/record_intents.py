"""Patient-bound recovery for the deployed family record write contract.

Recovery payloads remain server-side. Every projection rechecks current field
scope, and expired intents renew the same request identity under a row lock.
"""
import re
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from authorization.policy import evaluate
from identity.models import Patient
from records.models import Record, RecordVersion
from requests.models import WriteIntent, WriteRequest
from requests.service import IntentRejected, compute_digest, prepare_intent

RECORD_OPERATIONS = ('record_create', 'record_correct')
UUID_PATTERN = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def record_target(operation, target):
    if not isinstance(target, str):
        raise IntentRejected('RECORD_INTENT_TARGET_INVALID')
    route = (rf'/api/(?:v1/)?patients/({UUID_PATTERN})/records' if operation == 'record_create'
        else rf'/api/(?:v1/)?records/({UUID_PATTERN})/corrections')
    match = re.fullmatch(route, target)
    if operation not in RECORD_OPERATIONS or match is None:
        raise IntentRejected('RECORD_INTENT_TARGET_INVALID')
    if operation == 'record_create':
        return Patient.objects.get(id=match[1]), None
    record = Record.objects.get(id=match[1])
    return record.patient, record


def record_permission(actor, epoch, audience, operation, target, payload):
    if audience != 'family':
        raise IntentRejected('FAMILY_AUDIENCE_REQUIRED')
    patient, record = record_target(operation, target)
    if not isinstance(payload, dict) or not isinstance(payload.get('fields'), list) or not payload['fields']:
        raise IntentRejected('RECORD_INTENT_INPUT_INVALID')
    fields = payload['fields']
    if any(not isinstance(field, dict) or not isinstance(field.get('field_code'), str)
            or not field['field_code'].strip() for field in fields):
        raise IntentRejected('RECORD_INTENT_INPUT_INVALID')
    author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1).author_id if record else actor.id
    evaluate(principal=actor, patient=patient, action='correct_record' if record else 'write_record',
        purpose='family_observation', environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=epoch,
        field_codes=[field['field_code'] for field in fields], record_id=record.id if record else None,
        author_id=author)
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if record is not None:
        record = Record.objects.select_for_update().get(id=record.id, patient=patient)
    if patient.status != 'active' or record is not None and record.status != 'active':
        raise IntentRejected('RECORD_INTENT_TARGET_UNAVAILABLE')
    return patient


@transaction.atomic
def prepare_record_intent(*, actor, auth_epoch, audience, data, retention_policy):
    if (not isinstance(data, dict) or set(data) != {'operation_id', 'target_key', 'request_id', 'payload', 'schema_version'}
            or data['schema_version'] != 'back-json-1'):
        raise IntentRejected('RECORD_INTENT_INPUT_INVALID')
    try:
        request_id = UUID(data['request_id'])
        if str(request_id) != data['request_id']:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise IntentRejected('RECORD_INTENT_INPUT_INVALID') from None
    patient = record_permission(actor, auth_epoch, audience, data['operation_id'], data['target_key'], data['payload'])
    return prepare_intent(actor=actor, patient=patient, operation_id=data['operation_id'],
        target_key=data['target_key'], request_id=request_id, payload=data['payload'], schema_version='back-json-1',
        retention_policy=retention_policy, recovery_payload=data['payload'])


def _receipt(intent):
    receipts = list(WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
        target_key=intent.target_key, request_id=intent.request_id))
    if not receipts:
        if intent.write_request_id or intent.status == 'committed':
            raise IntentRejected('RECORD_RECEIPT_INVALID')
        return None
    saved = receipts[0]
    if (len(receipts) != 1 or intent.write_request_id != saved.id or intent.status != 'committed'
            or saved.status != 'committed' or saved.resource_id is None or saved.completed_at is None
            or saved.payload_sha256 != intent.payload_sha256 or saved.patient_id != intent.patient_id):
        raise IntentRejected('INTENT_RECEIPT_RECONCILIATION_REQUIRED')
    if intent.operation_id == 'record_create' and saved.resource_type == 'record':
        record = Record.objects.get(id=saved.resource_id, patient_id=intent.patient_id)
        version = RecordVersion.objects.get(record_id=record.id, patient_id=intent.patient_id, version=1)
    elif intent.operation_id == 'record_correct' and saved.resource_type == 'record_version':
        version = RecordVersion.objects.get(id=saved.resource_id, patient_id=intent.patient_id)
        record = Record.objects.get(id=version.record_id, patient_id=intent.patient_id)
        _, requested_record = record_target(intent.operation_id, intent.target_key)
        if requested_record.id != record.id:
            raise IntentRejected('RECORD_RECEIPT_INVALID')
    else:
        raise IntentRejected('RECORD_RECEIPT_INVALID')
    return {'patient_id': str(intent.patient_id), 'record_id': str(record.id),
        'record_version_id': str(version.id), 'version': version.version}


@transaction.atomic
def record_request_view(*, actor, auth_epoch, audience, intent):
    intent = WriteIntent.objects.get(id=intent.id, actor=actor)
    payload = intent.recovery_payload
    # Legacy intents without recovery data can expose no patient resource or
    # payload until their original write was bound and current scope is known.
    if payload is None:
        raise IntentRejected('RECORD_RECOVERY_PAYLOAD_UNAVAILABLE')
    patient = record_permission(actor, auth_epoch, audience, intent.operation_id, intent.target_key, payload)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor)
    if intent.patient_id != patient.id or intent.recovery_payload != payload or intent.payload_sha256 != compute_digest(
            intent.operation_id, intent.target_key, 'back-json-1', payload):
        raise IntentRejected('RECORD_INTENT_MISMATCH')
    receipt = _receipt(intent)
    return {'patient_id': str(patient.id), 'intent_id': str(intent.id), 'request_id': str(intent.request_id),
        'operation_id': intent.operation_id, 'target_key': intent.target_key, 'status': intent.status,
        'generation': str(intent.generation), 'prepared_at': intent.prepared_at.isoformat(),
        'expires_at': intent.expires_at.isoformat(), 'resource_id': str(intent.write_request.resource_id) if receipt else None,
        'receipt': receipt, 'may_retry': receipt is None and (intent.status == 'failed'
            or intent.status == 'prepared' and intent.expires_at <= timezone.now()), 'record_input': payload}


@transaction.atomic
def retry_record_intent(*, actor, auth_epoch, audience, intent_id, expected_generation):
    intent = WriteIntent.objects.get(id=intent_id, actor=actor)
    view = record_request_view(actor=actor, auth_epoch=auth_epoch, audience=audience, intent=intent)
    intent.refresh_from_db()
    if str(intent.generation) != str(expected_generation) or not view['may_retry']:
        raise IntentRejected('RECORD_RETRY_NOT_AVAILABLE')
    if intent.generation >= 9223372036854775807:
        raise IntentRejected('GENERATION_RANGE_EXCEEDED')
    intent.generation += 1
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=300)
    intent.save(update_fields=['generation', 'status', 'error_code', 'completed_at', 'prepared_at', 'expires_at'])
    return intent
