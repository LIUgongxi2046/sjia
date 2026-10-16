"""Version-bound professional correction requests; no clinical facts are edited here."""
import json
from pathlib import Path
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from assessment.qualified_facts import lock_case_permissions
from audit.service import write_audit
from authorization.policy import Denied, check_principal_session, check_service_grant
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from records.correction import CorrectionRejected
from records.models import Record, RecordField, RecordVersion
from sources.models import DataUseAuthorization
from verification.models import CorrectionRequest


PROTOCOL = json.loads((Path(__file__).resolve().parents[2] / 'backend-design/protocol.schema.json').read_text())
VALIDATORS = {name: Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
    for name in ('CorrectionRequestInput', 'CorrectionResolution')}
MAX_VERSION = 9223372036854775807


def correction_payload(name, payload):
    if not VALIDATORS[name].is_valid(payload) or not payload['reason'].strip():
        raise CorrectionRejected('CORRECTION_INPUT_INVALID')
    if name == 'CorrectionRequestInput':
        if len({UUID(value) for value in payload['field_ids']}) != len(payload['field_ids']):
            raise CorrectionRejected('CORRECTION_FIELDS_DUPLICATE')
        if payload['proposed_text'] is not None and not payload['proposed_text'].strip():
            raise CorrectionRejected('CORRECTION_INPUT_INVALID')
    elif (int(payload['expected_version']) >= MAX_VERSION
            or (payload['decision'] == 'resolved') != (payload['resolution_record_version_id'] is not None)):
        raise CorrectionRejected('CORRECTION_RESOLUTION_INVALID')
    return payload


def locked_record(record_id, actor, epoch, environment):
    gate = EnvironmentGate.objects.select_for_update().get(environment=environment)
    if not gate.enabled:
        raise Denied('ENVIRONMENT_DISABLED')
    initial = Record.objects.get(id=record_id)
    patient = Patient.objects.select_for_update().get(id=initial.patient_id)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, epoch)
    record = Record.objects.select_for_update().get(id=record_id, patient=patient)
    if patient.status != 'active' or record.status != 'active':
        raise CorrectionRejected('CORRECTION_RECORD_UNAVAILABLE')
    return patient, actor, record


def selected_fields(version, field_ids):
    rows = list(RecordField.objects.select_for_update().filter(patient_id=version.patient_id,
        record_version_id=version.id, id__in=field_ids).order_by('ordinal', 'id'))
    if not rows or {row.id for row in rows} != {UUID(str(value)) for value in field_ids}:
        raise CorrectionRejected('CORRECTION_FIELDS_MISMATCH')
    return rows


def approved_retention(version):
    policy = version.retention_policy
    if (policy.status != 'active' or policy.approved_at is None or policy.approved_at > timezone.now()
            or policy.approved_by_id is None or not policy.evidence_ref or not policy.evidence_ref.strip()):
        raise CorrectionRejected('CORRECTION_RETENTION_UNAVAILABLE')
    return policy


def request_view(row):
    return {'id': str(row.id), 'version': str(row.version), 'status': row.status,
        'request': {'record_version_id': str(row.record_version_id), 'field_ids': [str(value) for value in row.field_ids],
            'reason': row.reason, 'proposed_text': row.proposed_text},
        'resolution_record_version_id': str(row.resolution_record_version_id) if row.resolution_record_version_id else None}


@transaction.atomic
def create_correction_request(*, record_id, actor, epoch, environment, payload):
    payload = correction_payload('CorrectionRequestInput', payload)
    patient, actor, record = locked_record(record_id, actor, epoch, environment)
    version = RecordVersion.objects.select_for_update().get(id=payload['record_version_id'], patient=patient, record_id=record.id)
    if version.version != record.current_version:
        raise CorrectionRejected('VERSION_CONFLICT')
    fields = selected_fields(version, payload['field_ids'])
    original_author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1).author_id
    lock_case_permissions(actor, patient, 'request_correction', 'family_observation', timezone.now(),
        field_codes=[row.field_code for row in fields], record_id=record.id, author_id=original_author, require_relation=True)
    policy = approved_retention(version)
    ids = [field.id for field in fields]
    prior = CorrectionRequest.objects.filter(patient_id=patient.id, record_version_id=version.id,
        requested_by=actor, field_ids=ids, reason=payload['reason'], proposed_text=payload['proposed_text'],
        status__in=['pending', 'in_review']).first()
    if prior is not None:
        return prior
    now = timezone.now()
    row = CorrectionRequest.objects.create(patient_id=patient.id, record_version_id=version.id, requested_by=actor,
        field_ids=ids, reason=payload['reason'], proposed_text=payload['proposed_text'], status='pending', version=1, created_at=now)
    write_audit(actor=actor, patient=patient, action='correction_request_create', object_type='correction_request',
        object_id=row.id, object_version='1', outcome='committed', retention_policy=policy, occurred_at=now)
    return row


