"""Atomic, version-preserving document boundary changes within one batch."""
from uuid import UUID, uuid4

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from api.base import ApiError, authorize_case
from archives.batch_creation import batch_write_identity
from archives.batches import batch_runtime
from archives.documents import document_sources
from archives.fact_candidates import validate_value
from archives.grouping import document_materials, document_time, document_write_admission
from archives.models import (ArchiveBatch, ArchiveDocument, ArchiveDocumentEpisode, ArchiveDocumentPage,
    ArchiveDocumentVersion, ArchiveFact, ArchiveFactVersion, ArchivePage, ArchiveRegrouping)
from archives.queries import fact_source
from assessment.provenance import entity_reference
from authorization.models import AccessGrant
from configurations.service import compute_checksum
from files.purpose import checked_bytes, lock_original, lock_use_authorization
from files.purpose_dependencies import connect, invalidate_resource, resource_node
from identity.models import Patient
from records.correction import correct_record
from records.models import Record, RecordField, RecordVersion
from records.service import medical_time_view
from relations.models import Relation
from recognition.models import SourceAnchor


OPERATION = 'archive_documents_regroup'


def regroup_input(payload):
    validate_value('RegroupInput', payload)
    sources = [item['document_id'] for item in payload['sources']]
    reused = [item['document_id'] for item in payload['outputs'] if item['document_id'] is not None]
    pages = [identity for item in payload['outputs'] for identity in item['page_ids']] + payload['unassigned_page_ids']
    if (len(set(sources)) != len(sources) or len(set(reused)) != len(reused) or not set(reused).issubset(sources)
            or len(set(pages)) != len(pages) or not payload['reason'].strip()
            or any(item['batch_id'] != payload['batch_id'] for item in payload['outputs'])
            or any(int(item['expected_version']) > 9223372036854775807 for item in payload['sources'])):
        raise ApiError('DOCUMENT_REGROUP_INPUT_INVALID', 422)
    return payload


def validate_partition(payload, source_page_ids):
    assigned = [identity for item in payload['outputs'] for identity in item['page_ids']]
    all_pages = assigned + payload['unassigned_page_ids']
    if len(set(all_pages)) != len(all_pages) or set(all_pages) != {str(value) for value in source_page_ids}:
        raise ApiError('DOCUMENT_REGROUP_PAGE_PARTITION_INVALID', 422)


def output_payload(payload, output, document=None):
    value = {key: value for key, value in output.items() if key not in ('document_id', 'metadata_confirmed')}
    if document is not None:
        value.update(expected_version=str(document.current_version), reason=payload['reason'])
    return value


