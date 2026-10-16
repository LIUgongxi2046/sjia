import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid5

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from authorization.policy import evaluate
from configurations.field_config import validate_record_configuration
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from files.models import FileObject, FilePage
from files.purpose import checked_bytes, lock_original, lock_use_authorization, runtime_environment
from files.storage import digest_bytes
from family_content.models import EnvironmentGate
from identity.models import Principal
from records.models import Record, RecordVersion
from records.service import create_record, read_version
from recognition.models import VoiceInput
from requests.service import commit_intent, prepare_intent
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import Task

from .voice_input_schemas import VoiceInputAdoption, VoiceInputCreate, VoiceInputProcess
from .invocation import contract_snapshot


class VoiceInputRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _lock_environment_gate():
    gate = EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
    if gate is None:
        raise VoiceInputRejected('VOICE_RUNTIME_CONFIGURATION_REQUIRED')
    return gate


def _authorized(actor, patient, auth_epoch, action, purpose, *, record_id=None, author_id=None, whole_resource=False):
    actor.refresh_from_db()
    evaluate(principal=actor, patient=patient, action=action, purpose=purpose,
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=auth_epoch,
        record_id=record_id, author_id=author_id, whole_resource=whole_resource)


@transaction.atomic
def create_voice_input(*, patient, actor, auth_epoch, original_file_id, request):
    payload = VoiceInputCreate.model_validate(request)
    _lock_environment_gate()
    patient = type(patient).objects.select_for_update().get(id=patient.id)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    _authorized(actor, patient, auth_epoch, 'write_record', 'family_observation')
    _authorized(actor, patient, auth_epoch, 'read_file', 'external_recognition')
    original = lock_original(original_file_id)
    if original.patient_id != patient.id:
        raise VoiceInputRejected('VOICE_ORIGINAL_PATIENT_MISMATCH')
    raw = checked_bytes(original)
    if original.media_type != 'text/plain':
        raise VoiceInputRejected('VOICE_CONFIRMED_TEXT_FILE_REQUIRED')
    if payload.input_mode == 'SIMULATION_TEXT_ONLY':
        if original.media_type != 'text/plain':
            raise VoiceInputRejected('VOICE_SIMULATION_TEXT_ORIGINAL_INVALID')
        transcript = raw.decode('utf-8', errors='strict')
        if transcript != payload.confirmed_text:
            raise VoiceInputRejected('VOICE_CONFIRMED_TEXT_MISMATCH')
        transcription_evidence = {'mode': 'SIMULATION_TEXT_ONLY', 'original_file_id': str(original.id),
            'original_checksum': original.checksum, 'transcript_sha256': digest_bytes(transcript.encode('utf-8'))}
    else:
        from recognition.local_transcription import verify_local_transcription_confirmation

        transcription_evidence = verify_local_transcription_confirmation(
            evidence_id=payload.transcription_evidence_id, transcript_file=original, actor=actor,
            patient=patient, confirmed_text=payload.confirmed_text, auth_epoch=auth_epoch)
        transcript = payload.confirmed_text
    if not transcript.strip():
        raise VoiceInputRejected('VOICE_CONFIRMED_TEXT_EMPTY')
    auth = lock_use_authorization(payload.authorization_id, original, 'external_recognition', runtime_environment())
    field_config = ConfigVersion.objects.select_for_update().get(id=payload.field_config_id,
        kind='content', schema_version='field-config-1', status='enabled', payload__kind='observation')
    if payload.input_mode == 'SIMULATION_TEXT_ONLY' and settings.SJIA_ENVIRONMENT == 'production':
        raise VoiceInputRejected('SIMULATION_INPUT_FORBIDDEN_IN_PRODUCTION')
    existing = VoiceInput.objects.select_for_update().filter(actor=actor, request_id=payload.request_id).first()
    if existing:
        if (existing.patient_id != patient.id or existing.original_file_id != original.id
                or existing.transcript_sha256 != digest_bytes(transcript.encode('utf-8')) or existing.input_mode != payload.input_mode
                or existing.use_authorization_id != auth.id or existing.field_config_id != field_config.id
                or existing.transcription_evidence != transcription_evidence):
            raise VoiceInputRejected('VOICE_REQUEST_IDEMPOTENCY_CONFLICT')
        return existing
    return VoiceInput.objects.create(patient=patient, actor=actor, request_id=payload.request_id,
        transcript=transcript, transcript_sha256=digest_bytes(transcript.encode('utf-8')), input_mode=payload.input_mode,
        original_file=original, purpose_copy_id=None, use_authorization=auth, task=None,
        provider_config=None, field_config=field_config, status='ready', status_version=1,
        candidates=None, raw_response_file=None, decisions=None, saved_record_version=None,
        transcription_evidence=transcription_evidence,
        confirmed_at=timezone.now(), created_at=timezone.now())


