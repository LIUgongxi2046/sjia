from case_details.models import MedicationEvent, MedicationEventItem
from records.models import RecordField
from api.base import ApiError
from audit.models import AuditEvent
from audit.service import write_audit
from records.models import RecordVersion


def require_therapy_confirmation(data):
    if data.get('family_confirmation') is not True:
        raise ApiError('THERAPY_FAMILY_CONFIRMATION_REQUIRED', 422, '请本人确认患儿、资料来源、使用状态及独立起止时间。')


def save_therapy_confirmation(*, result, actor, patient, intent, retention):
    version = RecordVersion.objects.get(id=result['record_version_id'], patient=patient, author=actor,
        version=result['version'])
    if intent.status != 'committed' or intent.write_request is None:
        raise ApiError('THERAPY_CONFIRMATION_REQUEST_REQUIRED', 409)
    return write_audit(actor=actor, patient=patient, action='therapy_family_confirmed',
        object_type='record_version', object_id=version.id, object_version=str(version.version),
        request_id=intent.request_id, outcome='committed', retention_policy=retention,
        reason_code='SOURCE_USE_STATUS_START_END_CONFIRMED', occurred_at=intent.completed_at)


def therapy_confirmation_view(record, principal):
    version = RecordVersion.objects.get(id=record['record_version_id'], patient_id=record['patient_id'])
    proofs = list(AuditEvent.objects.filter(patient_id=version.patient_id, actor_id=version.author_id,
        action='therapy_family_confirmed', object_type='record_version', object_id=version.id,
        object_version=str(version.version), outcome='committed'))
    if not proofs:
        return None
    if len(proofs) != 1 or proofs[0].request_id is None:
        raise ApiError('THERAPY_CONFIRMATION_PROOF_INVALID', 409)
    codes = {field['field_code'] for field in record['fields']}
    scope = [name for name, code in (('source', 'medication_name'), ('use_status', 'therapy_status'),
        ('start_time', 'therapy_start'), ('end_time', 'therapy_end')) if code in codes]
    if not scope:
        return None
    return {'confirmed_by_self': proofs[0].actor_id == principal.id,
        'confirmed_at': proofs[0].occurred_at.isoformat(), 'scope': scope}


def therapy_events_view(record, *, include_history=False):
    visible = {field['field_id']: field for field in record['fields']}
    codes = {field['field_code']: field for field in record['fields']}
    rows = list(RecordField.objects.filter(patient_id=record['patient_id'],
        record_version_id=record['record_version_id']).order_by('ordinal'))
    fields = {str(field.id): field for field in rows}
    events = MedicationEvent.objects.filter(patient_id=record['patient_id'],
        record_version_id=record['record_version_id']).order_by('id')
    result = []
    item_codes = ('dose', 'dose_unit', 'concentration_text', 'route_text', 'frequency_text', 'interval_value', 'interval_unit')
    for event in events:
        source_id = str(event.source_field_id)
        if source_id not in fields:
            raise ApiError('THERAPY_SOURCE_FIELD_MISMATCH', 409)
        if source_id not in visible:
            continue
        status_field = codes.get('therapy_status')
        status = {'value_state': status_field['value_state'], 'value': status_field['categorical_value']} if status_field else None
        times = [field for field in record['fields'] if field['field_code'] in ('therapy_start', 'therapy_end')
            and field['medical_time_id'] is not None and field['medical_time_id'] == str(event.medical_time_id)]
        value = {'medication_event_id': str(event.id), 'source_field_id': source_id,
            'previous_event_id': str(event.previous_event_id) if include_history and event.previous_event_id else None,
            'status': status, 'action': event.action if status and status['value_state'] == 'known' else None,
            'medical_time': times[0]['medical_time'] if times else None, 'items': []}
        note = codes.get('therapy_note')
        if note is not None:
            value['note'] = event.note
        for item in MedicationEventItem.objects.filter(event_id=event.id, patient_id=record['patient_id'],
                record_version_id=record['record_version_id']).order_by('ordinal', 'id'):
            item_source = str(item.source_field_id)
            if item_source not in fields:
                raise ApiError('THERAPY_ITEM_SOURCE_FIELD_MISMATCH', 409)
            if item_source not in visible:
                continue
            item_view = {'medication_event_item_id': str(item.id), 'source_field_id': item_source,
                'ordinal': item.ordinal, 'medication_name': item.medication_name}
            for code in item_codes:
                if code in codes:
                    scalar = getattr(item, code)
                    item_view[code] = str(scalar) if code in ('dose', 'interval_value') and scalar is not None else scalar
            value['items'].append(item_view)
        result.append(value)
    return result
