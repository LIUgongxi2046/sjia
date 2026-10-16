from datetime import datetime, timezone
from decimal import Decimal

from django.db import transaction
from django.db.models import F

from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion, RecordAttachment, Report
from identity.models import Patient
from files.models import FileObject, FilePage
from recognition.models import SourceAnchor
from archives.record_facts import create_record_facts, record_fact_references
from records.medical_times import create_medical_time


class RecordRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


PROTECTED_FIELDS = frozenset(
    {
        "patient_id",
        "author_id",
        "verifier_id",
        "verification_status",
        "quality_status",
        "rule_result",
        "approved_by",
        "permissions",
    }
)


def _validate_field_input(field: dict) -> None:
    if not isinstance(field, dict):
        raise RecordRejected("FIELD_INVALID")
    unknown = set(field.keys()) & PROTECTED_FIELDS
    if unknown:
        raise RecordRejected("PROTECTED_FIELD")
    value_type = field.get("value_type")
    value_state = field.get("value_state")
    if not field.get('field_code') or value_type not in RecordField.ValueType.values or value_state not in RecordField.ValueState.values:
        raise RecordRejected('FIELD_INVALID')
    if field.get('qualifier') is not None and field['qualifier'] not in RecordField.Qualifier.values:
        raise RecordRejected('QUALIFIER_INVALID')
    if field.get('source_file_id') and (not isinstance(field.get('source_text'), str) or not field['source_text'].strip()):
        raise RecordRejected('FIELD_SOURCE_TEXT_REQUIRED')
    if value_state == "known":
        if value_type == "decimal" and field.get("raw_numeric") is None:
            raise RecordRejected("FIELD_DECIMAL_REQUIRED")
        if value_type == "categorical" and field.get("categorical_value") is None:
            raise RecordRejected("FIELD_CATEGORICAL_REQUIRED")
        if value_type == "text" and not field.get("raw_text"):
            raise RecordRejected("FIELD_TEXT_REQUIRED")
        if value_type == 'decimal' and not Decimal(str(field['raw_numeric'])).is_finite():
            raise RecordRejected('FIELD_DECIMAL_INVALID')
        if value_type == 'time' and not field.get('medical_time'):
            raise RecordRejected('FIELD_TIME_REQUIRED')


def _create_medical_time(patient, time_input: dict | None) -> MedicalTime | None:
    if not time_input:
        return None
    return create_medical_time(patient, time_input)