def regroup_scope(request, actor, epoch, patient_id, payload, *, committed=None):
    from api.views_archive_documents import authorize_document

    regroup_input(payload)
    actor, patient, gate = batch_write_identity(actor, epoch, patient_id)
    runtime = batch_runtime()
    batch = ArchiveBatch.objects.select_for_update().get(id=payload['batch_id'], patient_id=patient.id)
    identifiers = [item['document_id'] for item in payload['sources']]
    documents = {str(item.id): item for item in ArchiveDocument.objects.select_for_update().filter(
        id__in=identifiers, patient_id=patient.id).order_by('id')}
    if set(documents) != set(identifiers):
        raise ApiError('DOCUMENT_REGROUP_SOURCE_INVALID', 404)
    source_refs, pages, proofs = [], {}, []
    for source in sorted(payload['sources'], key=lambda value: value['document_id']):
        document = documents[source['document_id']]
        version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
            version=int(source['expected_version']) if committed is not None else document.current_version)
        for action in (('read_history',) if committed is not None else ('read_raw', 'write_record')):
            _, scopes = authorize_document(request, actor, patient, document, version, action)
            proofs.extend(scope['grant_id'] for scope in scopes)
        if committed is None and (document.retired_at is not None or document.current_version != int(source['expected_version'])):
            raise ApiError('VERSION_CONFLICT', 409)
        if version.batch_id != batch.id:
            raise ApiError('DOCUMENT_REGROUP_BATCH_MISMATCH', 422)
        _, _, files, groups, _, _ = document_sources(document=document, patient=patient, version=version)
        _, source_pages, _, _, _, _ = document_sources(document=document, patient=patient, version=version)
        for page in source_pages:
            if page.id in pages:
                raise ApiError('DOCUMENT_REGROUP_SOURCE_PAGE_DUPLICATED', 409)
            pages[page.id] = page
        for file in files:
            group = next(value for value in groups if value.id == file.file_group_id)
            lock_use_authorization(group.use_authorization_id, file, 'family_observation', gate.environment)
            checked_bytes(lock_original(file.id))
        source_refs.append({'document_id': str(document.id), 'version_id': str(version.id),
            'version': str(version.version), 'page_ids': [str(page.id) for page in source_pages]})
    validate_partition(payload, pages)
    if len(pages) > runtime.payload['limits']['max_pages']:
        raise ApiError('DOCUMENT_REGROUP_SCOPE_LIMIT_EXCEEDED', 422)
    locked_pages = list(ArchivePage.objects.select_for_update().filter(
        id__in=pages, patient_id=patient.id, batch_id=batch.id).order_by('id'))
    # Receipt recovery checks the immutable original sources; it must not try to
    # recreate a now-obsolete current partition or require write permission.
    if committed is not None:
        if committed.source_refs != source_refs or committed.patient_id != patient.id or committed.batch_id != batch.id:
            raise ApiError('DOCUMENT_REGROUP_RECEIPT_INVALID', 409)
        return {'patient': patient, 'batch': batch, 'documents': documents, 'source_refs': source_refs,
            'runtime': runtime, 'context': None}
    if len(locked_pages) != len(pages):
        raise ApiError('DOCUMENT_REGROUP_SOURCE_PAGE_INVALID', 409)
    # An empty output partition or an unassigned page still changes document
    # membership. It must not bypass retry/uncertainty admission by omitting the
    # page from every output. Recheck the locked rows, not the earlier reads.
    document_write_admission(patient, batch, locked_pages)
    pages = {page.id: page for page in locked_pages}
    versions = set(ArchiveDocumentVersion.objects.filter(document_id__in=identifiers, patient_id=patient.id).values_list('id', flat=True))
    matching = ArchiveFactVersion.objects.filter(patient_id=patient.id).filter(Q(page_id__in=pages) | Q(document_version_id__in=versions))
    facts = []
    for identity in ArchiveFact.objects.select_for_update().filter(id__in=matching.values('fact_id'), patient_id=patient.id).order_by('id'):
        fact = ArchiveFactVersion.objects.get(fact_id=identity.id, patient_id=patient.id, version=identity.current_version)
        if fact.page_id not in pages and fact.document_version_id not in versions:
            continue
        if fact.page_id not in pages:
            raise ApiError('DOCUMENT_REGROUP_FACT_PAGE_INVALID', 409)
        if fact.document_version_id is not None and not ArchiveDocumentVersion.objects.filter(
                id=fact.document_version_id, patient_id=patient.id, document_id__in=identifiers).exists():
            raise ApiError('DOCUMENT_FACT_ALREADY_GROUPED', 409)
        fact_source(fact, patient)
        if fact.source_anchor_id is None or not SourceAnchor.objects.filter(id=fact.source_anchor_id,
                patient_id=patient.id, page_id=pages[fact.page_id].file_page_id).exists():
            raise ApiError('DOCUMENT_FACT_SOURCE_INVALID', 409)
        facts.append(fact)
    record_ids = RecordVersion.objects.filter(id__in={fact.record_version_id for fact in facts}, patient=patient).values('record_id')
    checks = []
    records = list(Record.objects.select_for_update().filter(id__in=record_ids, patient=patient).order_by('id'))
    for record in records:
        previous = RecordVersion.objects.get(record_id=record.id, patient=patient, version=record.current_version)
        if record.current_version >= 9223372036854775807:
            raise ApiError('RECORD_VERSION_LIMIT', 409)
        if record.status != 'active' or any(fact.record_version_id != previous.id for fact in facts
                if RecordVersion.objects.get(id=fact.record_version_id).record_id == record.id):
            raise ApiError('DOCUMENT_REGROUP_RECORD_CHANGED', 409)
        initial = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=record.id, patient=patient, version=1)
        for field in RecordField.objects.filter(record_version_id=previous.id, patient=patient).order_by('ordinal'):
            for action in ('read_raw', 'correct_record'):
                check = dict(field_codes=[field.field_code], record_id=record.id, author_id=initial.author_id, require_relation=True)
                checks.append((action, check))
                decision = authorize_case(request, actor, patient, action, **check)
                proofs.append(str(decision.grant.id))
    materials = []
    for output in payload['outputs']:
        document = documents.get(output['document_id'])
        if document is not None and document.current_version >= 9223372036854775807:
            raise ApiError('DOCUMENT_VERSION_LIMIT', 409)
        prepared = output_payload(payload, output, document)
        material = document_materials(patient, prepared, document, check_facts=False, ignore_document_ids=identifiers)
        for fact in facts:
            if str(fact.page_id) in output['page_ids'] and fact.episode_id is not None and str(fact.episode_id) not in output['episode_ids']:
                raise ApiError('DOCUMENT_FACT_EPISODE_REQUIRED', 422)
        for episode, field, _ in material[4]:
            version = RecordVersion.objects.get(id=episode.record_version_id, patient=patient)
            initial = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient=patient, version=1)
            check = dict(field_codes=[field.field_code], record_id=version.record_id, author_id=initial.author_id, require_relation=True)
            checks.append(('read_raw', check))
            proofs.append(str(authorize_case(request, actor, patient, 'read_raw', **check).grant.id))
        materials.append((document, prepared, material))
    grants = list(AccessGrant.objects.select_for_update().filter(id__in=set(proofs)).order_by('id'))
    relations = list(Relation.objects.select_for_update().filter(id__in=[row.relation_id for row in grants if row.relation_id]).order_by('id'))
    for source in source_refs:
        document = documents[source['document_id']]
        version = ArchiveDocumentVersion.objects.get(id=source['version_id'], patient_id=patient.id)
        for action in ('read_raw', 'write_record'):
            authorize_document(request, actor, patient, document, version, action)
    for action, check in checks:
        authorize_case(request, actor, patient, action, **check)
    first_file = lock_original(next(iter(pages.values())).file_id)
    context = {'patient_id': str(patient.id), 'auth_epoch': str(epoch), 'gate': entity_reference(gate),
        'runtime': entity_reference(runtime), 'batch': entity_reference(batch), 'sources': source_refs,
        'documents': [entity_reference(value) for value in documents.values()],
        'pages': [entity_reference(value) for value in sorted(pages.values(), key=lambda value: value.id)],
        'facts': [entity_reference(value) for value in facts], 'records': [entity_reference(value) for value in records],
        'grants': [entity_reference(value) for value in grants], 'relations': [entity_reference(value) for value in relations]}
    return {'patient': patient, 'batch': batch, 'documents': documents, 'source_refs': source_refs,
        'runtime': runtime, 'context': context, 'materials': materials, 'facts': facts, 'records': records,
        'retention': first_file.retention_policy}


