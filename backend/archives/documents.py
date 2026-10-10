from datetime import datetime, timezone

from django.core import signing

from archives.models import ArchiveBatch, ArchiveDocumentVersion, ArchiveDocumentPage, ArchiveDocumentEpisode, ArchivePage, ArchiveFactVersion, ArchiveFact
from archives.queries import fact_source
from case_details.models import ClinicalEpisode
from configurations.service import compute_checksum
from files.models import FileGroup, FileObject, FilePage
from recognition.models import SourceAnchor
from records.models import RecordField
from records.models import RecordVersion
from records.queries import QueryRejected
from records.service import medical_time_view


def document_record_scope(document, patient, version):
    fact_versions = ArchiveFactVersion.objects.filter(document_version_id=version.id, patient_id=patient.id)
    episode_ids = ArchiveDocumentEpisode.objects.filter(document_version_id=version.id,
        patient_id=patient.id).values('episode_id')
    version_ids = set(fact_versions.values_list('record_version_id', flat=True))
    if version.version == document.current_version:
        document_version_ids = set(ArchiveDocumentVersion.objects.filter(document_id=document.id,
            patient_id=patient.id).values_list('id', flat=True))
        fact_ids = ArchiveFactVersion.objects.filter(document_version_id__in=document_version_ids,
            patient_id=patient.id).values('fact_id')
        for fact in ArchiveFact.objects.filter(id__in=fact_ids, patient_id=patient.id).order_by('id'):
            current = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id,
                version=fact.current_version)
            if current.document_version_id in document_version_ids:
                version_ids.add(current.record_version_id)
    version_ids.update(ClinicalEpisode.objects.filter(id__in=episode_ids,
        patient_id=patient.id).values_list('record_version_id', flat=True))
    record_ids = set(RecordVersion.objects.filter(id__in=version_ids,
        patient_id=patient.id).values_list('record_id', flat=True))
    return next(iter(record_ids)) if len(record_ids) == 1 else None


def document_sources(*, document, patient, version):
    if document.patient_id != patient.id or version.document_id != document.id or version.patient_id != patient.id:
        raise QueryRejected('DOCUMENT_VERSION_INVALID')
    batch = ArchiveBatch.objects.get(id=version.batch_id, patient_id=patient.id)
    links = list(ArchiveDocumentPage.objects.filter(document_version_id=version.id, patient_id=patient.id)
        .order_by('page_order', 'id'))
    if not links or [row.page_order for row in links] != list(range(1, len(links) + 1)):
        raise QueryRejected('DOCUMENT_PAGE_ORDER_INVALID')
    pages, files, groups = [], {}, {}
    for link in links:
        page = ArchivePage.objects.get(id=link.page_id, patient_id=patient.id, batch_id=batch.id)
        file_page = FilePage.objects.get(id=page.file_page_id, patient=patient, file_id=page.file_id)
        source = FileObject.objects.get(id=page.file_id, patient=patient)
        group = FileGroup.objects.get(id=file_page.file_group_id, patient=patient)
        if (source.file_group_id != group.id or source.purpose != 'original'
                or source.object_version != page.file_object_version or source.checksum != page.checksum):
            raise QueryRejected('DOCUMENT_SOURCE_INVALID')
        pages.append(page)
        files[source.id], groups[group.id] = source, group
    page_by_id = {page.id: page for page in pages}
    episodes = list(ArchiveDocumentEpisode.objects.filter(document_version_id=version.id, patient_id=patient.id)
        .order_by('episode_id'))
    for relation in episodes:
        episode = ClinicalEpisode.objects.get(id=relation.episode_id, patient_id=patient.id)
        field = RecordField.objects.get(id=episode.source_field_id, record_version_id=episode.record_version_id, patient=patient)
        anchor = SourceAnchor.objects.get(id=relation.evidence_anchor_id, patient=patient)
        if field.anchor_id != anchor.id or not any(page.file_page_id == anchor.page_id for page in pages):
            raise QueryRejected('DOCUMENT_EPISODE_SOURCE_INVALID')
    facts = list(ArchiveFactVersion.objects.filter(document_version_id=version.id, patient_id=patient.id).order_by('id'))
    for fact in facts:
        field, _ = fact_source(fact, patient)
        if fact.page_id not in page_by_id or fact.source_anchor_id is None:
            raise QueryRejected('DOCUMENT_FACT_SOURCE_INVALID')
        anchor = SourceAnchor.objects.get(id=fact.source_anchor_id, patient=patient)
        if anchor.page_id != page_by_id[fact.page_id].file_page_id or field.anchor_id != anchor.id:
            raise QueryRejected('DOCUMENT_FACT_SOURCE_INVALID')
    return batch, pages, list(files.values()), list(groups.values()), episodes, facts


def document_read(*, document, patient, version=None):
    if version is None:
        version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
            version=document.current_version)
    batch, pages, _, _, episodes, _ = document_sources(document=document, patient=patient, version=version)
    return {'document_id': str(document.id), 'patient_id': str(patient.id), 'version_id': str(version.id),
        'version': str(version.version), 'batch_id': str(batch.id), 'document_type': version.document_type,
        'organization': version.organization, 'documented_time': medical_time_view(version.documented_time_id, patient),
        'page_ids': [str(page.id) for page in pages], 'episode_ids': [str(row.episode_id) for row in episodes],
        'grouping_status': version.grouping_status, 'confirmed_text': version.confirmed_text,
        'proposed_metadata': version.proposed_metadata,
        'available_at': version.available_at.isoformat(), 'allowed_actions': []}


def document_versions(*, document, patient, scope, limit, expires_at, cursor=None):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise QueryRejected('LIMIT_INVALID')
    now = datetime.now(timezone.utc)
    if expires_at <= now:
        raise QueryRejected('CURSOR_STALE')
    bound = {**scope, 'document_id': str(document.id), 'patient_id': str(patient.id),
        'current_version': str(document.current_version), 'data_epoch': str(patient.data_epoch),
        'verification_epoch': str(patient.verification_epoch), 'limit': limit}
    offset, expiry = 0, expires_at.timestamp()
    if cursor is not None:
        saved = signing.loads(cursor, salt='archive-document-history')
        if saved['scope'] != bound or saved['expires_at'] <= now.timestamp():
            raise QueryRejected('CURSOR_STALE')
        offset, expiry = saved['offset'], saved['expires_at']
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise QueryRejected('CURSOR_INVALID')
    rows = list(ArchiveDocumentVersion.objects.filter(document_id=document.id, patient_id=patient.id)
        .order_by('-version', 'id')[offset:offset + limit + 1])
    selected = rows[:limit]
    return {'items': [document_read(document=document, patient=patient, version=row) for row in selected],
        'snapshot_version': compute_checksum(bound),
        'next_cursor': signing.dumps({'scope': bound, 'offset': offset + limit, 'expires_at': expiry},
            salt='archive-document-history') if len(rows) > limit else None}
