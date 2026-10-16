"""Legacy synchronous API shape with the same durable provider safeguards as jobs."""
from datetime import timedelta
from uuid import uuid4

from django.db import transaction
from django.utils import timezone

from authorization.policy import evaluate
from django.conf import settings
from family_content.models import EnvironmentGate
from files.model_input import model_copy_input, model_copy_ref
from files.models import FileGroup, FileObject
from files.purpose import checked_bytes
from recognition.ark import recognize_image_with_evidence
from recognition.invocation import InvocationRejected, checked_candidates, provider_timeout_seconds, contract_snapshot, failure_state
from recognition.invocation_runtime import (admit_invocation, validate_invocation, provider_marker,
    persist_call_receipt, record_invocation_failure, require_authority)
from recognition.schemas import ReportCandidates, TherapyCandidates, ArkCompletion
from recognition.report_times import report_candidate_model
from tasks.lease import lease_heartbeat
from tasks.models import Task, TaskAttempt
from tasks.service import register_task, acquire_lease, create_attempt, finish_attempt


def _authorize(actor, patient, auth_epoch):
    actor.refresh_from_db()
    patient.refresh_from_db()
    for action, purpose in (('write_record', 'family_observation'), ('read_file', 'external_recognition')):
        evaluate(principal=actor, patient=patient, action=action, purpose=purpose,
            environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=auth_epoch)


def existing_sync_invocation(*, original, actor, group, kind):
    """Caller holds EnvironmentGate: concurrent HTTP retries cannot reserve twice."""
    prior = list(Task.objects.filter(actor=actor, patient_id=original.patient_id,
        schema_version='recognition-sync-1', input_refs__original_file_id=str(original.id),
        input_refs__original_checksum=original.checksum, input_refs__file_group_version=str(group.version),
        input_refs__invocation_contract__kind=kind).order_by('-created_at', '-id'))
    for task in prior:
        if task.status in ('accepted', 'queued', 'running', 'cancel_requested', 'uncertain', 'awaiting_resolution'):
            raise InvocationRejected('MODEL_PRIOR_INVOCATION_UNRESOLVED')
        if any(failure_state(attempt.usage_evidence or {}) == 'uncertain'
                for attempt in TaskAttempt.objects.filter(task=task)):
            raise InvocationRejected('MODEL_PRIOR_INVOCATION_UNRESOLVED')
    contracts = [contract_snapshot(kind)]
    if kind == 'report':
        from types import SimpleNamespace
        contracts.append(contract_snapshot(kind, SimpleNamespace(payload={'report_schema_version':'report-recognition-3'})))
    return next((task for task in prior if task.status == 'completed'
        and task.input_refs.get('invocation_contract') in contracts), None)


def replay_candidates(*, task, original, actor, auth_epoch, kind):
    try:
        provider = validate_invocation(task, kind)
    except InvocationRejected as exc:
        if exc.reason_code in ('MODEL_INVOCATION_CONTRACT_CHANGED', 'MODEL_RUNTIME_CONFIGURATION_CHANGED',
                'MODEL_PROVIDER_SELECTION_REQUIRED', 'MODEL_FIELD_CONFIGURATION_BINDING_CHANGED',
                'MODEL_FIELD_CONFIGURATION_UNQUALIFIED', 'MODEL_FIELD_CONFIGURATION_BINDING_REQUIRED'):
            # Known completed receipt under an old approved configuration is not
            # an eligible cache entry. Fresh admission still validates all current
            # configuration, authorization, purpose and budget requirements.
            return None
        raise
    reference = model_copy_ref(original_file_id=original.id, actor=actor, auth_epoch=auth_epoch,
        target=provider.payload['endpoint'].rstrip('/'))
    if reference != task.input_refs['purpose_copy']:
        # A new reviewed purpose copy is a genuinely different submitted input.
        return None
    attempt = TaskAttempt.objects.get(task=task, status='completed')
    calls = (attempt.usage_evidence or {}).get('provider_calls', [])
    if len(calls) != 1 or not calls[0].get('raw_response_file_id'):
        raise InvocationRejected('MODEL_PRIOR_RECEIPT_UNAVAILABLE')
    response = FileObject.objects.get(id=calls[0]['raw_response_file_id'], patient_id=original.patient_id,
        file_group_id=original.file_group_id, purpose='ocr_response', status='available')
    if response.checksum != calls[0]['raw_response_sha256']:
        raise InvocationRejected('MODEL_PRIOR_RECEIPT_CHANGED')
    body = ArkCompletion.model_validate_json(checked_bytes(response))
    return checked_candidates({'completion': body, 'model': body.model}, provider,
        report_candidate_model(provider) if kind == 'report' else TherapyCandidates)


