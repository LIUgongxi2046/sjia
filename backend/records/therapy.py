"""治疗资料登记：原药名、状态、时间与完整子项。

家庭只保存原药名；IL-1/IL-6 专业分类不接受家庭赋值（T17 核对）；
结束时间 null 不推断持续；既往剂量不成为当前剂量。
"""
from datetime import datetime, timezone
from decimal import Decimal

from django.db import transaction

from case_details.models import MedicationEvent, MedicationEventItem
from medical_time.models import MedicalTime
from records.models import Record, RecordAttachment, RecordField, RecordVersion
from archives.record_facts import create_record_facts
from identity.models import Patient
from django.db.models import F


class TherapyRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _field_text(field):
    if field is None or field.value_state != 'known':
        return None
    return field.categorical_value if field.value_type == RecordField.ValueType.CATEGORICAL else field.raw_text


def _validate_item_pair(dose, dose_unit, interval_value, interval_unit):
    if (dose is None) != (dose_unit is None):
        raise TherapyRejected('THERAPY_DOSE_AND_UNIT_REQUIRED')
    if (interval_value is None) != (interval_unit is None):
        raise TherapyRejected('THERAPY_INTERVAL_AND_UNIT_REQUIRED')


def create_therapy_events(*, patient, version, fields):
    """为通用记录版本建立对应的家庭药物事件与分项。"""
    by_code = {}
    for field in fields:
        by_code.setdefault(field.field_code, []).append(field)
    medication_fields = by_code.get('medication_name', [])
    if not medication_fields:
        raise TherapyRejected('MEDICATION_NAME_REQUIRED')
    status_field = next(iter(by_code.get('therapy_status', [])), None)
    start_field = next(iter(by_code.get('therapy_start', [])), None)
    end_field = next(iter(by_code.get('therapy_end', [])), None)
    note_field = next(iter(by_code.get('therapy_note', [])), None)
    action_by_status = {
        'ongoing': MedicationEvent.Action.ONGOING,
        'ended': MedicationEvent.Action.STOPPED_BY_FAMILY,
        'not_started': MedicationEvent.Action.REGIMEN_RECORD,
    }
    status = status_field.categorical_value if status_field and status_field.value_state == 'known' else None
    if status not in (None, 'ongoing', 'ended', 'not_started'):
        raise TherapyRejected('THERAPY_STATUS_INVALID')
    action = action_by_status.get(status, MedicationEvent.Action.REGIMEN_RECORD)
    event_time = end_field if status == 'ended' else start_field if status == 'ongoing' else None
    dose_field = next(iter(by_code.get('dose', [])), None)
    dose_unit_field = next(iter(by_code.get('dose_unit', [])), None)
    interval_field = next(iter(by_code.get('interval_value', [])), None)
    interval_unit_field = next(iter(by_code.get('interval_unit', [])), None)
    dose = dose_field.raw_numeric if dose_field and dose_field.value_state == 'known' else None
    dose_unit = _field_text(dose_unit_field)
    interval_value = interval_field.raw_numeric if interval_field and interval_field.value_state == 'known' else None
    interval_unit = _field_text(interval_unit_field)
    _validate_item_pair(dose, dose_unit, interval_value, interval_unit)
    interval_units = {value for value, _ in MedicationEventItem.IntervalUnit.choices}
    if interval_unit is not None and interval_unit not in interval_units:
        raise TherapyRejected('THERAPY_INTERVAL_UNIT_INVALID')
    events = []
    for ordinal, medication_field in enumerate(medication_fields, start=1):
        if medication_field.value_state != 'known' or not medication_field.raw_text:
            raise TherapyRejected('MEDICATION_NAME_REQUIRED')
        event = MedicationEvent.objects.create(
            patient_id=patient.id,
            record_version_id=version.id,
            source_field_id=medication_field.id,
            action=action,
            medical_time_id=event_time.medical_time_id if event_time and event_time.value_state == 'known' else None,
            note=note_field.raw_text if note_field and note_field.value_state == 'known' else None,
        )
        concentration_field = next(iter(by_code.get('concentration_text', [])), None)
        route_field = next(iter(by_code.get('route_text', [])), None)
        frequency_field = next(iter(by_code.get('frequency_text', [])), None)
        item = MedicationEventItem.objects.create(
            event_id=event.id,
            patient_id=patient.id,
            record_version_id=version.id,
            source_field_id=medication_field.id,
            ordinal=ordinal,
            medication_name=medication_field.raw_text,
            dose=Decimal(str(dose)) if dose is not None else None,
            dose_unit=dose_unit,
            concentration_text=_field_text(concentration_field),
            route_text=_field_text(route_field),
            frequency_text=_field_text(frequency_field),
            interval_value=Decimal(str(interval_value)) if interval_value is not None else None,
            interval_unit=interval_unit,
        )
        events.append((event, item))
    return events


