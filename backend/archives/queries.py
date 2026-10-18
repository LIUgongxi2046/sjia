from datetime import datetime, timezone

from django.core import signing
from django.db.models import OuterRef, Subquery
from archives.models import ArchiveFact, ArchiveFactVersion, ArchiveDocumentVersion, ArchiveDocumentPage, ArchivePage
from files.models import FilePage, FileObject
from recognition.models import SourceAnchor
from records.models import RecordField, RecordVersion
from records.service import medical_time_view
from records.queries import QueryRejected
from verification.models import FieldVerificationState, Verification, VerificationField
from configurations.service import compute_checksum


def current_fact_versions(facts, patient):
    facts = list(facts)
    if any(fact.patient_id != patient.id for fact in facts):
        raise QueryRejected('FACT_PATIENT_INVALID')
    current = ArchiveFact.objects.filter(id=OuterRef('fact_id'), patient_id=patient.id).values('current_version')[:1]
    rows = list(ArchiveFactVersion.objects.filter(patient_id=patient.id,
        fact_id__in=[fact.id for fact in facts], version=Subquery(current)))
    versions = {row.fact_id: row for row in rows}
    if len(rows) != len(versions) or set(versions) != {fact.id for fact in facts}:
        raise ArchiveFactVersion.DoesNotExist('当前事实版本无法读取。')
    if any(versions[fact.id].version != fact.current_version for fact in facts):
        raise QueryRejected('FACT_VERSION_INVALID')
    return versions


def checked_fact_source(version, patient, field, record):
    if field is None or field.record_version_id != version.record_version_id:
        raise RecordField.DoesNotExist('事实字段及其记录版本无法读取。')
    if record is None:
        raise RecordVersion.DoesNotExist('事实记录版本无法读取。')
    if (version.patient_id != patient.id or version.medical_time_id != field.medical_time_id
        or version.source_anchor_id != field.anchor_id or version.confirmation_id != field.confirmation_id):
        raise QueryRejected('FACT_SOURCE_INVALID')
    return field, record


def fact_sources(versions, patient):
    versions = list(versions)
    fields = RecordField.objects.filter(patient=patient, id__in=[version.field_id for version in versions]).in_bulk()
    records = RecordVersion.objects.filter(patient=patient, id__in=[version.record_version_id for version in versions]).in_bulk()
    return {version.id: checked_fact_source(version, patient, fields.get(version.field_id),
        records.get(version.record_version_id)) for version in versions}


def fact_source(version, patient):
    return fact_sources([version], patient)[version.id]


