import json

from api.base import ApiError
from archives.batches import page_read, page_sources, page_task
from archives.inputs import PROTOCOL, REGISTRY
from archives.models import ArchivePageAttempt, ArchiveDocumentPage, ArchiveDocumentVersion, ArchiveDocument
from configurations.models import ConfigVersion
from files.models import FileObject
from files.purpose import checked_bytes
from jsonschema import Draft202012Validator, FormatChecker
from recognition.archive_extraction import SCHEMA_VERSION, VALIDATOR
from recognition.models import CandidateConfirmation, CandidateField, CandidateSet, SourceAnchor
from recognition.schemas import REPORT_SCHEMA_VERSION
from recognition.service import add_candidate_field, add_source_anchor


FIELD_NAMES = ('client_field_id', 'field_code', 'concept_code', 'dictionary_entry_id', 'original_name',
    'value_type', 'value_state', 'raw_text', 'raw_numeric', 'categorical_value', 'qualifier', 'raw_unit',
    'method', 'reference_interval', 'report_flag', 'medical_time', 'sample_time_origin', 'anchor_id', 'candidate_field_id')
CONTEXT_NAMES = ('context_type', 'assertion_state', 'episode_id', 'medical_time', 'raw_time_text', 'time_origin', 'documented_time')


def validate_value(name, value):
    validator = Draft202012Validator({**PROTOCOL, '$ref': '#/$defs/' + name}, registry=REGISTRY,
        format_checker=FormatChecker())
    if not validator.is_valid(value):
        raise ApiError('ARCHIVE_FACT_PAYLOAD_INVALID', 422)
    return value


def unknown_context(raw_time=None, origin='unknown', context='unknown', assertion='unknown'):
    return {'context_type': context, 'assertion_state': assertion, 'episode_id': None, 'medical_time': None,
        'raw_time_text': raw_time, 'time_origin': origin, 'documented_time': None}


def field_input(identity, anchor_id, value):
    field = dict.fromkeys(FIELD_NAMES)
    field.update(value)
    field.update(client_field_id=str(identity), candidate_field_id=str(identity), anchor_id=str(anchor_id))
    return validate_value('FieldInput', field)


def save_extraction(candidate, page, extraction, config):
    for index, item in enumerate(extraction['items'], 1):
        anchor = add_source_anchor(patient=candidate.patient, page_id=page.file_page_id, original_text=item['source_quote'])
        locator = item['locator']
        if locator['anchor_kind'] == 'table_cell':
            anchor.row_label, anchor.column_label = str(locator['table_row']), str(locator['table_column'])
            anchor.save(update_fields=['row_label', 'column_label'])
        row = add_candidate_field(candidate_set=candidate, field_path=f'/items/{index}', field_config_version=config,
            recognition_status={'recognized': 'recognized', 'partial': 'uncertain', 'unknown': 'missing', 'unreadable': 'unlocated'}[item['recognition_status']],
            raw_text=item['source_quote'], anchor_id=anchor.id)
        kind = 'text' if item['value_type'] == 'categorical' else item['value_type']
        # 日期解析与单位映射须经本人确认，模型只保留准确原值。
        state = 'unknown' if kind == 'time' else item['value_state']
        field = field_input(row.id, anchor.id, {'field_code': {'decimal': 'report_item', 'text': 'archive_text',
            'time': 'archive_time'}[kind], 'original_name': item['original_name'],
            'value_type': kind, 'value_state': state, 'raw_text': item['raw_text'],
            'raw_numeric': item['raw_numeric'] if state == 'known' else None,
            'categorical_value': item['raw_text'] if kind == 'categorical' and state == 'known' else None,
            'qualifier': item['qualifier'], 'raw_unit': item['raw_unit'], 'method': item['method'],
            'reference_interval': item['reference_interval'],
            'sample_time_origin': 'item' if item['time_origin'] == 'explicit_common' else item['time_origin']})
        context = unknown_context(item['raw_time_text'], item['time_origin'], item['context_type'], item['assertion_state'])
        row.proposed_field = {'field': field, 'context': context, 'locator': locator,
            'episode_text': item['episode_text'], 'recognition_status': item['recognition_status'],
            'document_metadata': {name: extraction[name] for name in ('document_type', 'organization', 'documented_time_text', 'page_text', 'unreadable_regions')}}
        row.save(update_fields=['proposed_field'])