def _text_copy(voice_input, actor, auth_epoch, copy_id, target):
    from archives.models import PurposeCopy
    from files.purpose import current_review, lock_copy_context

    copy = PurposeCopy.objects.select_for_update().get(id=copy_id, original_file_id=voice_input.original_file_id,
        patient_id=voice_input.patient_id, purpose='external_recognition', environment=runtime_environment(),
        status='approved')
    if copy.processing_evidence.get('target') != target:
        raise VoiceInputRejected('VOICE_PURPOSE_TARGET_MISMATCH')
    locked, original, derived, *_ = lock_copy_context(copy.id, actor, auth_epoch, service=False)
    if current_review(locked, original, derived) is None:
        raise VoiceInputRejected('VOICE_PURPOSE_COPY_REVIEW_REQUIRED')
    _verify_confirmed_text_copy(locked, original, derived, target, voice_input.transcript)
    return {'copy_id': str(locked.id), 'copy_version': str(locked.version),
        'checksum': derived.checksum, 'target': target}


def _verify_confirmed_text_copy(copy, original, derived, target, transcript):
    from files.purpose_schemas import ConfirmedTextProcessingPlan

    evidence = copy.processing_evidence
    if (copy.processing_version != 'confirmed-text-purpose-1' or derived.media_type != 'text/plain'
            or derived.derived_from_id != original.id or derived.purpose != 'deidentified'
            or evidence.get('processing_version') != 'confirmed-text-purpose-1'
            or evidence.get('full_text_included') is not True
            or evidence.get('source_media_type') != 'text/plain'
            or evidence.get('output_media_type') != 'text/plain'
            or evidence.get('original_checksum') != original.checksum
            or evidence.get('derived_checksum') != derived.checksum
            or not evidence.get('source_page_numbers') or not evidence.get('prepared_page_numbers')):
        raise VoiceInputRejected('VOICE_TEXT_PROCESSING_EVIDENCE_INVALID')
    config = ConfigVersion.objects.select_for_update().get(id=evidence['config_id'], kind='provider',
        schema_version='purpose-processing-text-1')
    plan = ConfirmedTextProcessingPlan.model_validate(config.payload)
    prepared = FileObject.objects.select_for_update().get(id=plan.prepared_file_id, patient_id=original.patient_id)
    if (plan.original_file_id != original.id or plan.patient_id != original.patient_id
            or plan.original_checksum != original.checksum or plan.original_version != original.object_version
            or plan.prepared_file_id != prepared.id or plan.prepared_checksum != prepared.checksum
            or prepared.status != 'available' or prepared.purpose != 'deidentified'
            or prepared.derived_from_id != original.id or prepared.file_group_id != original.file_group_id
            or prepared.media_type != 'text/plain' or evidence.get('prepared_checksum') != prepared.checksum
            or evidence.get('original_checksum') != original.checksum
            or evidence.get('derived_checksum') != derived.checksum
            or plan.target != target or plan.environment != settings.SJIA_ENVIRONMENT
            or plan.purpose != 'external_recognition' or plan.mode != 'prepared_text'):
        raise VoiceInputRejected('VOICE_TEXT_PROCESSING_PLAN_MISMATCH')
    prepared_text = checked_bytes(prepared).decode('utf-8', errors='strict')
    text = checked_bytes(derived).decode('utf-8', errors='strict')
    if prepared_text != transcript or text != transcript or len(text) > 8000:
        raise VoiceInputRejected('VOICE_PURPOSE_COPY_CONTENT_MISMATCH')
    return plan