def fact_read(*, fact, patient, version=None, allowed_actions=(), read_context=None):
    if fact.patient_id != patient.id:
        raise QueryRejected('FACT_PATIENT_INVALID')
    if read_context is not None and read_context.patient.id != patient.id:
        raise QueryRejected('FACT_PATIENT_INVALID')

    def get(model, **scope):
        if read_context is None:
            return model.objects.get(**scope)
        return read_context.get(model, **scope)

    if version is None:
        version = get(ArchiveFactVersion, fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
    if version.fact_id != fact.id or version.patient_id != patient.id:
        raise QueryRejected('FACT_VERSION_INVALID')
    field, record_version = fact_source(version, patient) if read_context is None else read_context.fact_source(version)
    anchor = get(SourceAnchor, id=field.anchor_id, patient_id=patient.id) if field.anchor_id is not None else None
    file_page = get(FilePage, id=anchor.page_id, patient_id=patient.id) if anchor is not None else None
    if file_page is not None:
        source_file = get(FileObject, id=file_page.file_id, patient_id=patient.id)
    if version.page_id is not None:
        page = get(ArchivePage, id=version.page_id, patient_id=patient.id)
        if file_page is None or page.file_page_id != file_page.id or page.file_id != file_page.file_id:
            raise QueryRejected('FACT_SOURCE_INVALID')
        if page.file_object_version != source_file.object_version or page.checksum != source_file.checksum:
            raise QueryRejected('FACT_SOURCE_INVALID')
    if version.document_version_id is not None:
        get(ArchiveDocumentVersion, id=version.document_version_id, patient_id=patient.id)
        if version.page_id is None:
            raise QueryRejected('FACT_SOURCE_INVALID')
        page_scope = {'patient_id': patient.id, 'document_version_id': version.document_version_id, 'page_id': version.page_id}
        page_exists = (ArchiveDocumentPage.objects.filter(**page_scope).exists() if read_context is None
            else read_context.exists(ArchiveDocumentPage, **page_scope))
        if not page_exists:
            raise QueryRejected('FACT_SOURCE_INVALID')
    verification_scope = {'patient_id': patient.id, 'field_id': field.id,
        'record_version_id': record_version.id, 'purpose': 'family_observation'}
    verification = (FieldVerificationState.objects.filter(**verification_scope).first() if read_context is None
        else read_context.first(FieldVerificationState, **verification_scope))
    verification_ids = []
    if verification is not None and verification.status == 'verified' and verification.current_verification_id is None:
        raise QueryRejected('FACT_VERIFICATION_INVALID')
    if verification is not None and verification.current_verification_id is not None:
        proof = get(Verification, id=verification.current_verification_id, patient_id=patient.id,
            record_version_id=record_version.id, purpose=verification.purpose)
        proof_field = get(VerificationField, verification_id=proof.id, patient_id=patient.id,
            record_version_id=record_version.id, field_id=field.id)
        if verification.status == 'verified' and proof.revoked_at is not None:
            raise QueryRejected('FACT_VERIFICATION_INVALID')
        if verification.status == 'verified' and proof_field.result != 'consistent':
            raise QueryRejected('FACT_VERIFICATION_INVALID')
        verification_ids = [str(proof.id)]
    source = {'source_revision_id': str(record_version.source_revision_id) if record_version.source_revision_id else None,
        'record_version_id': str(record_version.id), 'field_id': str(field.id),
        'file_id': str(file_page.file_id) if file_page is not None else None,
        'page_id': str(file_page.id) if file_page is not None else None,
        'anchor_id': str(anchor.id) if anchor is not None else None,
        'verification_ids': verification_ids, 'mapping_id': None}
    clinical = {name: getattr(field, name) for name in ['ordinal', 'field_code', 'concept_code', 'original_name',
        'value_type', 'value_state', 'raw_text', 'categorical_value', 'qualifier', 'raw_unit', 'method',
        'reference_interval', 'report_flag', 'sample_time_origin', 'quality_reasons']}
    clinical.update(field_id=str(field.id), dictionary_entry_id=str(field.dictionary_entry_id) if field.dictionary_entry_id else None,
        raw_numeric=str(field.raw_numeric) if field.raw_numeric is not None else None,
        medical_time=medical_time_view(field.medical_time_id, patient), source_reference=source)
    return {'fact_id': str(fact.id), 'patient_id': str(patient.id), 'version_id': str(version.id), 'version': str(version.version),
        'record_version_id': str(record_version.id), 'field': {'field': clinical,
            'verification_status': verification.status if verification is not None else 'unverified',
            'quality_reasons_snapshot_state': 'not_recorded' if field.value_state in ('unknown', 'invalid') and not field.quality_reasons else 'recorded',
            'verification_purpose': verification.purpose if verification is not None else None},
        'context': {'context_type': version.context_type, 'assertion_state': version.assertion_state,
            'episode_id': str(version.episode_id) if version.episode_id is not None else None,
            'medical_time': clinical['medical_time'], 'raw_time_text': version.raw_time_text, 'time_origin': version.time_origin,
            'documented_time': medical_time_view(version.documented_time_id, patient)},
        'document_version_id': str(version.document_version_id) if version.document_version_id is not None else None,
        'page_id': str(version.page_id) if version.page_id is not None else None,
        'source_anchor_id': str(version.source_anchor_id) if version.source_anchor_id is not None else None,
        'available_at': version.available_at.isoformat() if version.available_at is not None else None,
        'availability_status': version.availability_status, 'source': source, 'allowed_actions': list(allowed_actions)}


def fact_versions(*, fact, patient, scope, limit, expires_at, cursor=None):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise QueryRejected('LIMIT_INVALID')
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        raise QueryRejected('CURSOR_STALE')
    bound = {**scope, 'fact_id': str(fact.id), 'patient_id': str(patient.id), 'current_version': str(fact.current_version),
        'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch), 'limit': limit}
    offset, expiry = 0, expires_at.timestamp()
    if cursor is not None:
        saved = signing.loads(cursor, salt='archive-fact-history')
        if saved['scope'] != bound or saved['expires_at'] <= now.timestamp():
            raise QueryRejected('CURSOR_STALE')
        offset, expiry = saved['offset'], saved['expires_at']
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise QueryRejected('CURSOR_INVALID')
    versions = list(ArchiveFactVersion.objects.filter(fact_id=fact.id, patient_id=patient.id).order_by('-version', 'id')[offset:offset + limit + 1])
    more = len(versions) > limit
    selected = versions[:limit]
    return {'items': [fact_read(fact=fact, patient=patient, version=version) for version in selected],
        'snapshot_version': compute_checksum(bound),
        'next_cursor': signing.dumps({'scope': bound, 'offset': offset + limit, 'expires_at': expiry},
            salt='archive-fact-history') if more else None}