@transaction.atomic
def read_correction_request(*, request_id, actor, epoch, environment):
    initial = CorrectionRequest.objects.get(id=request_id, requested_by=actor)
    version = RecordVersion.objects.get(id=initial.record_version_id, patient_id=initial.patient_id)
    patient, actor, record = locked_record(version.record_id, actor, epoch, environment)
    row = CorrectionRequest.objects.get(id=request_id, patient_id=patient.id, requested_by=actor)
    fields = selected_fields(version, row.field_ids)
    original_author = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1).author_id
    lock_case_permissions(actor, patient, 'request_correction', 'family_observation', timezone.now(),
        field_codes=[field.field_code for field in fields], record_id=record.id, author_id=original_author, require_relation=True)
    return row


@transaction.atomic
def resolve_correction_request(*, request_id, actor, epoch, environment, payload):
    payload = correction_payload('CorrectionResolution', payload)
    initial = CorrectionRequest.objects.get(id=request_id)
    original = RecordVersion.objects.get(id=initial.record_version_id, patient_id=initial.patient_id)
    patient, actor, record = locked_record(original.record_id, actor, epoch, environment)
    row = CorrectionRequest.objects.select_for_update().get(id=request_id, patient_id=patient.id)
    original = RecordVersion.objects.select_for_update().get(id=row.record_version_id, patient=patient, record_id=record.id)
    fields = selected_fields(original, row.field_ids)
    now = timezone.now()
    check_service_grant(actor, 'verify_field', environment, now)
    authorization = DataUseAuthorization.objects.select_for_update().get(id=original.use_authorization_id, patient=patient)
    from verification.workflow import source_authorization
    source_authorization(original, authorization.purpose, environment)
    # A service role alone never grants access to this patient's professional fields.
    for action in ('verify_field', 'correct_record'):
        lock_case_permissions(actor, patient, action, authorization.purpose, now,
            record_id=record.id, author_id=original.author_id, field_codes=[field.field_code for field in fields])
    policy = approved_retention(original)
    expected = int(payload['expected_version'])
    resolution_id = UUID(payload['resolution_record_version_id']) if payload['resolution_record_version_id'] else None
    if (row.status == payload['decision'] and row.version == expected + 1 and row.resolved_by_id == actor.id
            and row.resolution_reason == payload['reason'] and row.resolution_record_version_id == resolution_id):
        return row
    if row.version != expected or row.status not in ('pending', 'in_review'):
        raise CorrectionRejected('VERSION_CONFLICT')
    if resolution_id is not None:
        resolution = RecordVersion.objects.select_for_update().get(id=resolution_id, patient=patient, record_id=record.id)
        if (resolution.version != record.current_version or resolution.version <= original.version
                or resolution.supersedes_version != original.version or resolution.corrected_at is None
                or not resolution.correction_reason or not resolution.correction_reason.strip()
                or resolution.recorded_at < row.created_at):
            raise CorrectionRejected('CORRECTION_RESOLUTION_INVALID')
        # Bind each requested field to its replacement identity in the actual saved correction.
        replacements = list(RecordField.objects.select_for_update().filter(record_version_id=resolution.id, patient=patient,
            field_code__in=[field.field_code for field in fields]))
        if sorted((field.field_code, field.concept_code, field.value_type) for field in replacements) != sorted(
                (field.field_code, field.concept_code, field.value_type) for field in fields):
            raise CorrectionRejected('CORRECTION_RESOLUTION_FIELDS_MISMATCH')
        source_authorization(resolution, authorization.purpose, environment)
    row.status, row.version = payload['decision'], row.version + 1
    row.resolved_by, row.resolved_at = actor, now
    row.resolution_record_version_id, row.resolution_reason = resolution_id, payload['reason']
    row.save(update_fields=['status', 'version', 'resolved_by', 'resolved_at', 'resolution_record_version_id', 'resolution_reason'])
    write_audit(actor=actor, patient=patient, action='correction_request_resolve', object_type='correction_request',
        object_id=row.id, object_version=str(row.version), outcome='committed',
        reason_code=f'CORRECTION_{row.status.upper()}', retention_policy=policy, occurred_at=now)
    return row
