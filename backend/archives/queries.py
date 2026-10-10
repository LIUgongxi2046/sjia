from datetime import datetime, timezone

from django.core import signing
from archives.models import ArchiveFactVersion, ArchiveDocumentVersion, ArchiveDocumentPage, ArchivePage
from files.models import FilePage, FileObject
from recognition.models import SourceAnchor
from records.models import RecordField, RecordVersion
from records.service import medical_time_view
from records.queries import QueryRejected
from verification.models import FieldVerificationState, Verification, VerificationField
from configurations.service import compute_checksum


def fact_source(version, patient):
    field = RecordField.objects.get(id=version.field_id, record_version_id=version.record_version_id, patient=patient)
    record_version = RecordVersion.objects.get(id=version.record_version_id, patient=patient)
    if (version.patient_id != patient.id or version.medical_time_id != field.medical_time_id
        or version.source_anchor_id != field.anchor_id or version.confirmation_id != field.confirmation_id):
        raise QueryRejected('FACT_SOURCE_INVALID')
    return field, record_version


def fact_read(*, fact, patient, version=None, allowed_actions=()):
    if fact.patient_id != patient.id:
        raise QueryRejected('FACT_PATIENT_INVALID')
    if version is None:
        version = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
    if version.fact_id != fact.id or version.patient_id != patient.id:
        raise QueryRejected('FACT_VERSION_INVALID')
    field, record_version = fact_source(version, patient)
    anchor = SourceAnchor.objects.get(id=field.anchor_id, patient=patient) if field.anchor_id is not None else None
    file_page = FilePage.objects.get(id=anchor.page_id, patient_id=patient.id) if anchor is not None else None
    if file_page is not None:
        source_file = FileObject.objects.get(id=file_page.file_id, patient=patient)
    if version.page_id is not None:
        page = ArchivePage.objects.get(id=version.page_id, patient_id=patient.id)
        if file_page is None or page.file_page_id != file_page.id or page.file_id != file_page.file_id:
            raise QueryRejected('FACT_SOURCE_INVALID')
        if page.file_object_version != source_file.object_version or page.checksum != source_file.checksum:
            raise QueryRejected('FACT_SOURCE_INVALID')
    if version.document_version_id is not None:
        ArchiveDocumentVersion.objects.get(id=version.document_version_id, patient_id=patient.id)
        if version.page_id is None or not ArchiveDocumentPage.objects.filter(patient_id=patient.id,
            document_version_id=version.document_version_id, page_id=version.page_id).exists():
            raise QueryRejected('FACT_SOURCE_INVALID')
    verification = FieldVerificationState.objects.filter(patient_id=patient.id, field_id=field.id,
        record_version_id=record_version.id, purpose='family_observation').first()
    verification_ids = []
    if verification is not None and verification.status == 'verified' and verification.current_verification_id is None:
        raise QueryRejected('FACT_VERIFICATION_INVALID')
    if verification is not None and verification.current_verification_id is not None:
        proof = Verification.objects.get(id=verification.current_verification_id, patient=patient,
            record_version_id=record_version.id, purpose=verification.purpose)
        proof_field = VerificationField.objects.get(verification_id=proof.id, patient_id=patient.id,
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