@transaction.atomic
def create_record(
    *,
    patient,
    actor,
    kind,
    capture_method,
    origin_type,
    retention_policy,
    use_authorization_id,
    fields,
    source_revision=None,
    source_organization=None,
    recorded_at=None,
    file_ids=None,
    report_metadata=None,
) -> dict:
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if kind not in Record.Kind.values or capture_method not in RecordVersion.CaptureMethod.values:
        raise RecordRejected('RECORD_TYPE_INVALID')
    if not isinstance(fields, list) or not fields:
        raise RecordRejected('RECORD_FIELDS_REQUIRED')
    for field in fields:
        _validate_field_input(field)
        if field.get('dictionary_entry_id'):
            from dictionaries.service import validate_entry

            validate_entry(field['dictionary_entry_id'], field.get('concept_code'))
    attachments = []
    for file_id in file_ids or []:
        attachment = FileObject.objects.filter(id=file_id, patient=patient, status='available', purpose='original').first()
        if attachment is None:
            raise RecordRejected('ATTACHMENT_UNAVAILABLE')
        attachments.append(attachment)
    if len({item.id for item in attachments}) != len(attachments) or len(attachments) > 10:
        raise RecordRejected('ATTACHMENTS_INVALID')
    now = recorded_at or datetime.now(timezone.utc)
    report = None
    if kind == 'report':
        metadata = report_metadata or {}
        report = Report.objects.create(patient=patient, source_revision=source_revision, external_ref=metadata.get('external_ref'), source_organization=source_organization, report_status=metadata.get('report_status', 'unknown'), sample_time_id=None, result_time_id=None, file_group_id=attachments[0].file_group_id if attachments else None, created_at=now)
        sample_time = _create_medical_time(patient, metadata.get('sample_time'))
        result_time = _create_medical_time(patient, metadata.get('result_time'))
        report.sample_time_id = sample_time.id if sample_time else None
        report.result_time_id = result_time.id if result_time else None
        report.save(update_fields=['sample_time_id', 'result_time_id'])
    record = Record.objects.create(
        patient=patient,
        kind=kind,
        current_version=1,
        status=Record.Status.ACTIVE,
        created_at=now,
    )
    version = RecordVersion.objects.create(
        patient=patient,
        record_id=record.id,
        version=1,
        author=actor,
        origin_type=origin_type,
        capture_method=capture_method,
        source_revision=source_revision,
        source_organization=source_organization,
        report_id=report.id if report else None,
        use_authorization_id=use_authorization_id,
        quality_status=RecordVersion.QualityStatus.PENDING,
        quality_reasons=[],
        recorded_at=now,
        retention_policy=retention_policy,
    )
    created_fields = []
    for ordinal, field in enumerate(fields, start=1):
        _validate_field_input(field)
        medical_time = _create_medical_time(patient, field.get("medical_time"))
        anchor_id = field.get('anchor_id')
        if field.get('source_file_id'):
            if str(field['source_file_id']) not in {str(item.id) for item in attachments}:
                raise RecordRejected('FIELD_SOURCE_NOT_ATTACHED')
            page = FilePage.objects.get(patient_id=patient.id, file_id=field['source_file_id'])
            anchor = SourceAnchor.objects.create(patient=patient, page_id=page.id, anchor_kind='page', coordinate_system='decoded_pixel', original_text=field['source_text'])
            anchor_id = anchor.id
        elif anchor_id:
            anchor = SourceAnchor.objects.filter(id=anchor_id, patient=patient).first()
            if anchor is None or not FilePage.objects.filter(id=anchor.page_id, patient_id=patient.id, file_id__in=[item.id for item in attachments]).exists():
                raise RecordRejected('FIELD_SOURCE_NOT_ATTACHED')
        created_fields.append(RecordField.objects.create(
            patient=patient,
            record_version_id=version.id,
            ordinal=ordinal,
            field_code=field.get("field_code"),
            concept_code=field.get("concept_code"),
            dictionary_entry_id=field.get('dictionary_entry_id'),
            original_name=field.get("original_name"),
            value_type=field.get("value_type"),
            value_state=field.get("value_state", "known"),
            raw_text=field.get("raw_text"),
            raw_numeric=Decimal(str(field["raw_numeric"])) if field.get('value_state') == 'known' and field.get("raw_numeric") is not None else None,
            categorical_value=field.get("categorical_value"),
            qualifier=field.get("qualifier"),
            raw_unit=field.get("raw_unit"),
            method=field.get("method"),
            reference_interval=field.get("reference_interval"),
            report_flag=field.get("report_flag"),
            medical_time_id=medical_time.id if medical_time else None,
            sample_time_origin=field.get("sample_time_origin", "unknown"),
            anchor_id=anchor_id,
            confirmation_id=field.get("confirmation_id"),
            quality_reasons=[],
        ))
    for ordinal, attachment in enumerate(attachments, start=1):
        RecordAttachment.objects.create(record_version_id=version.id, patient_id=patient.id, file_id=attachment.id, ordinal=ordinal)
    if kind == Record.Kind.THERAPY:
        from records.therapy import create_therapy_events

        create_therapy_events(patient=patient, version=version, fields=created_fields)
    create_record_facts(version)
    from records.quality import validate_record_format

    validate_record_format(version)
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    return {"patient_id": str(patient.id), "record_id": str(record.id), "record_version_id": str(version.id), "version": 1}


def read_record(record_id, patient) -> dict:
    record = Record.objects.filter(id=record_id, patient=patient).first()
    if record is None:
        raise RecordRejected("RECORD_NOT_FOUND")
    version = RecordVersion.all_objects.filter(record_id=record.id, patient=patient, version=record.current_version).first()
    if version is None:
        raise RecordRejected("RECORD_VERSION_NOT_FOUND")
    return read_version(version, record)