def copy_therapy_events(*, patient, previous_version, version, fields):
    """按新版本字段引用保留药物事件历史。"""
    from records.models import RecordField

    old_fields = {field.id: field.ordinal for field in RecordField.objects.filter(record_version_id=previous_version.id, patient=patient)}
    field_by_ordinal = {field.ordinal: field for field in fields}
    fields_by_code = {}
    for field in fields:
        fields_by_code.setdefault(field.field_code, []).append(field)
    for event in MedicationEvent.objects.filter(record_version_id=previous_version.id, patient_id=patient.id).order_by('id'):
        ordinal = old_fields.get(event.source_field_id)
        if ordinal not in field_by_ordinal:
            raise TherapyRejected('THERAPY_SOURCE_FIELD_MISSING')
        source_field = field_by_ordinal[ordinal]
        status_field = next((field for field in fields if field.field_code == 'therapy_status'), None)
        status = status_field.categorical_value if status_field and status_field.value_state == 'known' else None
        if status not in (None, 'ongoing', 'ended', 'not_started'):
            raise TherapyRejected('THERAPY_STATUS_INVALID')
        action_by_status = {'ongoing': MedicationEvent.Action.ONGOING, 'ended': MedicationEvent.Action.STOPPED_BY_FAMILY,
            'not_started': MedicationEvent.Action.REGIMEN_RECORD}
        time_code = 'therapy_end' if status == 'ended' else 'therapy_start' if status == 'ongoing' else None
        time_field = next((field for field in fields if field.field_code == time_code), None) if time_code else None
        note_field = next((field for field in fields if field.field_code == 'therapy_note'), None)
        event_copy = MedicationEvent.objects.create(
            patient_id=patient.id, record_version_id=version.id, source_field_id=source_field.id,
            action=action_by_status.get(status, MedicationEvent.Action.REGIMEN_RECORD),
            medical_time_id=time_field.medical_time_id if time_field and time_field.value_state == 'known' else None, previous_event_id=event.id,
            reason_statement_id=event.reason_statement_id,
            note=note_field.raw_text if note_field and note_field.value_state == 'known' else None,
        )
        old_items = MedicationEventItem.objects.filter(event_id=event.id, patient_id=patient.id).order_by('ordinal')
        for item in old_items:
            dose_field = next(iter(fields_by_code.get('dose', [])), None)
            dose_unit_field = next(iter(fields_by_code.get('dose_unit', [])), None)
            interval_field = next(iter(fields_by_code.get('interval_value', [])), None)
            interval_unit_field = next(iter(fields_by_code.get('interval_unit', [])), None)
            concentration_field = next(iter(fields_by_code.get('concentration_text', [])), None)
            route_field = next(iter(fields_by_code.get('route_text', [])), None)
            frequency_field = next(iter(fields_by_code.get('frequency_text', [])), None)
            dose = (Decimal(str(dose_field.raw_numeric)) if dose_field.value_state == 'known' and dose_field.raw_numeric is not None else None) if dose_field else item.dose
            dose_unit = _field_text(dose_unit_field) if dose_unit_field else item.dose_unit
            interval_value = (Decimal(str(interval_field.raw_numeric)) if interval_field.value_state == 'known' and interval_field.raw_numeric is not None else None) if interval_field else item.interval_value
            interval_unit = _field_text(interval_unit_field) if interval_unit_field else item.interval_unit
            _validate_item_pair(dose, dose_unit, interval_value, interval_unit)
            if interval_unit is not None and interval_unit not in {value for value, _ in MedicationEventItem.IntervalUnit.choices}:
                raise TherapyRejected('THERAPY_INTERVAL_UNIT_INVALID')
            MedicationEventItem.objects.create(
                event_id=event_copy.id, patient_id=patient.id, record_version_id=version.id,
                source_field_id=source_field.id, ordinal=item.ordinal, medication_name=source_field.raw_text,
                medication_code=item.medication_code,
                dose=dose, dose_unit=dose_unit,
                concentration_text=_field_text(concentration_field) if concentration_field else item.concentration_text,
                route_text=_field_text(route_field) if route_field else item.route_text,
                frequency_text=_field_text(frequency_field) if frequency_field else item.frequency_text,
                interval_value=interval_value, interval_unit=interval_unit,
            )