@transaction.atomic
def prepare_voice_processing(*, voice_input_id, actor, auth_epoch, request):
    payload = VoiceInputProcess.model_validate(request)
    _lock_environment_gate()
    voice_input = VoiceInput.objects.select_for_update().select_related('patient', 'actor', 'field_config').get(
        id=voice_input_id, actor=actor)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    if str(voice_input.status_version) != payload.expected_status_version:
        if voice_input.task_id and voice_input.status in ('running', 'awaiting_confirmation', 'uncertain'):
            prior = Task.objects.get(id=voice_input.task_id, patient=voice_input.patient)
            refs = prior.input_refs
            if (refs.get('purpose_copy', {}).get('copy_id') == str(payload.purpose_copy_id)
                    and refs.get('provider_config_id') == str(payload.provider_config_id)):
                return prior
        raise VoiceInputRejected('VOICE_STATUS_VERSION_CONFLICT')
    if voice_input.status not in ('ready', 'awaiting_review'):
        if voice_input.task_id:
            task = Task.objects.get(id=voice_input.task_id, patient=voice_input.patient)
            return task
        raise VoiceInputRejected('VOICE_STATE_NOT_PROCESSABLE')
    _authorized(actor, voice_input.patient, auth_epoch, 'read_file', 'external_recognition')
    _authorized(actor, voice_input.patient, auth_epoch, 'write_record', 'family_observation')
    original = lock_original(voice_input.original_file_id)
    auth = lock_use_authorization(voice_input.use_authorization_id, original, 'external_recognition', runtime_environment())
    if auth.revoked_at is not None:
        raise VoiceInputRejected('VOICE_AUTHORIZATION_REVOKED')
    provider = ConfigVersion.objects.select_for_update().get(id=payload.provider_config_id,
        kind='provider', schema_version='provider-1', status='enabled')
    if configuration_qualification_reason(provider, settings.SJIA_ENVIRONMENT) is not None:
        raise VoiceInputRejected('VOICE_PROVIDER_CONFIGURATION_UNQUALIFIED')
    runtime, provider_entry, task_config = _voice_runtime(provider)
    if provider.payload['field_config_id'] != str(voice_input.field_config_id):
        raise VoiceInputRejected('VOICE_FIELD_CONFIGURATION_MISMATCH')
    purpose_ref = _text_copy(voice_input, actor, auth_epoch, payload.purpose_copy_id,
        provider.payload['endpoint'].rstrip('/'))
    existing = Task.objects.select_for_update().filter(task_type='ocr', schema_version='voice-input-1',
        actor=actor, input_refs__voice_input_id=str(voice_input.id)).first()
    if existing:
        if existing.input_refs['purpose_copy'] != purpose_ref or existing.input_refs['provider_config_id'] != str(provider.id):
            raise VoiceInputRejected('VOICE_TASK_REQUEST_CONFLICT')
        return existing
    budget_evidence = _check_voice_budget(runtime, provider_entry, task_config)
    refs = {
            'voice_input_id': str(voice_input.id), 'transcript_sha256': voice_input.transcript_sha256,
            'original_file_id': str(original.id), 'original_checksum': original.checksum,
            'authorization_id': str(auth.id), 'auth_epoch': str(auth_epoch),
            'purpose_copy': purpose_ref, 'provider_config_id': str(provider.id),
            'provider_checksum': provider.checksum, 'runtime_config_id': str(runtime.id),
            'runtime_checksum': runtime.checksum, 'provider_entry': provider_entry,
            'task_config': task_config, 'invocation_contract': contract_snapshot('voice'),
            'budget_evidence': budget_evidence,
            'field_config_id': str(voice_input.field_config_id), 'reserved_cost': provider_entry['reserved_cost'],
            'environment': settings.SJIA_ENVIRONMENT, 'input_mode': voice_input.input_mode,
        }
    from tasks.service import register_task
    task = register_task(actor=actor, patient=voice_input.patient, task_type='ocr',
        schema_version='voice-input-1', input_refs=refs,
        deadline_seconds=task_config['queue_wait_seconds'] + task_config['execution_seconds'])
    voice_input.task = task
    voice_input.provider_config = provider
    voice_input.purpose_copy_id = payload.purpose_copy_id
    voice_input.status = 'running'
    voice_input.status_version += 1
    voice_input.save(update_fields=['task', 'provider_config', 'purpose_copy_id', 'status', 'status_version'])
    from tasks.outbox import emit_event
    emit_event(event_type='voice_input.requested', schema_version='voice-input-1', aggregate_id=voice_input.id,
        aggregate_version=voice_input.status_version, correlation_id=task.id,
        resource_refs={'task_id': str(task.id), 'voice_input_id': str(voice_input.id)}, task=task)
    return task