def create_output(patient, actor, document, payload, materials, now):
    batch, pages, files, _, episodes, _, previous = materials
    if document is None:
        document = ArchiveDocument.objects.create(patient_id=patient.id, current_version=1, created_at=now)
    version_number = 1 if previous is None else previous.version + 1
    medical = document_time(patient, payload['documented_time'], previous)
    proposed = payload['proposed_metadata']
    version = ArchiveDocumentVersion.objects.create(patient_id=patient.id, document_id=document.id,
        version=version_number, batch_id=batch.id, document_type=payload['document_type'], organization=payload['organization'],
        proposed_metadata=proposed, confirmed_text=payload['confirmed_text'],
        grouping_status='pending' if payload['document_type'] == 'unknown' or not episodes or proposed and proposed['episodes'] else 'confirmed',
        documented_time_id=medical.id if medical else None, available_at=now, confirmed_by=actor.id, confirmed_at=now,
        supersedes_version=previous.version if previous else None, reason=payload.get('reason'))
    for order, page in enumerate(pages, 1):
        ArchiveDocumentPage.objects.create(patient_id=patient.id, document_version_id=version.id, page_id=page.id, page_order=order)
    for episode, _, anchor in episodes:
        ArchiveDocumentEpisode.objects.create(patient_id=patient.id, document_version_id=version.id,
            episode_id=episode.id, evidence_anchor_id=anchor.id, confirmed_by=actor.id, confirmed_at=now)
    node = resource_node('archive_document', document.id, str(version_number), patient.id)
    for file in files:
        connect(node, resource_node('file_object', file.id, file.object_version, patient.id), 'source')
        connect(node, resource_node('retention_policy', file.retention_policy_id, str(file.retention_policy.version), None), 'policy')
    for page in pages:
        connect(node, resource_node('archive_page', page.id, str(page.version), patient.id), 'source')
    for episode, _, _ in episodes:
        source = RecordVersion.objects.get(id=episode.record_version_id, patient=patient)
        connect(node, resource_node('record_version', source.id, str(source.version), patient.id), 'source')
    document.current_version = version_number
    document.save(update_fields=['current_version'])
    return document, version


