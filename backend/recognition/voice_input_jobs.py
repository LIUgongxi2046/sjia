import json
import time
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from archives.models import PurposeCopy
from authorization.policy import evaluate
from configurations.models import ConfigVersion
from files.model_input import model_copy_input, require_model_text
from files.models import FileObject
from files.purpose import checked_bytes, current_review, lock_copy_context, lock_original, lock_use_authorization
from files.service import object_store
from files.storage import build_object_key, digest_bytes
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from recognition.models import VoiceInput
from recognition.voice_input_service import _verify_confirmed_text_copy
from recognition.voice_completion import checked_voice_candidates, voice_completion_with_evidence
from sources.models import DataUseAuthorization
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import record_receipt
from tasks.lease import lease_heartbeat
from tasks.service import acquire_lease, create_attempt, finish_attempt


def _authorize(task):
    task.actor.refresh_from_db()
    task.patient.refresh_from_db()
    evaluate(principal=task.actor, patient=task.patient, action='read_file', purpose='external_recognition',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=int(task.input_refs['auth_epoch']))
    evaluate(principal=task.actor, patient=task.patient, action='write_record', purpose='family_observation',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=int(task.input_refs['auth_epoch']))


@transaction.atomic
def _execution_scope(task):
    from configurations.qualification import configuration_qualification_reason
    from files.purpose import runtime_environment

    gate = EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
    if gate is None or not gate.enabled:
        raise ValueError('VOICE_RUNTIME_CONFIGURATION_REQUIRED')
    patient = Patient.objects.select_for_update().get(id=task.patient_id)
    actor = Principal.objects.select_for_update().get(id=task.actor_id)
    task = Task.objects.select_for_update().select_related('patient', 'actor').get(
        id=task.id, patient=patient, actor=actor)
    refs = task.input_refs
    if refs['environment'] != settings.SJIA_ENVIRONMENT or refs['auth_epoch'] != str(task.actor.auth_epoch):
        raise ValueError('VOICE_AUTHORIZATION_CHANGED')
    voice_input = VoiceInput.objects.select_for_update().select_related('patient', 'actor').get(
        id=refs['voice_input_id'], patient=task.patient, actor=task.actor, task=task)
    if (voice_input.status not in ('running', 'ready') or voice_input.transcript_sha256 != refs['transcript_sha256']
            or voice_input.input_mode != refs['input_mode']):
        raise ValueError('VOICE_INPUT_CHANGED')
    _authorize(task)
    original = lock_original(refs['original_file_id'])
    if original.checksum != refs['original_checksum'] or checked_bytes(original).decode('utf-8', errors='strict') != voice_input.transcript:
        raise ValueError('VOICE_ORIGINAL_CHANGED')
    use_auth = lock_use_authorization(refs['authorization_id'], original, 'external_recognition', runtime_environment())
    if use_auth.revoked_at is not None:
        raise ValueError('VOICE_AUTHORIZATION_REVOKED')
    provider = ConfigVersion.objects.select_for_update().get(id=refs['provider_config_id'], kind='provider',
        schema_version='provider-1', status='enabled')
    runtime = ConfigVersion.objects.select_for_update().get(id=refs['runtime_config_id'], kind='provider',
        schema_version='backend-runtime-1', status='enabled')
    if (str(gate.runtime_config_id) != str(runtime.id) or runtime.checksum != refs['runtime_checksum']
            or configuration_qualification_reason(runtime, settings.SJIA_ENVIRONMENT) is not None
            or provider.checksum != refs['provider_checksum']
            or provider.payload['field_config_id'] != refs['field_config_id']
            or configuration_qualification_reason(provider, settings.SJIA_ENVIRONMENT) is not None):
        raise ValueError('VOICE_CONFIGURATION_CHANGED')
    provider_entries = [entry for entry in runtime.payload['providers'] if entry['kind'] == 'voice'
        and entry['enabled'] and entry['config_id'] == str(provider.id)]
    task_entries = [entry for entry in runtime.payload['tasks'] if entry['task_type'] == 'ocr']
    if (len(provider_entries) != 1 or provider_entries[0] != refs['provider_entry']
            or provider_entries[0]['config_id'] != str(provider.id)
            or len(task_entries) != 1 or task_entries[0] != refs['task_config']):
        raise ValueError('VOICE_RUNTIME_POLICY_CHANGED')
    copy = PurposeCopy.objects.select_for_update().get(id=refs['purpose_copy']['copy_id'],
        original_file_id=original.id, patient_id=task.patient_id)
    locked, copy_original, derived, *_ = lock_copy_context(copy.id, task.actor, int(refs['auth_epoch']), service=False)
    if (str(locked.version) != refs['purpose_copy']['copy_version'] or derived.checksum != refs['purpose_copy']['checksum']
            or locked.processing_evidence.get('target') != provider.payload['endpoint'].rstrip('/')
            or derived.media_type != 'text/plain' or current_review(locked, copy_original, derived) is None):
        raise ValueError('VOICE_PURPOSE_COPY_CHANGED')
    _verify_confirmed_text_copy(locked, copy_original, derived,
        provider.payload['endpoint'].rstrip('/'), voice_input.transcript)
    current_ref = {'copy_id': str(locked.id), 'copy_version': str(locked.version),
        'checksum': derived.checksum, 'target': provider.payload['endpoint'].rstrip('/')}
    if current_ref != refs['purpose_copy']:
        raise ValueError('VOICE_PURPOSE_COPY_SELECTION_CHANGED')
    return task, voice_input, original, use_auth, provider, runtime, task_entries[0]