def _voice_runtime(provider):
    from configurations.qualification import configuration_qualification_reason

    gate = EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
    if gate is None or not gate.enabled or gate.runtime_config_id is None:
        raise VoiceInputRejected('VOICE_RUNTIME_CONFIGURATION_REQUIRED')
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1', status='enabled').first()
    if runtime is None or configuration_qualification_reason(runtime, settings.SJIA_ENVIRONMENT) is not None:
        raise VoiceInputRejected('VOICE_RUNTIME_CONFIGURATION_UNQUALIFIED')
    entries = [item for item in runtime.payload['providers'] if item['kind'] == 'voice' and item['enabled']
        and item['config_id'] == str(provider.id)]
    if len(entries) != 1 or entries[0]['config_id'] != str(provider.id):
        raise VoiceInputRejected('VOICE_PROVIDER_RUNTIME_MISMATCH')
    entry = entries[0]
    if (not isinstance(entry.get('billing_scope_key'), str) or not entry['billing_scope_key'].strip()
            or entry['endpoint'].rstrip('/') != provider.payload['endpoint'].rstrip('/')
            or entry['currency'] != provider.payload['currency'] or entry['daily_budget'] is None
            or Decimal(entry['daily_budget']) <= 0 or Decimal(entry['reserved_cost']) <= 0):
        raise VoiceInputRejected('VOICE_PROVIDER_BUDGET_INVALID')
    task_entries = [item for item in runtime.payload['tasks'] if item['task_type'] == 'ocr']
    if len(task_entries) != 1:
        raise VoiceInputRejected('VOICE_TASK_RUNTIME_MISSING')
    return runtime, entry, task_entries[0]


def _check_voice_budget(runtime, provider_entry, task_config):
    from .provider_budget import check_provider_budget

    return check_provider_budget(runtime=runtime, billing_scope_key=provider_entry['billing_scope_key'],
        currency=provider_entry['currency'], daily_budget=provider_entry['daily_budget'],
        reserved_cost=provider_entry['reserved_cost'], budget_timezone=settings.TIME_ZONE,
        max_concurrency=task_config['max_concurrency'], reject=VoiceInputRejected)


@transaction.atomic
def cancel_voice_input(*, voice_input_id, actor, auth_epoch, expected_status_version):
    from tasks.service import cancel_task
    from tasks.models import TaskAttempt

    _lock_environment_gate()
    voice_input = VoiceInput.objects.select_for_update().get(id=voice_input_id, actor=actor)
    _authorized(actor, voice_input.patient, auth_epoch, 'read_file', 'family_observation')
    if voice_input.status_version != int(expected_status_version):
        raise VoiceInputRejected('VOICE_STATUS_VERSION_CONFLICT')
    if voice_input.status in ('saved', 'cancelled', 'revoked', 'failed'):
        return voice_input
    if voice_input.task_id:
        task = Task.objects.select_for_update().get(id=voice_input.task_id)
        cancel_task(task.id)
        task.refresh_from_db()
        attempts = list(TaskAttempt.objects.filter(task=task))
        if any(attempt.status == TaskAttempt.Status.UNCERTAIN and attempt.usage_evidence
                and attempt.usage_evidence.get('provider_called') and attempt.usage_evidence.get('actual_cost') is None
                for attempt in attempts):
            voice_input.status = 'uncertain'
        else:
            voice_input.status = 'cancelled'
    else:
        voice_input.status = 'cancelled'
    voice_input.cancelled_at = timezone.now()
    voice_input.status_version += 1
    voice_input.save(update_fields=['status', 'cancelled_at', 'status_version'])
    return voice_input


