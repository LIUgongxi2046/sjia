import json
from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from api.base import ApiError
from archives.batch_creation import batch_write_identity, locked_grants
from archives.batches import batch_runtime, page_record_scope, page_sources
from archives.fact_candidates import FIELD_NAMES, candidate_items, page_candidate_set, validate_value
from archives.inputs import check_medical_time
from archives.models import ArchiveBatch, ArchiveFact, ArchiveFactVersion, ArchivePage
from archives.page_execution import update_batch
from archives.queries import fact_source
from authorization.models import AccessGrant
from authorization.policy import matching_case_grants
from case_details.models import ClinicalEpisode
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from dictionaries.models import DictionaryEntry
from files.purpose import checked_bytes, lock_use_authorization
from files.purpose_dependencies import connect, invalidate_resource, resource_node
from recognition.models import CandidateConfirmation
from records.correction import correct_record
from records.medical_times import create_medical_time
from records.models import Record, RecordField, RecordVersion
from records.service import create_record, medical_time_view


OPERATIONS = {'archive_candidates_confirm': 'FactConfirmationInput', 'archive_fact_correct': 'FactCorrectionInput'}


def fact_input(operation, payload):
    validate_value(OPERATIONS[operation], payload)
    fields = payload['items'] if operation == 'archive_candidates_confirm' else [payload]
    for item in fields:
        context = item['context']
        if context is not None:
            for name in ('medical_time', 'documented_time'):
                check_medical_time(context[name])
        if item['field'] is not None:
            check_medical_time(item['field']['medical_time'])
    return payload


def field_grants(actor, patient, action, codes, author, record_id=None):
    proofs = []
    for code in sorted(set(codes)):
        grants = [grant for grant in matching_case_grants(actor, patient, action, timezone.now(),
            author_id=author, record_id=record_id, field_codes=[code], require_relation=True)
            if grant.purpose == 'family_observation' and grant.evidence_ref.strip()]
        if not grants:
            raise ApiError('CASE_ACTION_DENIED', 403)
        for grant in grants:
            AccessGrant.objects.select_for_update().get(id=grant.id)
            proofs.append({'grant_id': str(grant.id), 'action': action, 'field_code': code,
                'relation_id': str(grant.relation_id)})
    return proofs


def field_definition(config, field):
    if configuration_qualification_reason(config, settings.SJIA_ENVIRONMENT) is not None:
        raise ApiError('ARCHIVE_FIELD_CONFIGURATION_PENDING', 503)
    definitions = [item for item in config.payload['fields'] if item['field_code'] == field['field_code']]
    if len(definitions) != 1:
        raise ApiError('ARCHIVE_FIELD_CONFIGURATION_INVALID', 422)
    definition = definitions[0]
    if not definition['writable'] or definition['value_type'] != field['value_type']:
        raise ApiError('ARCHIVE_FIELD_TYPE_INVALID', 422)
    if field['concept_code'] is not None or field['dictionary_entry_id'] is not None:
        if field['concept_code'] is None or field['dictionary_entry_id'] is None:
            raise ApiError('ARCHIVE_FIELD_MAPPING_REQUIRES_APPROVED_DICTIONARY', 422)
        entry = DictionaryEntry.objects.select_related('dictionary_version').get(id=field['dictionary_entry_id'])
        if (entry.concept_code != field['concept_code'] or entry.concept_code != field['field_code']
                or configuration_qualification_reason(entry.dictionary_version, settings.SJIA_ENVIRONMENT) is not None):
            raise ApiError('ARCHIVE_FIELD_MAPPING_REQUIRES_APPROVED_DICTIONARY', 422)
    if definition['max_length'] is not None and any(value is not None and len(value) > definition['max_length']
            for value in [field['raw_text'], field['original_name'], field['method'], field['reference_interval']]):
        raise ApiError('ARCHIVE_FIELD_LENGTH_EXCEEDED', 422)
    if definition['time_required'] and field['medical_time'] is None:
        raise ApiError('ARCHIVE_FIELD_TIME_REQUIRED', 422)
    # 原始单位保留；没有映射依据的单位不产生规范化临床数值。
    if field['value_state'] == 'known' and field['raw_numeric'] is not None:
        value = Decimal(field['raw_numeric'])
        if (not value.is_finite() or definition['minimum'] is not None and value < Decimal(definition['minimum'])
                or definition['maximum'] is not None and value > Decimal(definition['maximum'])):
            raise ApiError('ARCHIVE_FIELD_VALUE_INVALID', 422)
    if field['value_state'] == 'known' and definition['allowed_values'] and field['categorical_value'] not in definition['allowed_values']:
        raise ApiError('ARCHIVE_FIELD_VALUE_INVALID', 422)
    return definition


