from uuid import UUID

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from archives.inputs import document_input
from archives.models import Archive, ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion, ArchiveDocumentPage, ArchiveDocumentEpisode, ArchivePage, ArchiveFactVersion, ArchiveFact
from archives.queries import fact_source
from case_details.models import ClinicalEpisode
from files.models import FileGroup, FilePage
from files.purpose import lock_original, checked_bytes
from files.purpose_dependencies import resource_node, connect, invalidate_resource
from identity.models import Patient
from medical_time.models import MedicalTime
from recognition.models import SourceAnchor
from records.models import RecordField, RecordVersion
from records.models import Record
from records.correction import correct_record
from records.queries import QueryRejected
from records.service import _create_medical_time, medical_time_view


def document_time(patient, payload, previous):
    if previous is not None and previous.documented_time_id is not None and payload is not None:
        saved = medical_time_view(previous.documented_time_id, patient)
        content_keys = ('raw_text', 'precision', 'local_year', 'local_month', 'end_year', 'end_month',
            'relative_text', 'local_date', 'local_datetime', 'timezone_name', 'utc_offset_minutes')
        same_content = all(saved.get(key) == payload.get(key) for key in content_keys)
        same_status = saved['resolution_status'] == payload['resolution_status']
        resolved_content = saved['resolution_status'] == 'resolved' and payload['resolution_status'] == 'local_only'
        if same_content and (same_status or resolved_content):
            return MedicalTime.objects.get(id=previous.documented_time_id, patient=patient)
    return _create_medical_time(patient, payload)


