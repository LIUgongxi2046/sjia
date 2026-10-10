from django.db import transaction
from django.utils import timezone

from archives.models import ArchiveFact, ArchiveFactVersion
from identity.models import Patient
from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion, RecordAttachment
from records.queries import QueryRejected
from recognition.models import SourceAnchor
from files.models import FilePage, FileObject


def record_fact_references(version, fields):
    rows = {row.field_id: row for row in ArchiveFactVersion.objects.filter(
        patient_id=version.patient_id, record_version_id=version.id, field_id__in=[field.id for field in fields])}
    if len(rows) != len(fields):
        raise QueryRejected('FACT_REFERENCE_INCOMPLETE')
    references = []
    for field in fields:
        row = rows[field.id]
        if (field.patient_id != version.patient_id or field.record_version_id != version.id
            or row.medical_time_id != field.medical_time_id or row.source_anchor_id != field.anchor_id
            or row.confirmation_id != field.confirmation_id):
            raise QueryRejected('FACT_SOURCE_INVALID')
        references.append({'field_id': str(field.id), 'fact_id': str(row.fact_id),
            'version_id': str(row.id), 'version': str(row.version)})
    return references


def source_available(field, version):
    if field.anchor_id is None:
        return
    anchor = SourceAnchor.objects.get(id=field.anchor_id, patient_id=version.patient_id)
    page = FilePage.objects.get(id=anchor.page_id, patient_id=version.patient_id)
    FileObject.objects.get(id=page.file_id, patient_id=version.patient_id, purpose='original', status='available')
    if not RecordAttachment.objects.filter(record_version_id=version.id, patient_id=version.patient_id, file_id=page.file_id).exists():
        raise QueryRejected('FACT_SOURCE_NOT_ATTACHED')


def medical_time_content(time_id, patient_id):
    if time_id is None:
        return None
    value = MedicalTime.objects.get(id=time_id, patient_id=patient_id)
    return {name: getattr(value, name) for name in ['precision', 'raw_text', 'local_date', 'local_datetime',
        'local_year', 'local_month', 'end_year', 'end_month', 'relative_text', 'timezone_name',
        'utc_offset_minutes', 'instant', 'resolution_status']}


def field_time_origin(field):
    if field.sample_time_origin in ('item', 'observation', 'therapy'):
        return field.sample_time_origin
    return 'unknown'


def initial_fact_content(field, version):
    if field.patient_id != version.patient_id or field.record_version_id != version.id:
        raise QueryRejected('FACT_SOURCE_INVALID')
    source_available(field, version)
    time = medical_time_content(field.medical_time_id, version.patient_id)
    return {'record_version_id': version.id, 'field_id': field.id,
        'document_version_id': None, 'page_id': None, 'source_anchor_id': field.anchor_id,
        'context_type': 'unknown', 'assertion_state': 'unknown', 'episode_id': None,
        'medical_time_id': field.medical_time_id, 'raw_time_text': time['raw_text'] if time is not None else None,
        'time_origin': field_time_origin(field), 'documented_time_id': None, 'confirmation_id': field.confirmation_id}


def correction_fact_content(field, version, previous_field, previous_fact):
    if (version.patient_id != previous_fact.patient_id or field.patient_id != version.patient_id
        or field.record_version_id != version.id or previous_field.id != previous_fact.field_id
        or previous_field.record_version_id != previous_fact.record_version_id
        or previous_field.patient_id != version.patient_id or field.ordinal != previous_field.ordinal
        or field.field_code != previous_field.field_code or field.anchor_id != previous_fact.source_anchor_id
        or previous_field.medical_time_id != previous_fact.medical_time_id
        or previous_field.confirmation_id != previous_fact.confirmation_id):
        raise QueryRejected('FACT_CORRECTION_SOURCE_INVALID')
    source_available(field, version)
    before = medical_time_content(previous_field.medical_time_id, version.patient_id)
    after = medical_time_content(field.medical_time_id, version.patient_id)
    same_time = before == after and previous_field.sample_time_origin == field.sample_time_origin
    return {'record_version_id': version.id, 'field_id': field.id,
        'document_version_id': previous_fact.document_version_id, 'page_id': previous_fact.page_id,
        'source_anchor_id': field.anchor_id, 'context_type': previous_fact.context_type,
        'assertion_state': previous_fact.assertion_state, 'episode_id': previous_fact.episode_id,
        'medical_time_id': field.medical_time_id,
        'raw_time_text': previous_fact.raw_time_text if same_time else after['raw_text'] if after is not None else None,
        'time_origin': previous_fact.time_origin if same_time else field_time_origin(field),
        'documented_time_id': previous_fact.documented_time_id, 'confirmation_id': field.confirmation_id}