def context_check(actor, patient, field, context):
    if context is None or field['medical_time'] != context['medical_time']:
        raise ApiError('ARCHIVE_FACT_TIME_MISMATCH', 422)
    origin = context['time_origin']
    if origin == 'legacy' or origin == 'explicit_common' and not context['raw_time_text']:
        raise ApiError('ARCHIVE_FACT_TIME_ORIGIN_INVALID', 422)
    if origin == 'unknown' and context['medical_time'] is not None:
        raise ApiError('ARCHIVE_FACT_TIME_ORIGIN_INVALID', 422)
    expected = 'item' if origin == 'explicit_common' else origin
    if field['sample_time_origin'] != expected:
        raise ApiError('ARCHIVE_FACT_TIME_ORIGIN_INVALID', 422)
    if context['episode_id'] is not None:
        episode = ClinicalEpisode.objects.get(id=context['episode_id'], patient_id=patient.id)
        source = RecordField.objects.get(id=episode.source_field_id, patient=patient, record_version_id=episode.record_version_id)
        version = RecordVersion.objects.get(id=episode.record_version_id, patient=patient)
        record = Record.objects.get(id=version.record_id, patient=patient, status='active')
        initial = RecordVersion.objects.get(record_id=record.id, patient=patient, version=1)
        return [{**proof, 'episode_id': str(episode.id), 'episode_record_version_id': str(version.id),
            'episode_source_field_id': str(source.id)} for proof in
            field_grants(actor, patient, 'read_raw', [source.field_code], initial.author_id, record.id)]
    return []


def page_scope(actor, epoch, page_id, *, write=False):
    seed = ArchivePage.objects.get(id=page_id)
    actor, patient, gate = batch_write_identity(actor, epoch, seed.patient_id)
    batch = ArchiveBatch.objects.select_for_update().get(id=seed.batch_id, patient_id=patient.id)
    page = ArchivePage.objects.select_for_update().get(id=seed.id, patient_id=patient.id)
    _, source, group, _ = page_sources(page, patient)
    record_id = page_record_scope(page, patient)
    proofs = locked_grants(actor, patient, 'read_raw', author_id=batch.submitted_by, record_id=record_id)
    proofs += locked_grants(actor, patient, 'read_file', author_id=group.owner_id, record_id=record_id)
    if write:
        proofs += locked_grants(actor, patient, 'write_record', author_id=batch.submitted_by, record_id=record_id)
    if source.status != 'available' or group.status != 'available':
        raise ApiError('ARCHIVE_FACT_SOURCE_UNAVAILABLE', 409)
    runtime = batch_runtime()
    return actor, patient, gate, batch, page, source, group, runtime, proofs