def document_materials(patient, payload, document=None, *, check_version=True, ignore_document_id=None, check_facts=True):
    document_input('GroupingInput' if document is not None else 'DocumentInput', payload)
    batch = ArchiveBatch.objects.select_for_update().get(id=payload['batch_id'], patient_id=patient.id)
    Archive.objects.get(id=batch.archive_id, patient_id=patient.id)
    if batch.status in ('receiving', 'cancelled', 'failed'):
        raise QueryRejected('DOCUMENT_BATCH_UNAVAILABLE')
    previous = None
    if document is not None:
        if document.patient_id != patient.id:
            raise QueryRejected('DOCUMENT_PATIENT_INVALID')
        if check_version and document.current_version != int(payload['expected_version']):
            raise QueryRejected('VERSION_CONFLICT')
        previous = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
            version=document.current_version)
        if previous.batch_id != batch.id:
            raise QueryRejected('DOCUMENT_BATCH_CHANGED')
    selected_ids = [UUID(value) for value in payload['page_ids']]
    pages_by_id = {page.id: page for page in ArchivePage.objects.select_for_update().filter(
        id__in=selected_ids, patient_id=patient.id, batch_id=batch.id).order_by('id')}
    if len(pages_by_id) != len(selected_ids):
        raise QueryRejected('DOCUMENT_PAGE_INVALID')
    pages = [pages_by_id[page_id] for page_id in selected_ids]
    files, groups = {}, {}
    for page in pages:
        if page.status not in ('uploaded', 'awaiting_confirmation', 'confirmed', 'archived'):
            raise QueryRejected('DOCUMENT_PAGE_UNAVAILABLE')
        source = files.get(page.file_id)
        if source is None:
            source = lock_original(page.file_id)
            checked_bytes(source)
            files[source.id] = source
        file_page = FilePage.objects.select_for_update().get(id=page.file_page_id, patient_id=patient.id)
        group = FileGroup.objects.select_for_update().get(id=file_page.file_group_id, patient_id=patient.id)
        if (source.patient_id != patient.id or file_page.file_id != source.id
                or source.file_group_id != group.id or group.status != 'available'
                or page.file_object_version != source.object_version or page.checksum != source.checksum):
            raise QueryRejected('DOCUMENT_SOURCE_INVALID')
        groups[group.id] = group
    current_documents = ArchiveDocument.objects.filter(patient_id=patient.id)
    if document is not None:
        current_documents = current_documents.exclude(id=document.id)
    if ignore_document_id is not None:
        current_documents = current_documents.exclude(id=ignore_document_id)
    # 当前文书引用逐项核验，不把重复页面自动认定为复印关系。
    for other in current_documents.order_by('id'):
        other_version = ArchiveDocumentVersion.objects.get(document_id=other.id, patient_id=patient.id,
            version=other.current_version)
        if ArchiveDocumentPage.objects.filter(document_version_id=other_version.id,
                patient_id=patient.id, page_id__in=selected_ids).exists():
            raise QueryRejected('DOCUMENT_COPY_CONFIRMATION_REQUIRED')
    episodes = []
    file_page_ids = {page.file_page_id for page in pages}
    for episode_id in sorted(payload['episode_ids']):
        episode = ClinicalEpisode.objects.select_for_update().get(id=episode_id, patient_id=patient.id)
        field = RecordField.objects.get(id=episode.source_field_id, record_version_id=episode.record_version_id,
            patient_id=patient.id)
        if field.anchor_id is None:
            raise QueryRejected('DOCUMENT_EPISODE_SOURCE_INVALID')
        anchor = SourceAnchor.objects.get(id=field.anchor_id, patient_id=patient.id)
        if anchor.page_id not in file_page_ids:
            raise QueryRejected('DOCUMENT_EPISODE_SOURCE_INVALID')
        episodes.append((episode, field, anchor))
    facts = []
    if check_facts:
        document_version_ids = set(ArchiveDocumentVersion.objects.filter(document_id=document.id,
            patient_id=patient.id).values_list('id', flat=True)) if document is not None else set()
        matching = ArchiveFactVersion.objects.filter(patient_id=patient.id, page_id__in=selected_ids)
        if document_version_ids:
            matching = ArchiveFactVersion.objects.filter(patient_id=patient.id).filter(
                Q(page_id__in=selected_ids) | Q(document_version_id__in=document_version_ids))
        for fact in ArchiveFact.objects.select_for_update().filter(patient_id=patient.id,
                id__in=matching.values('fact_id')).order_by('id'):
            current = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
            if current.page_id not in pages_by_id and current.document_version_id not in document_version_ids:
                continue
            if current.document_version_id is not None and current.document_version_id not in document_version_ids:
                raise QueryRejected('DOCUMENT_FACT_ALREADY_GROUPED')
            fact_source(current, patient)
            if current.page_id not in pages_by_id:
                raise QueryRejected('DOCUMENT_FACT_PAGE_REQUIRED')
            if current.source_anchor_id is None:
                raise QueryRejected('DOCUMENT_FACT_SOURCE_INVALID')
            anchor = SourceAnchor.objects.get(id=current.source_anchor_id, patient_id=patient.id)
            if anchor.page_id != pages_by_id[current.page_id].file_page_id:
                raise QueryRejected('DOCUMENT_FACT_SOURCE_INVALID')
            if current.episode_id is not None and str(current.episode_id) not in payload['episode_ids']:
                raise QueryRejected('DOCUMENT_FACT_EPISODE_REQUIRED')
            facts.append(current)
    return batch, pages, list(files.values()), list(groups.values()), episodes, facts, previous