def synchronous_image_candidates(*, file_object, actor, auth_epoch, kind):
    if kind not in ('report', 'therapy'):
        raise InvocationRejected('MODEL_CONTRACT_UNREGISTERED')
    owner = str(uuid4())
    with transaction.atomic():
        EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
        _authorize(actor, file_object.patient, auth_epoch)
        original = FileObject.objects.get(id=file_object.id, status='available')
        group = FileGroup.objects.get(id=original.file_group_id, patient_id=original.patient_id, status='available')
        prior = existing_sync_invocation(original=original, actor=actor, group=group, kind=kind)
        if prior is not None:
            cached = replay_candidates(task=prior, original=original, actor=actor, auth_epoch=auth_epoch, kind=kind)
            if cached is not None:
                return cached
        provider, refs = admit_invocation(kind=kind, field_kind=kind)
        reference = model_copy_ref(original_file_id=original.id, actor=actor, auth_epoch=auth_epoch,
            target=provider.payload['endpoint'].rstrip('/'))
        policy = refs['task_config']
        task = register_task(actor=actor, patient=original.patient, task_type='ocr',
            schema_version='recognition-sync-1', input_refs={**refs, 'auth_epoch': auth_epoch,
                'original_file_id': str(original.id), 'original_checksum': original.checksum,
                'file_group_id': str(group.id), 'file_group_version': str(group.version),
                'purpose_copy': reference}, deadline_seconds=policy['execution_seconds'])
        if not acquire_lease(task.id, owner, timezone.now() + timedelta(seconds=policy['lease_seconds'])):
            raise InvocationRejected('MODEL_PROVIDER_CALL_AUTHORITY_UNAVAILABLE')
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation)
        attempt.usage_evidence = {'phase': 'pre_provider', 'provider_called': False, 'provider_calls': []}
        attempt.save(update_fields=['usage_evidence'])
    execution_until = min(task.deadline_at, attempt.started_at + timedelta(seconds=policy['execution_seconds']))
    try:
        validate_invocation(task, kind)
        with model_copy_input(original_file_id=original.id, actor=actor,
                auth_epoch=auth_epoch, reference=reference) as (data, media_type):
            with lease_heartbeat(task.id, attempt.lease_generation, policy, execution_until):
                result = recognize_image_with_evidence(data, media_type, kind=kind, provider=provider,
                    before_provider_call=provider_marker(task, attempt, owner, execution_until, call_index=0),
                    timeout_seconds=provider_timeout_seconds(task, execution_until, refs['provider_entry']))
                persist_call_receipt(task=task, attempt=attempt, original=original,
                    result=result, provider=provider, call_index=0)
        candidates = checked_candidates(result, provider, report_candidate_model(provider) if kind == 'report' else TherapyCandidates)
        with transaction.atomic():
            EnvironmentGate.objects.select_for_update().filter(environment=settings.SJIA_ENVIRONMENT).first()
            _authorize(actor, original.patient, auth_epoch)
            validate_invocation(task, kind)
            current_ref = model_copy_ref(original_file_id=original.id, actor=actor,
                auth_epoch=auth_epoch, target=provider.payload['endpoint'].rstrip('/'))
            if current_ref != reference:
                raise InvocationRejected('PURPOSE_INPUT_CHANGED')
            current_group = FileGroup.objects.get(id=group.id, patient_id=original.patient_id)
            if current_group.status != 'available' or current_group.version != group.version:
                raise InvocationRejected('FILE_INPUT_CHANGED')
            task = Task.objects.select_for_update().get(id=task.id)
            attempt = TaskAttempt.objects.select_for_update().get(id=attempt.id)
            require_authority(task, attempt, owner, execution_until)
            task.status, task.status_version, task.lease_until = 'completed', task.status_version + 1, timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            finish_attempt(attempt, 'completed', usage_evidence=attempt.usage_evidence)
        return candidates
    except Exception as exc:
        record_invocation_failure(task.id, owner, getattr(exc, 'reason_code', 'MODEL_INVOCATION_FAILED'))
        raise