def read_version(version, record):
    from retention.content_fences import assert_content_available
    assert_content_available('records.recordversion', version.id)
    version = RecordVersion.objects.get(id=version.id, patient_id=record.patient_id)
    from verification.models import FieldVerificationState

    patient = record.patient
    fields = list(RecordField.objects.filter(record_version_id=version.id, patient=patient).order_by("ordinal"))
    states = {}
    for state in FieldVerificationState.objects.filter(patient_id=patient.id, record_version_id=version.id):
        states.setdefault(state.field_id, []).append({'purpose': state.purpose, 'status': state.status, 'version': str(state.version)})
    report = Report.objects.filter(id=version.report_id, patient=patient).first() if version.report_id else None
    anchors = {anchor.id: anchor for anchor in SourceAnchor.objects.filter(patient=patient, id__in=[field.anchor_id for field in fields if field.anchor_id])}
    pages = {page.id: page for page in FilePage.objects.filter(patient_id=patient.id, id__in=[anchor.page_id for anchor in anchors.values()])}
    return {
        "record_id": str(record.id),
        "kind": record.kind,
        "patient_id": str(patient.id),
        "record_version_id": str(version.id),
        "version": version.version,
        "origin_type": version.origin_type,
        "capture_method": version.capture_method,
        "quality_status": version.quality_status,
        "source_organization": version.source_organization,
        "correction_reason": version.correction_reason,
        "corrected_at": version.corrected_at.isoformat() if version.corrected_at else None,
        "recorded_at": version.recorded_at.isoformat(),
        'fact_refs': record_fact_references(version, fields),
        'report': {'external_ref': report.external_ref, 'report_status': report.report_status, 'sample_time': medical_time_view(report.sample_time_id, patient), 'result_time': medical_time_view(report.result_time_id, patient)} if report else None,
        "fields": [
            {
                "field_code": f.field_code,
                "field_id": str(f.id),
                "concept_code": f.concept_code,
                'dictionary_entry_id': str(f.dictionary_entry_id) if f.dictionary_entry_id else None,
                "original_name": f.original_name,
                "value_type": f.value_type,
                "value_state": f.value_state,
                "raw_text": f.raw_text,
                "raw_numeric": str(f.raw_numeric) if f.raw_numeric is not None else None,
                "categorical_value": f.categorical_value,
                "qualifier": f.qualifier,
                "raw_unit": f.raw_unit,
                "medical_time_id": str(f.medical_time_id) if f.medical_time_id else None,
                "sample_time_origin": f.sample_time_origin,
                "method": f.method,
                "reference_interval": f.reference_interval,
                "report_flag": f.report_flag,
                "verification_states": states.get(f.id, []),
                "confirmation_id": str(f.confirmation_id) if f.confirmation_id else None,
                "source_anchor": {'anchor_kind': anchors[f.anchor_id].anchor_kind, 'page_id': str(anchors[f.anchor_id].page_id), 'page_ordinal': pages[anchors[f.anchor_id].page_id].ordinal, 'file_id': str(pages[anchors[f.anchor_id].page_id].file_id), 'original_text': anchors[f.anchor_id].original_text} if f.anchor_id else None,
                "medical_time": medical_time_view(f.medical_time_id, patient),
            }
            for f in fields
        ],
        'attachments': [{'file_id': str(item.file_id), 'ordinal': item.ordinal} for item in RecordAttachment.objects.filter(record_version_id=version.id, patient_id=patient.id).order_by('ordinal')],
    }


def medical_time_view(time_id, patient):
    if time_id is None:
        return None
    value = MedicalTime.objects.get(id=time_id, patient=patient)
    return {
        'precision': value.precision, 'raw_text': value.raw_text,
        'local_date': value.local_date.isoformat() if value.local_date else None,
        'local_datetime': value.local_datetime.isoformat() if value.local_datetime else None,
        'instant': value.instant.isoformat() if value.instant else None,
        'timezone_name': value.timezone_name,
        'resolution_status': value.resolution_status,
        'local_year': value.local_year, 'local_month': value.local_month,
        'end_year': value.end_year, 'end_month': value.end_month,
        'relative_text': value.relative_text, 'utc_offset_minutes': value.utc_offset_minutes,
    }