def _voice_record_fields(voice_input, decisions, candidates, definitions, page):
    by_path = {row.field_path: row for row in decisions}
    expected_codes = {'temperature': 'temp', 'temperature_unit': 'temp',
        'symptoms': 'observation_note', 'note': 'observation_note'}
    for path, code in expected_codes.items():
        row = by_path[path]
        if row.decision == 'excluded':
            continue
        if row.record_field_codes != [code]:
            raise VoiceInputRejected('VOICE_FIELD_MAPPING_INVALID')
    time_row = by_path['observed_time']
    if time_row.decision != 'excluded' and (len(time_row.record_field_codes) != 1
            or time_row.record_field_codes[0] not in ('temp', 'observation_note')):
        raise VoiceInputRejected('VOICE_TIME_FIELD_MAPPING_INVALID')
    for row in decisions:
        candidate = candidates.get(row.field_path)
        if row.decision in ('accepted', 'modified') and row.selected_value is None:
            raise VoiceInputRejected('VOICE_SELECTED_VALUE_REQUIRED')
        if row.decision == 'accepted':
            if row.field_path == 'temperature':
                if candidate is None or Decimal(str(candidate)) != Decimal(row.selected_value):
                    raise VoiceInputRejected('VOICE_ACCEPTED_VALUE_MISMATCH')
            elif row.field_path == 'observed_time':
                if candidate is None or not isinstance(row.selected_value, dict) or row.selected_value.get('raw_text') != candidate:
                    raise VoiceInputRejected('VOICE_ACCEPTED_VALUE_MISMATCH')
            elif row.selected_value != candidate:
                raise VoiceInputRejected('VOICE_ACCEPTED_VALUE_MISMATCH')
    from recognition.service import add_source_anchor

    def field_input(code, original_name, value_type, value_state, **values):
        anchor = add_source_anchor(patient=voice_input.patient, page_id=page.id,
            original_text=voice_input.transcript)
        return {'client_field_id': str(uuid5(voice_input.id, f'voice-field:{code}')),
            'field_code': code, 'concept_code': None, 'dictionary_entry_id': None,
            'original_name': original_name, 'value_type': value_type, 'value_state': value_state,
            'raw_text': None, 'raw_numeric': None, 'categorical_value': None, 'qualifier': None,
            'raw_unit': None, 'method': None, 'reference_interval': None, 'report_flag': None,
            'medical_time': None, 'sample_time_origin': 'unknown', 'anchor_id': str(anchor.id),
            'candidate_field_id': None, **values}

    fields = []
    temp = by_path['temperature']
    unit = by_path['temperature_unit']
    temp_time = time_row if time_row.decision != 'excluded' and time_row.record_field_codes == ['temp'] else None
    if any(row.decision != 'excluded' for row in (temp, unit)) or temp_time is not None:
        if temp.decision == 'excluded' and unit.decision != 'excluded':
            raise VoiceInputRejected('VOICE_TEMPERATURE_UNIT_WITHOUT_VALUE')
        if 'temp' not in definitions or definitions['temp']['value_type'] != 'decimal' or not definitions['temp']['writable']:
            raise VoiceInputRejected('VOICE_TEMPERATURE_CONFIGURATION_INVALID')
        field = field_input('temp', 'temperature', 'decimal',
            'known' if temp.decision in ('accepted', 'modified') else 'unknown')
        if temp.decision in ('accepted', 'modified'):
            field.update(raw_numeric=temp.selected_value, qualifier='eq')
        if unit.decision in ('accepted', 'modified'):
            if not isinstance(unit.selected_value, str):
                raise VoiceInputRejected('VOICE_UNIT_MAPPING_INVALID')
            field['raw_unit'] = unit.selected_value
        if temp_time is not None:
            if temp_time.decision in ('accepted', 'modified'):
                if not isinstance(temp_time.selected_value, dict):
                    raise VoiceInputRejected('VOICE_TIME_MAPPING_INVALID')
                field['medical_time'] = temp_time.selected_value
            elif temp_time.decision == 'unknown':
                field['value_state'] = 'unknown'
        fields.append(field)
    symptoms = by_path['symptoms']
    note = by_path['note']
    note_time = time_row if time_row.decision != 'excluded' and time_row.record_field_codes == ['observation_note'] else None
    if any(row.decision != 'excluded' for row in (symptoms, note)) or note_time is not None:
        if ('observation_note' not in definitions or definitions['observation_note']['value_type'] != 'text'
                or not definitions['observation_note']['writable']):
            raise VoiceInputRejected('VOICE_NOTE_CONFIGURATION_INVALID')
        selections = {}
        if symptoms.decision in ('accepted', 'modified'):
            values = symptoms.selected_value if isinstance(symptoms.selected_value, list) else [symptoms.selected_value]
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise VoiceInputRejected('VOICE_SYMPTOM_MAPPING_INVALID')
            selections['symptoms'] = values
        if note.decision in ('accepted', 'modified'):
            if not isinstance(note.selected_value, str):
                raise VoiceInputRejected('VOICE_NOTE_MAPPING_INVALID')
            selections['note'] = note.selected_value
        field = field_input('observation_note', 'symptoms_and_note', 'text',
            'known' if selections else 'unknown')
        if selections:
            field['raw_text'] = json.dumps(selections, ensure_ascii=False, separators=(',', ':'))
        if note_time is not None:
            if note_time.decision in ('accepted', 'modified'):
                if not isinstance(note_time.selected_value, dict):
                    raise VoiceInputRejected('VOICE_TIME_MAPPING_INVALID')
                field['medical_time'] = note_time.selected_value
        fields.append(field)
    return fields