def rebind_facts(patient, actor, records, selected, page_versions, reason):
    selected_ids = {fact.fact_id for fact in selected}
    for record in records:
        previous = RecordVersion.objects.get(record_id=record.id, patient=patient, version=record.current_version)
        fields = []
        for field in RecordField.objects.filter(record_version_id=previous.id, patient=patient).order_by('ordinal'):
            value = {name: getattr(field, name) for name in ('field_code', 'concept_code', 'original_name',
                'value_type', 'value_state', 'raw_text', 'categorical_value', 'qualifier', 'raw_unit',
                'method', 'reference_interval', 'report_flag', 'sample_time_origin')}
            value.update(dictionary_entry_id=field.dictionary_entry_id,
                raw_numeric=str(field.raw_numeric) if field.raw_numeric is not None else None,
                medical_time=medical_time_view(field.medical_time_id, patient))
            fields.append(value)
        result = correct_record(record=record, expected_version=previous.version, actor=actor, reason=reason,
            fields=fields, use_authorization_id=previous.use_authorization_id, retention_policy=previous.retention_policy,
            capture_method=previous.capture_method, source_revision=previous.source_revision)
        for fact in ArchiveFactVersion.objects.filter(record_version_id=result['record_version_id'], patient_id=patient.id):
            if fact.fact_id in selected_ids:
                target = page_versions.get(fact.page_id)
                fact.document_version_id = target.id if target else None
                fact.save(update_fields=['document_version_id'])
            node = resource_node('archive_fact_version', fact.id, str(fact.version), patient.id)
            connect(node, resource_node('record_version', fact.record_version_id, str(result['version']), patient.id), 'source')
            if fact.document_version_id is not None:
                source = ArchiveDocumentVersion.objects.get(id=fact.document_version_id, patient_id=patient.id)
                connect(node, resource_node('archive_document', source.document_id, str(source.version), patient.id), 'source')
        for previous_fact in ArchiveFactVersion.objects.filter(record_version_id=previous.id, patient_id=patient.id):
            invalidate_resource('archive_fact_version', previous_fact.id)


@transaction.atomic
def save_regrouping(*, actor, payload, scope, request_id, input_sha256):
    patient, now = scope['patient'], timezone.now()
    identity = uuid4()
    outputs, page_versions, reused = [], {}, set()
    for document, prepared, materials in scope['materials']:
        document, version = create_output(patient, actor, document, prepared, materials, now)
        reused.add(document.id)
        for page in materials[1]:
            page_versions[page.id] = version
        outputs.append({'document_id': str(document.id), 'version_id': str(version.id), 'version': str(version.version),
            'page_ids': [str(page.id) for page in materials[1]]})
    rebind_facts(patient, actor, scope['records'], scope['facts'], page_versions, payload['reason'])
    retired = sorted(str(value.id) for value in scope['documents'].values() if value.id not in reused)
    result = validate_value('RegroupResult', {'regrouping_id': str(identity), 'patient_id': str(patient.id),
        'batch_id': str(scope['batch'].id), 'sources': scope['source_refs'], 'outputs': outputs, 'retired_document_ids': retired,
        'unassigned_page_ids': payload['unassigned_page_ids']})
    row = ArchiveRegrouping.objects.create(id=identity, patient_id=patient.id, batch_id=scope['batch'].id,
        actor_id=actor.id, request_id=request_id, input_sha256=input_sha256, source_refs=scope['source_refs'],
        result_refs=result, result_sha256=compute_checksum(result), created_at=now)
    for reference in scope['source_refs']:
        invalidate_resource('archive_document', UUID(reference['document_id']), reference['version'])
        invalidate_resource('archive_document_version', UUID(reference['version_id']))
    for identifier in retired:
        ArchiveDocument.objects.filter(id=identifier, patient_id=patient.id).update(retired_at=now,
            retired_by=actor.id, retirement_id=row.id, retirement_reason=payload['reason'])
    Patient.objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    return row