def page_candidate_set(page, patient):
    data = page_read(page, patient)
    if data['candidate_set_id'] is None:
        raise ApiError('ARCHIVE_CANDIDATES_UNAVAILABLE', 409)
    candidate = CandidateSet.objects.select_for_update().get(id=data['candidate_set_id'], patient=patient)
    _, _, group, _ = page_sources(page, patient)
    task = page_task(page, patient, candidate.task_id)
    attempt = ArchivePageAttempt.objects.get(page_id=page.id, task_id=task.id, candidate_set_id=candidate.id,
        page_version=page.version, status='completed')
    if (candidate.file_group_id != group.id or candidate.file_group_version != group.version
            or task.id != page.current_task_id or candidate.provider_request_id != attempt.provider_request_id):
        raise ApiError('ARCHIVE_CANDIDATE_SOURCE_INVALID', 409)
    rows = list(CandidateField.objects.select_for_update().filter(candidate_set_id=candidate.id,
        patient=patient).order_by('field_path'))
    rows.sort(key=lambda value: int(value.field_path.rsplit('/', 1)[1]))
    return candidate, rows, task


def current_document(page):
    links = ArchiveDocumentPage.objects.filter(page_id=page.id, patient_id=page.patient_id)
    versions = ArchiveDocumentVersion.objects.filter(id__in=links.values('document_version_id'), patient_id=page.patient_id)
    current = [version for version in versions if ArchiveDocument.objects.filter(id=version.document_id,
        retired_at__isnull=True, current_version=version.version).exists()]
    if len(current) > 1:
        raise ApiError('ARCHIVE_CANDIDATE_DOCUMENT_AMBIGUOUS', 409)
    return current[0] if current else None


def candidate_items(page, patient, candidate, rows):
    document = current_document(page)
    items = []
    for row in rows:
        anchor = SourceAnchor.objects.get(id=row.anchor_id, patient=patient, page_id=page.file_page_id)
        proposed = row.proposed_field
        if candidate.schema_version == SCHEMA_VERSION:
            field, context, status = proposed['field'], proposed['context'], proposed['recognition_status']
        elif candidate.schema_version == REPORT_SCHEMA_VERSION:
            field = field_input(row.id, anchor.id, {name: proposed.get(name) for name in FIELD_NAMES if name in proposed})
            context = unknown_context()
            status = {'recognized': 'recognized', 'uncertain': 'partial', 'missing': 'unknown', 'unlocated': 'unreadable'}[row.recognition_status]
        else:
            raise ApiError('ARCHIVE_CANDIDATE_SCHEMA_UNSUPPORTED', 409)
        if field['anchor_id'] != str(anchor.id) or field['candidate_field_id'] != str(row.id):
            raise ApiError('ARCHIVE_CANDIDATE_SOURCE_INVALID', 409)
        confirmation = None
        if candidate.schema_version == SCHEMA_VERSION and candidate.status == 'confirmed':
            saved = CandidateConfirmation.objects.get(candidate_field_id=row.id, patient=patient)
            confirmation = validate_value('FactDecision', json.loads(saved.confirmed_text))
            if confirmation['candidate_id'] != str(row.id) or confirmation['decision'] != saved.decision:
                raise ApiError('ARCHIVE_CANDIDATE_CONFIRMATION_INVALID', 409)
        item = {'candidate_id': str(row.id), 'field': field, 'context': context,
            'document_version_id': str(document.id) if document else None, 'page_id': str(page.id),
            'source_anchor_id': str(anchor.id), 'recognition_status': status, 'source_quote': anchor.original_text,
            'confirmation': confirmation}
        validate_value('FactCandidate', item)
        items.append(item)
    return items


def candidate_view(page, patient):
    candidate, rows, _ = page_candidate_set(page, patient)
    extraction = None
    if candidate.schema_version == SCHEMA_VERSION:
        raw_file = FileObject.objects.get(id=candidate.raw_response_file_id, patient=patient, purpose='ocr_response')
        completion = json.loads(checked_bytes(raw_file))
        if completion['id'] != candidate.provider_request_id:
            raise ApiError('ARCHIVE_CANDIDATE_RESPONSE_INVALID', 409)
        extraction = json.loads(completion['choices'][0]['message']['content'])
        VALIDATOR.validate(extraction)
    value = {'candidate_set_id': str(candidate.id), 'page_id': str(page.id), 'page_version': str(page.version),
        'model_version': candidate.model_version, 'status': {'pending_confirmation': 'awaiting_confirmation',
            'confirmed': 'confirmed', 'superseded': 'superseded', 'cancelled': 'cancelled'}[candidate.status],
        'items': candidate_items(page, patient, candidate, rows), 'extraction': extraction}
    return validate_value('FactCandidateSet', value)