@transaction.atomic
def adopt_voice_input(*, request, actor, auth_epoch):
    payload = VoiceInputAdoption.model_validate(request)
    _lock_environment_gate()
    voice_input = VoiceInput.objects.select_for_update().select_related('patient', 'field_config', 'use_authorization').get(
        id=payload.voice_input_id, actor=actor)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    _authorized(actor, voice_input.patient, auth_epoch, 'write_record', 'family_observation')
    _authorized(actor, voice_input.patient, auth_epoch, 'read_file', 'family_observation')
    _authorized(actor, voice_input.patient, auth_epoch, 'read_raw', 'family_observation',
        author_id=actor.id, whole_resource=True)
    original = lock_original(voice_input.original_file_id)
    lock_use_authorization(voice_input.use_authorization_id, original, 'external_recognition', settings.SJIA_ENVIRONMENT)
    model_auth = voice_input.use_authorization
    auth_rows = list(DataUseAuthorization.objects.select_for_update().filter(patient=voice_input.patient,
        purpose='family_observation', environment=settings.SJIA_ENVIRONMENT, revoked_at__isnull=True,
        source_revision_id=model_auth.source_revision_id, approved_at__lte=timezone.now()).exclude(valid_until__lte=timezone.now()))
    if len(auth_rows) != 1:
        raise VoiceInputRejected('VOICE_RECORD_AUTHORIZATION_REQUIRED')
    auth = auth_rows[0]
    if voice_input.status_version != int(payload.expected_status_version):
        if voice_input.status == 'saved' and voice_input.decisions == payload.model_dump(mode='json')['decisions']:
            record_version = voice_input.saved_record_version
            if record_version is None:
                raise VoiceInputRejected('VOICE_SAVED_RECORD_VERSION_MISSING')
            lock_use_authorization(record_version.use_authorization_id, original,
                'family_observation', settings.SJIA_ENVIRONMENT)
            result = _read_voice_record_version(record_version, voice_input.patient, actor, auth_epoch)
            return {'record': result, 'idempotent_replay': True}
        raise VoiceInputRejected('VOICE_STATUS_VERSION_CONFLICT')
    if voice_input.status != 'awaiting_confirmation' or not voice_input.candidates:
        raise VoiceInputRejected('VOICE_CANDIDATE_NOT_AVAILABLE')
    retention = RetentionPolicy.objects.select_for_update().filter(status='active', data_class='observation',
        approved_at__isnull=False, approved_by__isnull=False).order_by('-approved_at', '-version').first()
    if retention is None:
        raise VoiceInputRejected('VOICE_RETENTION_POLICY_REQUIRED')
    decisions = payload.decisions
    definitions = {row['field_code']: row for row in voice_input.field_config.payload['fields']}
    pages = list(FilePage.objects.select_for_update().filter(patient=voice_input.patient, file_id=original.id))
    if len(pages) != 1:
        raise VoiceInputRejected('VOICE_SOURCE_PAGE_SELECTION_REQUIRED')
    fields = _voice_record_fields(voice_input, decisions, voice_input.candidates, definitions, pages[0])
    if any(not definitions[field['field_code']]['writable'] for field in fields):
        raise VoiceInputRejected('VOICE_FIELD_CODE_UNAVAILABLE')
    if not fields:
        raise VoiceInputRejected('VOICE_NO_FIELDS_TO_SAVE')
    validate_record_configuration(voice_input.field_config_id, 'observation', fields)
    target = f'/patients/{voice_input.patient_id}/records'
    body = {'kind': 'observation', 'capture_method': 'voice_confirmed', 'origin_type': 'self_report',
        'field_config_version': str(voice_input.field_config_id), 'file_ids': [str(original.id)],
        'fields': fields, 'source_organization': None, 'report': None}
    auth_context = {'environment': settings.SJIA_ENVIRONMENT, 'auth_epoch': str(auth_epoch),
        'patient_id': str(voice_input.patient_id), 'authorization_id': str(auth.id),
        'voice_input_id': str(voice_input.id), 'transcript_sha256': voice_input.transcript_sha256}
    intent = prepare_intent(actor=actor, operation_id='record_create', target_key=target,
        request_id=voice_input.request_id, payload=body, schema_version='back-json-1',
        retention_policy=retention, patient=voice_input.patient, authorization_context=auth_context,
        recovery_payload={'voice_input_id': str(voice_input.id), 'task_id': str(voice_input.task_id)})
    if intent.status == 'committed':
        record = Record.objects.select_for_update().get(id=intent.write_request.resource_id, patient=voice_input.patient)
        record_version = RecordVersion.objects.select_for_update().get(
            record_id=record.id, patient=voice_input.patient, version=record.current_version)
        lock_use_authorization(record_version.use_authorization_id, original,
            'family_observation', settings.SJIA_ENVIRONMENT)
        result = _read_voice_record_version(record_version, voice_input.patient, actor, auth_epoch)
    elif intent.status == 'prepared' and intent.expires_at > timezone.now():
        saved = create_record(patient=voice_input.patient, actor=actor, kind='observation',
            capture_method='voice_confirmed', origin_type='self_report', retention_policy=retention,
            use_authorization_id=auth.id, fields=fields, source_revision=auth.source_revision,
            file_ids=[original.id])
        commit_intent(intent=intent, action='record_create', resource_type='record', resource_id=saved['record_id'])
        record_version = RecordVersion.objects.select_for_update().get(
            id=saved['record_version_id'], patient=voice_input.patient)
        lock_use_authorization(record_version.use_authorization_id, original,
            'family_observation', settings.SJIA_ENVIRONMENT)
        result = _read_voice_record_version(record_version, voice_input.patient, actor, auth_epoch)
    else:
        raise VoiceInputRejected('VOICE_WRITE_INTENT_NOT_REUSABLE')
    _assert_saved_fields(result, fields, original.id, voice_input.transcript)
    voice_input.decisions = [item.model_dump(mode='json') for item in decisions]
    voice_input.saved_record_version_id = result['record_version_id']
    voice_input.status = 'saved'
    voice_input.decided_at = timezone.now()
    voice_input.status_version += 1
    voice_input.save(update_fields=['decisions', 'saved_record_version', 'status', 'decided_at', 'status_version'])
    task = Task.objects.select_for_update().get(id=voice_input.task_id, patient=voice_input.patient)
    if task.status != 'awaiting_confirmation':
        raise VoiceInputRejected('VOICE_TASK_NOT_CONFIRMABLE')
    task.status = 'completed'
    task.status_version += 1
    task.lease_until = timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    return {'record': result, 'idempotent_replay': False}