@transaction.atomic
def create_record_facts(version):
    Patient.objects.select_for_update().get(id=version.patient_id)
    record = Record.objects.select_for_update().get(id=version.record_id, patient_id=version.patient_id)
    if version.version != 1 or record.current_version != 1 or record.status != 'active':
        raise QueryRejected('FACT_INITIAL_VERSION_INVALID')
    fields = list(RecordField.objects.filter(record_version_id=version.id, patient_id=version.patient_id).order_by('ordinal'))
    if not fields or ArchiveFactVersion.objects.filter(record_version_id=version.id).exists():
        raise QueryRejected('FACT_INITIAL_VERSION_INVALID')
    contents = [initial_fact_content(field, version) for field in fields]
    now = timezone.now()
    for content in contents:
        fact = ArchiveFact.objects.create(patient_id=version.patient_id, current_version=1, created_at=now)
        ArchiveFactVersion.objects.create(patient_id=version.patient_id, fact_id=fact.id, version=1,
            **content, available_at=now, availability_status='known', created_at=now)


@transaction.atomic
def correct_record_facts(version, previous):
    Patient.objects.select_for_update().get(id=version.patient_id)
    record = Record.objects.select_for_update().get(id=version.record_id, patient_id=version.patient_id)
    if (previous.patient_id != version.patient_id or previous.record_id != version.record_id
        or version.supersedes_version != previous.version or version.version != previous.version + 1
        or record.current_version != previous.version or record.status != 'active'
        or not isinstance(version.correction_reason, str) or not version.correction_reason.strip()):
        raise QueryRejected('FACT_CORRECTION_VERSION_INVALID')
    fields = list(RecordField.objects.filter(record_version_id=version.id, patient_id=version.patient_id).order_by('ordinal'))
    previous_fields = list(RecordField.objects.filter(record_version_id=previous.id, patient_id=version.patient_id).order_by('ordinal'))
    if not fields or len(fields) != len(previous_fields):
        raise QueryRejected('FACT_CORRECTION_FIELDS_INCOMPLETE')
    references = record_fact_references(previous, previous_fields)
    fact_ids = [reference['fact_id'] for reference in references]
    facts = {str(fact.id): fact for fact in ArchiveFact.objects.select_for_update().filter(
        id__in=fact_ids, patient_id=version.patient_id).order_by('id')}
    if len(facts) != len(references):
        raise QueryRejected('FACT_CORRECTION_FIELDS_INCOMPLETE')
    changes = []
    for field, previous_field, reference in zip(fields, previous_fields, references, strict=True):
        fact = facts[reference['fact_id']]
        saved = ArchiveFactVersion.objects.get(id=reference['version_id'], patient_id=version.patient_id, fact_id=fact.id)
        if fact.current_version != saved.version:
            raise QueryRejected('VERSION_CONFLICT')
        changes.append((fact, saved, correction_fact_content(field, version, previous_field, saved)))
    now = timezone.now()
    for fact, saved, content in changes:
        ArchiveFactVersion.objects.create(patient_id=version.patient_id, fact_id=fact.id, version=saved.version + 1,
            **content, available_at=now, availability_status='known', created_at=now,
            supersedes_version=saved.version, reason=version.correction_reason)
        fact.current_version = saved.version + 1
        fact.save(update_fields=['current_version'])