def fact_scope(actor, epoch, operation, identifier, payload, *, mutating):
    fact_input(operation, payload)
    if operation == 'archive_candidates_confirm':
        actor, patient, gate, batch, page, source, group, runtime, proofs = page_scope(actor, epoch, identifier, write=True)
        candidate, rows, task = page_candidate_set(page, patient)
        if str(candidate.id) != payload['candidate_set_id'] or str(page.version) != payload['expected_page_version']:
            raise ApiError('ARCHIVE_CANDIDATE_VERSION_CHANGED', 409)
        proposed = {item['candidate_id']: item for item in candidate_items(page, patient, candidate, rows)}
        decisions = {item['candidate_id']: item for item in payload['items']}
        if len(decisions) != len(payload['items']) or set(decisions) != set(proposed):
            raise ApiError('ARCHIVE_CANDIDATES_NOT_DECIDED', 422)
        codes = [row.proposed_field['field']['field_code'] if candidate.schema_version == 'archive-fact-extraction-2'
            else row.proposed_field['field_code'] for row in rows]
        proofs += field_grants(actor, patient, 'read_raw', codes, batch.submitted_by, page_record_scope(page, patient))
        selected_codes = [item['field']['field_code'] for item in payload['items'] if item['decision'] != 'excluded']
        proofs += field_grants(actor, patient, 'write_record', selected_codes, batch.submitted_by, page_record_scope(page, patient))
        if mutating and (page.status != 'awaiting_confirmation' or candidate.status != 'pending_confirmation'
                or task.status != 'awaiting_confirmation'):
            raise ApiError('ARCHIVE_CANDIDATES_ALREADY_CONFIRMED', 409)
        configs = {row.field_config_version_id: row.field_config_version for row in rows}
        if mutating:
            counts = Counter(selected_codes)
            for row in rows:
                item, before = decisions[str(row.id)], proposed[str(row.id)]
                if item['decision'] == 'excluded':
                    if not item['reason'].strip():
                        raise ApiError('ARCHIVE_FACT_REASON_REQUIRED', 422)
                    continue
                field, context = item['field'], item['context']
                if field is None or context is None or (field['candidate_field_id'] != str(row.id)
                        or field['anchor_id'] != str(row.anchor_id) or field['client_field_id'] != before['field']['client_field_id']
                        or field['field_code'] != before['field']['field_code']):
                    raise ApiError('ARCHIVE_FACT_SOURCE_CHANGED', 422)
                if item['decision'] == 'accepted' and (field != before['field'] or context != before['context']):
                    raise ApiError('ARCHIVE_FACT_MODIFICATION_UNCONFIRMED', 422)
                if item['decision'] == 'modified' and (item['reason'] is None or not item['reason'].strip()):
                    raise ApiError('ARCHIVE_FACT_REASON_REQUIRED', 422)
                if item['decision'] == 'unknown' and (field['value_state'] != 'unknown'
                        or field['raw_numeric'] is not None or field['categorical_value'] is not None):
                    raise ApiError('ARCHIVE_FACT_UNKNOWN_VALUE_INVALID', 422)
                definition = field_definition(configs[row.field_config_version_id], field)
                if counts[field['field_code']] > definition['max_occurrences']:
                    raise ApiError('ARCHIVE_FACT_COUNT_EXCEEDED', 422)
            checked_bytes(source)
            lock_use_authorization(group.use_authorization_id, source, 'family_observation', gate.environment)
        for item in payload['items']:
            if item['decision'] != 'excluded':
                proofs += context_check(actor, patient, item['field'], item['context'])
        context = {'environment': gate.environment, 'auth_epoch': str(epoch), 'gate_version': str(gate.version),
            'patient_id': str(patient.id), 'page_id': str(page.id), 'page_version': str(page.version),
            'candidate_set_id': str(candidate.id), 'task_id': str(task.id), 'source_checksum': source.checksum,
            'source_version': source.object_version, 'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
            'field_configs': sorted([{'id': str(value.id), 'checksum': value.checksum} for value in configs.values()], key=lambda value: value['id']),
            'grants': sorted(proofs, key=lambda value: json.dumps(value, sort_keys=True))}
        return actor, patient, source.retention_policy, runtime, context, (page, candidate, rows, task, source, group, proposed)
    seed = ArchiveFact.objects.get(id=identifier)
    actor, patient, gate = batch_write_identity(actor, epoch, seed.patient_id)
    fact = ArchiveFact.objects.select_for_update().get(id=seed.id, patient_id=patient.id)
    version = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
    original_field, record_version = fact_source(version, patient)
    initial = RecordVersion.objects.get(record_id=record_version.record_id, patient=patient, version=1)
    proofs = field_grants(actor, patient, 'read_raw', [original_field.field_code], initial.author_id, record_version.record_id)
    proofs += field_grants(actor, patient, 'correct_record', [original_field.field_code], initial.author_id, record_version.record_id)
    field = payload['field']
    if (field['field_code'] != original_field.field_code or field['concept_code'] != original_field.concept_code
            or field['value_type'] != original_field.value_type
            or field['dictionary_entry_id'] != (str(original_field.dictionary_entry_id) if original_field.dictionary_entry_id else None)
            or field['anchor_id'] != (str(original_field.anchor_id) if original_field.anchor_id else None)):
        raise ApiError('ARCHIVE_FACT_IDENTITY_CHANGED', 422)
    if mutating and str(fact.current_version) != payload['expected_version']:
        raise ApiError('VERSION_CONFLICT', 409)
    if not payload['reason'].strip():
        raise ApiError('ARCHIVE_FACT_REASON_REQUIRED', 422)
    page = ArchivePage.objects.get(id=version.page_id, patient_id=patient.id) if version.page_id else None
    if page is not None:
        _, _, _, _, _, source, group, _, file_proofs = page_scope(actor, epoch, page.id)
        proofs += file_proofs
        if mutating:
            checked_bytes(source)
            lock_use_authorization(group.use_authorization_id, source, 'family_observation', gate.environment)
    proofs += context_check(actor, patient, field, payload['context'])
    if mutating:
        configurations = [value for value in ConfigVersion.objects.filter(kind='content', schema_version='field-config-1',
            status='enabled') if any(entry['field_code'] == field['field_code'] for entry in value.payload['fields'])
            and configuration_qualification_reason(value, gate.environment) is None]
        if len(configurations) != 1:
            raise ApiError('ARCHIVE_FIELD_CONFIGURATION_PENDING', 503)
        field_definition(configurations[0], field)
    runtime = batch_runtime()
    context = {'environment': gate.environment, 'auth_epoch': str(epoch), 'gate_version': str(gate.version),
        'patient_id': str(patient.id), 'fact_id': str(fact.id), 'fact_version': str(fact.current_version),
        'record_version_id': str(record_version.id), 'record_version': str(record_version.version),
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'grants': sorted(proofs, key=lambda value: json.dumps(value, sort_keys=True))}
    return actor, patient, record_version.retention_policy, runtime, context, (fact, version, original_field, record_version, page)