def _usage_dict(result):
    usage = result['usage']
    if usage is None:
        return None
    if hasattr(usage, 'model_dump'):
        return usage.model_dump(mode='json')
    return usage


def _provider_timeout_seconds(task, execution_until):
    now = timezone.now()
    deadlines = [task.deadline_at, execution_until]
    if task.lease_until is None:
        raise ValueError('VOICE_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
    if task.lease_until <= now:
        raise ValueError('VOICE_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
    remaining = (min(deadlines) - now).total_seconds()
    if remaining <= 0:
        raise ValueError('VOICE_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
    return min(remaining, settings.ARK_REQUEST_TIMEOUT_SECONDS)


def _persist_provider_receipt(*, task, attempt, voice_input, original, result, provider, elapsed_ms):
    from archives.page_execution import actual_cost

    raw = result['raw_response']
    raw = raw.encode('utf-8') if isinstance(raw, str) else raw
    checksum = digest_bytes(raw)
    response_id = uuid4()
    key = build_object_key(task.patient_id, response_id, 'voice_response')
    object_store.put_immutable(key, raw)
    usage = _usage_dict(result)
    usage_evidence = {'phase': 'provider_finished', 'provider_called': True,
        'provider_request_id': result['provider_request_id'], 'model': result['model'], 'usage': usage,
        'provider_config_id': str(provider.id), 'provider_checksum': provider.checksum,
        'pricing_evidence_ref': provider.payload['pricing_evidence_ref'],
        'input_per_million': provider.payload['input_per_million'],
        'output_per_million': provider.payload['output_per_million'], 'currency': provider.payload['currency'],
        'actual_cost': None, 'cost_status': 'not_measured',
        'provider_duration_ms': elapsed_ms, 'raw_response_file_id': str(response_id),
        'raw_response_sha256': checksum, 'raw_response_size_bytes': len(raw)}
    with transaction.atomic():
        response = FileObject.objects.create(id=response_id, patient=task.patient,
            file_group_id=original.file_group_id, object_key=key, object_version=checksum,
            checksum=checksum, size_bytes=len(raw), media_type='application/json',
            purpose=FileObject.Purpose.OCR_RESPONSE, status=FileObject.Status.AVAILABLE,
            retention_policy=original.retention_policy, created_at=timezone.now())
        attempt = TaskAttempt.objects.select_for_update().get(id=attempt.id)
        attempt.provider_request_id = result['provider_request_id']
        attempt.usage_evidence = {**(attempt.usage_evidence or {}), **usage_evidence}
        attempt.save(update_fields=['provider_request_id', 'usage_evidence'])
        voice_input = VoiceInput.objects.select_for_update().get(id=voice_input.id, task=task)
        voice_input.raw_response_file = response
        voice_input.save(update_fields=['raw_response_file'])
    cost = actual_cost({'usage': usage}, provider)
    if cost is not None:
        usage_evidence['actual_cost'] = str(cost)
        usage_evidence['cost_status'] = 'measured'
        with transaction.atomic():
            attempt = TaskAttempt.objects.select_for_update().get(id=attempt.id)
            attempt.usage_evidence = {**(attempt.usage_evidence or {}),
                'actual_cost': str(cost), 'cost_status': 'measured'}
            attempt.save(update_fields=['usage_evidence'])
    return response, usage_evidence


def execute_voice_input(event, owner):
    with transaction.atomic():
        gate = EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
        if gate is None or not gate.enabled:
            raise ValueError('VOICE_RUNTIME_CONFIGURATION_REQUIRED')
        task = Task.objects.select_for_update().get(id=event.task_id, task_type='ocr', schema_version='voice-input-1')
        if ConsumerReceipt.objects.filter(consumer='voice_input', event=event).exists():
            return
        if task.status in ('awaiting_confirmation', 'completed', 'failed', 'uncertain', 'awaiting_resolution', 'cancelled', 'superseded'):
            record_receipt('voice_input', event, task.id)
            return
        task_config = task.input_refs['task_config']
        if not acquire_lease(task.id, owner, min(task.deadline_at,
                timezone.now() + timedelta(seconds=task_config['lease_seconds']))):
            return
        task.refresh_from_db()
        voice_input = VoiceInput.objects.select_for_update().get(id=task.input_refs['voice_input_id'], task=task)
        if voice_input.status in ('cancelled', 'revoked', 'uncertain'):
            task.status = 'cancelled'
            task.status_version += 1
            task.lease_until = timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            record_receipt('voice_input', event, task.id)
            return
        voice_input.status = 'running'
        voice_input.status_version += 1
        voice_input.save(update_fields=['status', 'status_version'])
    task, voice_input, original, use_auth, provider, runtime, task_config = _execution_scope(task)
    with model_copy_input(original_file_id=original.id, actor=task.actor,
            auth_epoch=int(task.input_refs['auth_epoch']), reference=task.input_refs['purpose_copy']) as (data, media_type):
        require_model_text(data, media_type, task.input_refs['purpose_copy']['target'])
        if data.decode('utf-8', errors='strict') != voice_input.transcript:
            raise ValueError('VOICE_MODEL_TEXT_MISMATCH')

        with transaction.atomic():
            task = Task.objects.select_for_update().get(id=task.id)
            if (task.status != 'running' or task.lease_until is None or task.lease_until <= timezone.now()):
                raise ValueError('VOICE_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
            attempt = create_attempt(task, task.lease_generation)
            attempt.usage_evidence = {'phase': 'pre_provider', 'provider_called': False,
                'provider_config_id': str(provider.id), 'provider_checksum': provider.checksum}
            attempt.save(update_fields=['usage_evidence'])

        execution_until = min(task.deadline_at,
            attempt.started_at + timedelta(seconds=task_config['execution_seconds']))
        if execution_until <= timezone.now():
            raise ValueError('VOICE_EXECUTION_DEADLINE_EXCEEDED')

        def mark_provider_started():
            with transaction.atomic():
                current_task = Task.objects.select_for_update().get(id=task.id)
                current_attempt = TaskAttempt.objects.select_for_update().get(id=attempt.id)
                if (current_task.status != 'running' or current_task.lease_generation != current_attempt.lease_generation
                        or current_task.lease_owner != owner or current_task.lease_until is None
                        or current_task.lease_until <= timezone.now() or current_task.deadline_at <= timezone.now()
                        or execution_until <= timezone.now()):
                    raise ValueError('VOICE_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
                current_attempt.usage_evidence = {**(current_attempt.usage_evidence or {}),
                    'phase': 'provider_started', 'provider_called': True}
                current_attempt.save(update_fields=['usage_evidence'])

        with lease_heartbeat(task.id, attempt.lease_generation, task_config, execution_until) as stop_heartbeat:
            started = time.perf_counter()
            result = voice_completion_with_evidence(voice_input.transcript, provider,
                before_provider_call=mark_provider_started,
                timeout_seconds=_provider_timeout_seconds(task, execution_until))
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            stop_heartbeat()
    response, usage_evidence = _persist_provider_receipt(task=task, attempt=attempt,
        voice_input=voice_input, original=original, result=result, provider=provider, elapsed_ms=elapsed_ms)
    candidates = checked_voice_candidates(result)
    with transaction.atomic():
        task = Task.objects.select_for_update().get(id=task.id)
        attempt = TaskAttempt.objects.select_for_update().get(id=attempt.id)
        voice_input = VoiceInput.objects.select_for_update().get(id=voice_input.id, task=task)
        original = lock_original(original.id)
        if (task.status != 'running' or task.lease_generation != attempt.lease_generation
                or task.lease_owner != owner or task.lease_until is None or task.lease_until <= timezone.now()
                or task.deadline_at <= timezone.now() or execution_until <= timezone.now()
                or voice_input.status in ('cancelled', 'revoked', 'uncertain')):
            raise ValueError('VOICE_TASK_EXECUTION_AUTHORITY_UNAVAILABLE')
        current_authorization = DataUseAuthorization.objects.select_for_update().get(
            id=task.input_refs['authorization_id'], patient=task.patient)
        if (current_authorization.revoked_at is not None or current_authorization.approved_at > timezone.now()
                or current_authorization.valid_until is not None and current_authorization.valid_until <= timezone.now()):
            voice_input.status = 'revoked'
            voice_input.status_version += 1
            voice_input.save(update_fields=['status', 'status_version'])
            task.status = 'failed'
            task.status_version += 1
            task.lease_until = timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            finish_attempt(attempt, TaskAttempt.Status.COMPLETED, error_code='VOICE_AUTHORIZATION_REVOKED',
                usage_evidence=usage_evidence)
            record_receipt('voice_input', event, task.id)
            return
        use_auth = lock_use_authorization(task.input_refs['authorization_id'], original,
            'external_recognition', settings.SJIA_ENVIRONMENT)
        _authorize(task)
        copy = PurposeCopy.objects.select_for_update().get(id=task.input_refs['purpose_copy']['copy_id'],
            patient_id=task.patient_id, original_file_id=original.id)
        derived = FileObject.objects.select_for_update().get(id=copy.derived_file_id, patient_id=task.patient_id)
        if current_review(copy, original, derived) is None:
            voice_input.status = 'revoked'
            voice_input.status_version += 1
            voice_input.save(update_fields=['status', 'status_version'])
            task.status = 'failed'
            task.status_version += 1
            task.lease_until = timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            finish_attempt(attempt, TaskAttempt.Status.COMPLETED, error_code='VOICE_PURPOSE_REVIEW_REVOKED',
                usage_evidence=usage_evidence)
            record_receipt('voice_input', event, task.id)
            return
        voice_input.candidates = candidates
        voice_input.status = 'awaiting_confirmation'
        voice_input.status_version += 1
        voice_input.save(update_fields=['candidates', 'status', 'status_version'])
        task.status = 'awaiting_confirmation'
        task.status_version += 1
        task.lease_until = timezone.now()
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        finish_attempt(attempt, TaskAttempt.Status.COMPLETED, usage_evidence=usage_evidence)
        record_receipt('voice_input', event, voice_input.id)


@transaction.atomic
def record_voice_failure(event, owner, reason):
    if (event.event_type != 'voice_input.requested' or event.schema_version != 'voice-input-1'
            or event.task_id is None or event.resource_refs.keys() != {'task_id', 'voice_input_id'}
            or event.resource_refs['task_id'] != str(event.task_id)):
        raise ValueError('VOICE_EVENT_SCOPE_MISMATCH')
    EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
    seed = Task.objects.get(id=event.task_id, task_type='ocr', schema_version='voice-input-1')
    if (seed.patient_id is None or str(seed.id) != event.resource_refs['task_id']
            or str(seed.input_refs.get('voice_input_id')) != event.resource_refs['voice_input_id']
            or str(event.aggregate_id) != event.resource_refs['voice_input_id']
            or event.correlation_id != seed.id):
        raise ValueError('VOICE_EVENT_SCOPE_MISMATCH')
    patient = Patient.objects.select_for_update().get(id=seed.patient_id)
    actor = Principal.objects.select_for_update().get(id=seed.actor_id)
    voice_input = VoiceInput.objects.select_for_update().get(id=event.resource_refs['voice_input_id'],
        patient=patient, actor=actor, task=seed)
    task = Task.objects.select_for_update().get(id=seed.id, patient=patient, actor=actor)
    if ConsumerReceipt.objects.filter(consumer='voice_input', event=event).exists():
        return
    now = timezone.now()
    if task.status not in ('accepted', 'queued', 'running', 'cancel_requested'):
        return
    if task.status == 'running' and task.lease_owner != owner and task.lease_until is not None and task.lease_until > now:
        return
    attempt = TaskAttempt.objects.select_for_update().filter(task=task,
        lease_generation=task.lease_generation).order_by('-attempt_number').first()
    usage = attempt.usage_evidence if attempt and isinstance(attempt.usage_evidence, dict) else {}
    provider_started = usage.get('phase') in ('provider_started', 'provider_finished') or usage.get('provider_called') is True
    provider_response_known = bool(attempt and attempt.provider_request_id
        and usage.get('raw_response_file_id'))
    if task.status == 'cancel_requested' and not provider_started:
        task_state, voice_state, attempt_state = 'cancelled', 'cancelled', TaskAttempt.Status.CANCELLED
    elif provider_started and not provider_response_known:
        task_state, voice_state, attempt_state = 'uncertain', 'uncertain', TaskAttempt.Status.UNCERTAIN
    else:
        task_state, voice_state, attempt_state = 'failed', 'failed', TaskAttempt.Status.FAILED
    task.status = task_state
    task.status_version += 1
    task.lease_until = now
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    if voice_input.status not in ('saved', 'revoked'):
        voice_input.status = voice_state
        voice_input.status_version += 1
        if voice_state == 'cancelled' and voice_input.cancelled_at is None:
            voice_input.cancelled_at = now
            voice_input.save(update_fields=['status', 'status_version', 'cancelled_at'])
        else:
            voice_input.save(update_fields=['status', 'status_version'])
    evidence = {**usage, 'failure_owner': owner, 'failure_reason': reason,
        'provider_called': provider_started, 'provider_result_known': provider_response_known}
    if attempt is None:
        TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
            lease_generation=task.lease_generation, started_at=now, finished_at=now,
            status=attempt_state, error_code=reason, usage_evidence=evidence)
    elif attempt.status == TaskAttempt.Status.RUNNING:
        finish_attempt(attempt, attempt_state, error_code=reason, usage_evidence=evidence)
    record_receipt('voice_input', event, voice_input.id)