@transaction.atomic
def create_therapy_record(
    *,
    patient,
    actor,
    retention_policy,
    use_authorization_id,
    medication_name,
    action,
    medical_time=None,
    dose=None,
    dose_unit=None,
    interval_value=None,
    interval_unit=None,
    note=None,
    source_revision=None,
    capture_method="manual",
) -> dict:
    Patient.objects.select_for_update().get(id=patient.id)
    if not medication_name:
        raise TherapyRejected("MEDICATION_NAME_REQUIRED")
    now = datetime.now(timezone.utc)
    record = Record.objects.create(patient=patient, kind=Record.Kind.THERAPY, current_version=1, status=Record.Status.ACTIVE, created_at=now)
    version = RecordVersion.objects.create(
        patient=patient,
        record_id=record.id,
        version=1,
        author=actor,
        origin_type=RecordVersion.OriginType.SELF_REPORT,
        capture_method=capture_method,
        source_revision=source_revision,
        use_authorization_id=use_authorization_id,
        quality_status=RecordVersion.QualityStatus.PENDING,
        quality_reasons=[],
        recorded_at=now,
        retention_policy=retention_policy,
    )
    field = RecordField.objects.create(
        patient=patient,
        record_version_id=version.id,
        ordinal=1,
        field_code="medication",
        value_type=RecordField.ValueType.TEXT,
        value_state=RecordField.ValueState.KNOWN,
        raw_text=medication_name,
        sample_time_origin=RecordField.SampleTimeOrigin.THERAPY,
        quality_reasons=[],
    )
    time_row = None
    if medical_time:
        time_row = MedicalTime.objects.create(
            patient=patient,
            raw_text=medical_time.get("raw_text"),
            precision=medical_time.get("precision", "unknown"),
            local_year=medical_time.get("local_year"),
            local_month=medical_time.get("local_month"),
            end_year=medical_time.get("end_year"),
            end_month=medical_time.get("end_month"),
            relative_text=medical_time.get("relative_text"),
            local_date=medical_time.get("local_date"),
            local_datetime=medical_time.get("local_datetime"),
            timezone_name=medical_time.get("timezone_name"),
            utc_offset_minutes=medical_time.get("utc_offset_minutes"),
            resolution_status=medical_time.get("resolution_status", "unknown"),
        )
        field.medical_time_id = time_row.id
        field.save(update_fields=['medical_time_id'])
    event = MedicationEvent.objects.create(
        patient_id=patient.id,
        record_version_id=version.id,
        source_field_id=field.id,
        action=action,
        medical_time_id=time_row.id if time_row else None,
        note=note,
    )
    item = MedicationEventItem.objects.create(
        event_id=event.id,
        patient_id=patient.id,
        record_version_id=version.id,
        source_field_id=field.id,
        ordinal=1,
        medication_name=medication_name,
        dose=Decimal(str(dose)) if dose is not None else None,
        dose_unit=dose_unit,
        interval_value=Decimal(str(interval_value)) if interval_value is not None else None,
        interval_unit=interval_unit,
    )
    create_record_facts(version)
    from records.quality import validate_record_format

    validate_record_format(version)
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    return {
        "record_id": str(record.id),
        "record_version_id": str(version.id),
        "medication_event_id": str(event.id),
        "medication_event_item_id": str(item.id),
    }