def save_context(version, context, page, document_id=None):
    documented = create_medical_time(RecordVersion.objects.get(id=version.record_version_id).patient,
        context['documented_time']) if context['documented_time'] is not None else None
    version.context_type, version.assertion_state = context['context_type'], context['assertion_state']
    version.episode_id, version.raw_time_text, version.time_origin = context['episode_id'], context['raw_time_text'], context['time_origin']
    version.documented_time_id = documented.id if documented else None
    if page is not None:
        version.page_id = page.id
    if document_id is not None:
        version.document_version_id = document_id
    version.save()
    node = resource_node('archive_fact_version', version.id, str(version.version), version.patient_id)
    for kind, identity in [('medical_time', version.medical_time_id), ('medical_time', version.documented_time_id),
            ('candidate_confirmation', version.confirmation_id),
            ('clinical_episode', version.episode_id)]:
        if identity is not None:
            connect(node, resource_node(kind, identity, '1', version.patient_id), 'source')
    if version.document_version_id is not None:
        from archives.models import ArchiveDocumentVersion

        document = ArchiveDocumentVersion.objects.get(id=version.document_version_id, patient_id=version.patient_id)
        connect(node, resource_node('archive_document_version', document.id, str(document.version), version.patient_id), 'source')


def confirm_facts(actor, patient, payload, objects):
    page, candidate, rows, task, source, group, proposed = objects
    decisions = {item['candidate_id']: item for item in payload['items']}
    fields, adopted = [], []
    for row in rows:
        item = decisions[str(row.id)]
        confirmation = CandidateConfirmation.objects.create(patient=patient, candidate_field_id=row.id,
            confirmed_by=actor, decision=item['decision'], confirmed_text=json.dumps(item, ensure_ascii=False), confirmed_at=timezone.now())
        if item['decision'] != 'excluded':
            fields.append({**item['field'], 'confirmation_id': confirmation.id})
            adopted.append((item, proposed[str(row.id)]))
    if fields:
        result = create_record(patient=patient, actor=actor, kind='report', capture_method='ocr_confirmed',
            origin_type='report_transcription', retention_policy=source.retention_policy,
            use_authorization_id=group.use_authorization_id, source_revision=group.source_revision,
            fields=fields, file_ids=[source.id])
        version = RecordVersion.objects.get(id=result['record_version_id'], patient=patient)
        saved_fields = list(RecordField.objects.filter(record_version_id=version.id, patient=patient).order_by('ordinal'))
        for field, (item, before) in zip(saved_fields, adopted, strict=True):
            fact = ArchiveFactVersion.objects.get(field_id=field.id, record_version_id=version.id, patient_id=patient.id)
            save_context(fact, item['context'], page, before['document_version_id'])
            node = resource_node('archive_fact_version', fact.id, str(fact.version), patient.id)
            connect(node, resource_node('record_version', version.id, str(version.version), patient.id), 'source')
            connect(node, resource_node('archive_page', page.id, str(page.version), patient.id), 'source')
            connect(node, resource_node('file_object', source.id, source.object_version, patient.id), 'source')
    candidate.status, task.status = 'confirmed', 'completed'
    candidate.save(update_fields=['status'])
    task.status_version += 1
    task.lease_until = timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    page.status, page.status_version = 'confirmed', page.status_version + 1
    page.save(update_fields=['status', 'status_version'])
    update_batch(page.batch_id)
    if not fields:
        type(patient).objects.filter(id=patient.id).update(data_epoch=F('data_epoch') + 1)
    return page.id, page.status_version


