"""记录更正：新不可变版本、旧依据失效、新字段未核对。

同版本只接受一次修改；新字段全部重新核对（无状态）；
旧评估/提示/摘要通过 data_epoch + 1 与依赖失效阻止采用。
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

from django.db import transaction
from django.db.models import F

from identity.models import Patient
from records.models import Record, RecordField, RecordVersion, RecordAttachment
from records.service import _create_medical_time, _validate_field_input, medical_time_view
from medical_time.models import MedicalTime
from archives.record_facts import correct_record_facts

correction_schema = json.loads(Path(__file__).with_name('search.schema.json').read_text())
correction_input = Draft202012Validator({**correction_schema, '$ref': '#/$defs/RecordCorrectionInput'}, format_checker=FormatChecker())


class CorrectionRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def validate_correction_request(value):
    if not correction_input.is_valid(value):
        raise CorrectionRejected('CORRECTION_INPUT_INVALID')


def medical_time_for_correction(patient, previous_field, field):
    previous_time = medical_time_view(previous_field.medical_time_id, patient)
    if field.get('medical_time') == previous_time:
        return MedicalTime.objects.get(id=previous_field.medical_time_id, patient=patient) if previous_field.medical_time_id else None
    # Only an unchanged server projection may retain a resolved instant. New
    # time content must use the input contract and be resolved by the server.
    from records.input_validation import validate_medical_input
    from records.service import RecordRejected
    try:
        validate_medical_input(field.get('medical_time'))
    except RecordRejected as exc:
        raise CorrectionRejected(exc.reason_code) from exc
    return _create_medical_time(patient, field.get('medical_time'))


@transaction.atomic
def correct_record(
    *,
    record: Record,
    expected_version: int,
    actor,
    reason: str,
    fields: list[dict],
    use_authorization_id,
    retention_policy,
    capture_method="manual",
    source_revision=None,
    origin_type=None,
) -> dict:
    if type(expected_version) is not int or not 1 <= expected_version < 9223372036854775807:
        raise CorrectionRejected('VERSION_INVALID')
    Patient.objects.select_for_update().get(id=record.patient_id)
    record = Record.objects.select_for_update().get(id=record.id, patient=record.patient)
    if record.current_version != expected_version:
        raise CorrectionRejected("VERSION_CONFLICT")
    if not isinstance(reason, str) or not reason.strip():
        raise CorrectionRejected("REASON_REQUIRED")
    now = datetime.now(timezone.utc)
    new_version = expected_version + 1
    patient = record.patient
    previous = RecordVersion.objects.get(record_id=record.id, patient=patient, version=expected_version)
    previous_fields = list(RecordField.objects.filter(record_version_id=previous.id, patient=patient).order_by('ordinal'))
    previous_codes = [field.field_code for field in previous_fields]
    if not fields or [field.get('field_code') for field in fields] != previous_codes:
        raise CorrectionRejected('CORRECTION_FIELD_SET_INCOMPLETE')
    if any(field.get('value_type') != previous_field.value_type or field.get('concept_code') != previous_field.concept_code
           for field, previous_field in zip(fields, previous_fields)):
        raise CorrectionRejected('CORRECTION_FIELD_IDENTITY_CHANGED')
    version = RecordVersion.objects.create(
        patient=patient,
        record_id=record.id,
        version=new_version,
        supersedes_version=expected_version,
        author=actor,
        origin_type=previous.origin_type if origin_type is None else origin_type,
        capture_method=capture_method,
        source_revision=source_revision,
        source_organization=previous.source_organization,
        report_id=previous.report_id,
        use_authorization_id=use_authorization_id,
        quality_status=RecordVersion.QualityStatus.PENDING,
        quality_reasons=[],
        correction_reason=reason,
        corrected_at=now,
        recorded_at=now,
        retention_policy=retention_policy,
    )
    created_fields = []
    for ordinal, field in enumerate(fields, start=1):
        _validate_field_input(field)
        medical_time = medical_time_for_correction(patient, previous_fields[ordinal - 1], field)
        created_fields.append(RecordField.objects.create(
            patient=patient,
            record_version_id=version.id,
            ordinal=ordinal,
            field_code=field.get("field_code"),
            concept_code=field.get("concept_code"),
            dictionary_entry_id=previous_fields[ordinal - 1].dictionary_entry_id,
            original_name=field.get('original_name'),
            value_type=field.get("value_type"),
            value_state=field.get("value_state", "known"),
            raw_text=field.get("raw_text"),
            raw_numeric=Decimal(str(field["raw_numeric"])) if field.get('value_state') == 'known' and field.get("raw_numeric") is not None else None,
            categorical_value=field.get("categorical_value"),
            qualifier=field.get("qualifier"),
            raw_unit=field.get("raw_unit"),
            method=field.get("method"),
            reference_interval=field.get("reference_interval"),
            report_flag=field.get('report_flag'),
            medical_time_id=medical_time.id if medical_time else None,
            sample_time_origin=field.get("sample_time_origin", "unknown"),
            anchor_id=previous_fields[ordinal - 1].anchor_id,
            confirmation_id=previous_fields[ordinal - 1].confirmation_id,
            quality_reasons=[],
        ))
    for attachment in RecordAttachment.objects.filter(record_version_id=previous.id, patient_id=patient.id):
        RecordAttachment.objects.create(record_version_id=version.id, patient_id=patient.id, file_id=attachment.file_id, ordinal=attachment.ordinal)
    if record.kind == Record.Kind.THERAPY:
        from records.therapy import copy_therapy_events

        copy_therapy_events(patient=patient, previous_version=previous, version=version, fields=created_fields)
    correct_record_facts(version, previous)
    from records.quality import validate_record_format

    validate_record_format(version)
    record.current_version = new_version
    record.save(update_fields=["current_version"])
    Patient.objects.filter(id=patient.id).update(data_epoch=F("data_epoch") + 1)
    return {"patient_id": str(patient.id), "record_id": str(record.id), "record_version_id": str(version.id), "version": new_version}