def _read_voice_record_version(record_version, patient, actor, auth_epoch):
    record = Record.objects.select_for_update().get(id=record_version.record_id, patient=patient)
    _authorized(actor, patient, auth_epoch, 'read_raw', 'family_observation',
        record_id=record.id, author_id=record_version.author_id, whole_resource=True)
    return read_version(record_version, record)


def _assert_saved_fields(result, expected, original_file_id, transcript):
    actual = result['fields']
    if len(actual) != len(expected):
        raise VoiceInputRejected('VOICE_RECORD_READBACK_MISMATCH')
    for left, right in zip(actual, expected, strict=True):
        if left['field_code'] != right['field_code'] or left['value_type'] != right['value_type']:
            raise VoiceInputRejected('VOICE_RECORD_READBACK_MISMATCH')
        if right.get('raw_numeric') is not None and left['raw_numeric'] != str(Decimal(right['raw_numeric'])):
            raise VoiceInputRejected('VOICE_RECORD_READBACK_MISMATCH')
        if right.get('raw_text') is not None and left['raw_text'] != right['raw_text']:
            raise VoiceInputRejected('VOICE_RECORD_READBACK_MISMATCH')
        if left['raw_unit'] != right.get('raw_unit') or left['value_state'] != right['value_state']:
            raise VoiceInputRejected('VOICE_RECORD_READBACK_MISMATCH')
        if (left['source_anchor'] is None or left['source_anchor']['file_id'] != str(original_file_id)
                or left['source_anchor']['original_text'] != transcript):
            raise VoiceInputRejected('VOICE_SOURCE_READBACK_MISMATCH')
        if right.get('medical_time') is not None:
            actual_time = left['medical_time']
            if actual_time is None or any(actual_time.get(key) != value for key, value in right['medical_time'].items()):
                raise VoiceInputRejected('VOICE_TIME_READBACK_MISMATCH')