def correct_fact(actor, patient, payload, objects):
    fact, previous_fact, original, previous, page = objects
    originals = list(RecordField.objects.filter(record_version_id=previous.id, patient=patient).order_by('ordinal'))
    fields = []
    for value in originals:
        field = {name: getattr(value, name) for name in ('field_code', 'concept_code', 'original_name', 'value_type',
            'value_state', 'raw_text', 'categorical_value', 'qualifier', 'raw_unit', 'method', 'reference_interval', 'report_flag', 'sample_time_origin')}
        field.update(dictionary_entry_id=value.dictionary_entry_id, raw_numeric=str(value.raw_numeric) if value.raw_numeric is not None else None,
            medical_time=medical_time_view(value.medical_time_id, patient))
        if value.id == original.id:
            field = payload['field']
        fields.append(field)
    result = correct_record(record=Record.objects.get(id=previous.record_id, patient=patient), expected_version=previous.version,
        actor=actor, reason=payload['reason'], fields=fields, use_authorization_id=previous.use_authorization_id,
        retention_policy=previous.retention_policy, source_revision=previous.source_revision)
    saved = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=previous_fact.version + 1)
    save_context(saved, payload['context'], page)
    for source_fact in ArchiveFactVersion.objects.filter(record_version_id=previous.id, patient_id=patient.id):
        invalidate_resource('archive_fact_version', source_fact.id, str(source_fact.version))
    invalidate_resource('record_version', previous.id, str(previous.version))
    new_version = RecordVersion.objects.get(id=result['record_version_id'], patient=patient)
    node = resource_node('archive_fact_version', saved.id, str(saved.version), patient.id)
    connect(node, resource_node('record_version', new_version.id, str(new_version.version), patient.id), 'source')
    fact.refresh_from_db()
    return fact.id, fact.current_version