def bind_document_facts(patient, actor, saved, facts, reason):
    version_ids = sorted({fact.record_version_id for fact in facts})
    selected = {fact.fact_id for fact in facts}
    for version_id in version_ids:
        previous = RecordVersion.objects.get(id=version_id, patient=patient)
        record = Record.objects.select_for_update().get(id=previous.record_id, patient=patient,
            current_version=previous.version, status='active')
        fields = []
        for field in RecordField.objects.filter(record_version_id=previous.id, patient=patient).order_by('ordinal'):
            value = {name: getattr(field, name) for name in ('field_code', 'concept_code', 'original_name',
                'value_type', 'value_state', 'raw_text', 'categorical_value', 'qualifier', 'raw_unit',
                'method', 'reference_interval', 'report_flag', 'sample_time_origin')}
            value.update(dictionary_entry_id=field.dictionary_entry_id,
                raw_numeric=str(field.raw_numeric) if field.raw_numeric is not None else None,
                medical_time=medical_time_view(field.medical_time_id, patient))
            fields.append(value)
        result = correct_record(record=record, expected_version=previous.version, actor=actor,
            reason=reason, fields=fields, use_authorization_id=previous.use_authorization_id,
            retention_policy=previous.retention_policy, capture_method=previous.capture_method,
            source_revision=previous.source_revision)
        updated = RecordVersion.objects.get(id=result['record_version_id'], patient=patient)
        for fact in ArchiveFactVersion.objects.filter(record_version_id=updated.id, patient_id=patient.id):
            if fact.fact_id in selected:
                fact.document_version_id = saved.id
                fact.save(update_fields=['document_version_id'])
            node = resource_node('archive_fact_version', fact.id, str(fact.version), patient.id)
            connect(node, resource_node('record_version', updated.id, str(updated.version), patient.id), 'source')
            if fact.document_version_id is not None:
                document_version = ArchiveDocumentVersion.objects.get(id=fact.document_version_id, patient_id=patient.id)
                connect(node, resource_node('archive_document', document_version.document_id,
                    str(document_version.version), patient.id), 'source')
        for old in ArchiveFactVersion.objects.filter(record_version_id=previous.id, patient_id=patient.id):
            invalidate_resource('archive_fact_version', old.id, str(old.version))


@transaction.atomic
def save_document(*, patient, actor, payload, document=None):
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if document is not None:
        document = ArchiveDocument.objects.select_for_update().get(id=document.id, patient_id=patient.id)
    batch, pages, files, _, episodes, facts, previous = document_materials(patient, payload, document)
    now = timezone.now()
    if document is None:
        document = ArchiveDocument.objects.create(patient_id=patient.id, current_version=1, created_at=now)
    next_version = 1 if previous is None else previous.version + 1
    proposed = payload.get('proposed_metadata', previous.proposed_metadata if previous is not None else None)
    text = payload.get('confirmed_text', previous.confirmed_text if previous is not None else None)
    unresolved = payload['document_type'] == 'unknown' or not episodes or (proposed is not None and bool(proposed['episodes']))
    documented_time = document_time(patient, payload['documented_time'], previous)
    saved = ArchiveDocumentVersion.objects.create(patient_id=patient.id, document_id=document.id,
        version=next_version, batch_id=batch.id, document_type=payload['document_type'],
        organization=payload['organization'], proposed_metadata=proposed, confirmed_text=text,
        grouping_status='pending' if unresolved else 'confirmed',
        documented_time_id=documented_time.id if documented_time is not None else None, available_at=now,
        confirmed_by=actor.id, confirmed_at=now,
        supersedes_version=previous.version if previous is not None else None,
        reason=payload['reason'] if previous is not None else None)
    for order, page in enumerate(pages, start=1):
        ArchiveDocumentPage.objects.create(patient_id=patient.id, document_version_id=saved.id,
            page_id=page.id, page_order=order)
    for episode, _, anchor in episodes:
        ArchiveDocumentEpisode.objects.create(patient_id=patient.id, document_version_id=saved.id,
            episode_id=episode.id, evidence_anchor_id=anchor.id, confirmed_by=actor.id, confirmed_at=now)
    node = resource_node('archive_document', document.id, str(next_version), patient.id)
    for source in files:
        connect(node, resource_node('file_object', source.id, source.object_version, patient.id), 'source')
        connect(node, resource_node('retention_policy', source.retention_policy_id,
            str(source.retention_policy.version), None), 'policy')
    for page in pages:
        connect(node, resource_node('archive_page', page.id, str(page.version), patient.id), 'source')
    for episode, field, _ in episodes:
        source_version = RecordVersion.objects.get(id=episode.record_version_id, patient_id=patient.id)
        connect(node, resource_node('record_version', episode.record_version_id,
            str(source_version.version), patient.id), 'source')
    bind_document_facts(patient, actor, saved, facts,
        payload['reason'] if previous is not None else '本人确认文书及逐项事实归属')
    if previous is not None:
        invalidate_resource('archive_document', document.id, str(previous.version))
    document.current_version = next_version
    document.save(update_fields=['current_version'])
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    return document, saved