def read_therapy_record(record_id, patient) -> dict:
    record = Record.objects.filter(id=record_id, patient=patient, kind=Record.Kind.THERAPY).first()
    if record is None:
        raise TherapyRejected("RECORD_NOT_FOUND")
    version = RecordVersion.objects.filter(record_id=record.id, patient=patient, version=record.current_version).first()
    events = list(MedicationEvent.objects.filter(record_version_id=version.id, patient_id=patient.id).order_by("id"))
    items = list(MedicationEventItem.objects.filter(record_version_id=version.id, patient_id=patient.id).order_by("ordinal"))
    from records.service import medical_time_view

    fields = list(RecordField.objects.filter(record_version_id=version.id, patient=patient).order_by('ordinal'))
    field_values = {field.field_code: field for field in fields}
    events_view = []
    for event in events:
        event_items = [item for item in items if str(item.event_id) == str(event.id)]
        events_view.append({
            "medication_event_id": str(event.id), "previous_event_id": str(event.previous_event_id) if event.previous_event_id else None,
            "source_field_id": str(event.source_field_id), "action": event.action,
            "medical_time": medical_time_view(event.medical_time_id, patient), "note": event.note,
            "items": [{"medication_event_item_id": str(item.id), "source_field_id": str(item.source_field_id),
                "ordinal": item.ordinal, "medication_name": item.medication_name, "medication_code": item.medication_code,
                "dose": str(item.dose) if item.dose is not None else None, "dose_unit": item.dose_unit,
                "concentration_text": item.concentration_text, "route_text": item.route_text,
                "frequency_text": item.frequency_text,
                "interval_value": str(item.interval_value) if item.interval_value is not None else None,
                "interval_unit": item.interval_unit} for item in event_items],
        })
    return {
        "record_id": str(record.id),
        "record_version_id": str(version.id),
        "version": version.version,
        "kind": record.kind,
        "capture_method": version.capture_method,
        "origin_type": version.origin_type,
        "correction_reason": version.correction_reason,
        "recorded_at": version.recorded_at.isoformat(),
        "status": {"value_state": field_values['therapy_status'].value_state, "value": field_values['therapy_status'].categorical_value} if 'therapy_status' in field_values else {"value_state": "unknown", "value": None},
        "start_time": {"value_state": field_values['therapy_start'].value_state, "value": medical_time_view(field_values['therapy_start'].medical_time_id, patient)} if 'therapy_start' in field_values else {"value_state": "unknown", "value": None},
        "end_time": {"value_state": field_values['therapy_end'].value_state, "value": medical_time_view(field_values['therapy_end'].medical_time_id, patient)} if 'therapy_end' in field_values else {"value_state": "unknown", "value": None},
        "attachments": [{"file_id": str(item.file_id), "ordinal": item.ordinal} for item in RecordAttachment.objects.filter(record_version_id=version.id, patient_id=patient.id).order_by('ordinal')],
        "events": events_view,
        "items": [
            {
                "medication_name": i.medication_name,
                "dose": str(i.dose) if i.dose is not None else None,
                "dose_unit": i.dose_unit,
                "interval_value": str(i.interval_value) if i.interval_value is not None else None,
                "interval_unit": i.interval_unit,
            }
            for i in items
        ],
    }
