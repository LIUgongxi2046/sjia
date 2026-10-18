from datetime import date, timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.urls import resolve, reverse
from django.utils import timezone

from authorization.policy import Denied, evaluate, check_principal_session
from identity.models import Patient, Principal
from requests.models import WriteIntent
from requests.service import prepare_intent, compute_digest, commit_intent
from summaries.inputs import SummaryInput, SummaryIntentInput, SummaryWriteHeaders
from summaries.models import Summary
from summaries.service import SummaryRejected, generate_summary
from tasks.models import Task, TaskAttempt
from family_content.gate_locks import read_environment_gate_shared


def summary_permission(actor, patient, auth_epoch, audience, action='create_summary'):
    if audience != 'family':
        raise SummaryRejected('FAMILY_AUDIENCE_REQUIRED')
    decision = evaluate(principal=actor, patient=patient, action=action, purpose='family_observation',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=auth_epoch)
    if action == 'read_summary' and not Patient.objects.filter(id=patient.id, status='active').exists():
        raise Denied('SUMMARY_PATIENT_SCOPE_DENIED')
    if decision.grant.record_scope != 'all':
        raise SummaryRejected('SUMMARY_SCOPE_NOT_AVAILABLE')
    return decision


def summary_snapshot_permission(request, actor, patient, audience):
    from records.snapshot import authorize_record_snapshot
    if audience != 'family':
        raise SummaryRejected('FAMILY_AUDIENCE_REQUIRED')
    if patient.status != 'active':
        raise Denied('SUMMARY_PATIENT_SCOPE_DENIED')
    grant = authorize_record_snapshot(request, actor, patient, 'read_summary')
    if grant.record_scope != 'all':
        raise SummaryRejected('SUMMARY_SCOPE_NOT_AVAILABLE')
    return grant


def summary_target(target):
    route = resolve(target)
    if route.url_name != 'summary_create' or target != reverse('summary_create', kwargs=route.kwargs):
        raise SummaryRejected('SUMMARY_TARGET_INVALID')
    return Patient.objects.get(id=route.kwargs['patient_id'])


def summary_task_view(task):
    if task.task_type != 'summary':
        raise SummaryRejected('SUMMARY_TASK_INVALID')
    result = Summary.objects.filter(task_id=task.id, patient_id=task.patient_id, requested_by_id=task.actor_id)
    summary = result.get() if task.status == 'completed' else None
    return {'task_id': str(task.id), 'task_type': task.task_type,
        'request_id': str(task.request_row.request_id) if task.request_row_id else None,
        'accepted_at': task.created_at.isoformat(), 'status': task.status, 'status_version': str(task.status_version),
        'poll_after_ms': 1500 if task.status in ('accepted', 'queued', 'running') else None,
        'allowed_actions': ['task_cancel'] if task.status in ('accepted', 'queued', 'running', 'awaiting_confirmation') else [],
        'result_type': 'summary' if summary else None, 'result_ref': str(summary.id) if summary else None,
        'error_code': TaskAttempt.objects.filter(task=task).order_by('-attempt_number').values_list('error_code', flat=True).first()}


def summary_receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
        or saved.resource_type not in ('summary', 'task') or saved.completed_at is None or saved.resource_id is None
        or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
        or saved.request_id != intent.request_id or saved.target_key != intent.target_key
        or saved.action != 'summary_create' or saved.payload_sha256 != intent.payload_sha256):
        raise SummaryRejected('SUMMARY_RECEIPT_INVALID')
    if saved.resource_type == 'summary':
        summary = Summary.objects.get(id=saved.resource_id, patient_id=intent.patient_id, requested_by_id=intent.actor_id)
        Task.objects.get(id=summary.task_id, request_row=saved, actor_id=intent.actor_id, patient_id=intent.patient_id)
    else:
        Task.objects.get(id=saved.resource_id, task_type='summary', schema_version='summary-task-2',
            request_row=saved, actor_id=intent.actor_id, patient_id=intent.patient_id)
    return {'resource_type': saved.resource_type, 'resource_id': str(saved.resource_id), 'version': '1',
        'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id)}


def summary_request_view(intent):
    if intent.operation_id != 'summary_create' or intent.patient_id is None:
        raise SummaryRejected('SUMMARY_REQUEST_INVALID')
    patient = summary_target(intent.target_key)
    if patient.id != intent.patient_id:
        raise SummaryRejected('SUMMARY_TARGET_INVALID')
    payload = None
    if intent.recovery_payload is not None:
        payload = SummaryInput.model_validate(intent.recovery_payload).model_dump()
        if intent.payload_sha256 != compute_digest('summary_create', intent.target_key, '1.0.0', payload):
            raise SummaryRejected('SUMMARY_REQUEST_PAYLOAD_INVALID')
    changed = intent.authorization_context != {'audience': 'family', 'auth_epoch': str(intent.actor.auth_epoch)}
    receipt = summary_receipt(intent) if intent.status == 'committed' else None
    task = None
    if receipt is not None:
        task_id = receipt['resource_id'] if receipt['resource_type'] == 'task' else Summary.objects.get(
            id=receipt['resource_id'], patient_id=intent.patient_id).task_id
        task = summary_task_view(Task.objects.get(id=task_id, actor_id=intent.actor_id, patient_id=intent.patient_id))
    return {'patient_id': str(intent.patient_id), 'intent_id': str(intent.id), 'request_id': str(intent.request_id),
        'operation_id': intent.operation_id, 'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': payload is not None and intent.write_request_id is None and (intent.status == 'failed'
            or (intent.status == 'prepared' and (intent.expires_at <= timezone.now() or changed))),
        'receipt': receipt, 'task': task,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'summary_input': payload}


@transaction.atomic
def prepare_summary_intent(*, actor, auth_epoch, audience, data, retention_policy):
    from family_content.models import EnvironmentGate
    read_environment_gate_shared(settings.SJIA_ENVIRONMENT)
    actor = Principal.objects.select_for_update(no_key=True).get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    data = SummaryIntentInput.model_validate(data)
    patient = summary_target(data.target_key)
    summary_permission(actor, patient, auth_epoch, audience)
    payload = SummaryInput.model_validate(data.payload).model_dump()
    return prepare_intent(actor=actor, patient=patient, operation_id='summary_create', target_key=data.target_key,
        request_id=UUID(data.request_id), payload=payload, schema_version='1.0.0', retention_policy=retention_policy,
        authorization_context={'audience': audience, 'auth_epoch': str(auth_epoch)}, recovery_payload=payload)


@transaction.atomic
def submit_summary(*, actor, patient, auth_epoch, audience, target, data, headers):
    from summaries.jobs import SCHEMA, admission_context, lock_summary_scope
    from tasks.outbox import emit_event
    from configurations.service import compute_checksum

    _, actor, patient = lock_summary_scope(actor.id, patient.id, settings.SJIA_ENVIRONMENT)
    check_principal_session(actor, auth_epoch)
    decision = summary_permission(actor, patient, auth_epoch, audience)
    if summary_target(target).id != patient.id:
        raise SummaryRejected('SUMMARY_TARGET_INVALID')
    data = SummaryInput.model_validate(data).model_dump()
    headers = SummaryWriteHeaders.model_validate(headers)
    intent = WriteIntent.objects.select_for_update().filter(id=headers.intent_id,
        actor=actor, patient=patient, operation_id='summary_create', target_key=target).first()
    if intent is None:
        raise SummaryRejected('SUMMARY_INTENT_NOT_FOUND')
    if (str(intent.request_id) != headers.request_id or str(intent.generation) != headers.generation
        or intent.recovery_payload != data or intent.payload_sha256 != compute_digest('summary_create', target, '1.0.0', data)):
        raise SummaryRejected('SUMMARY_INTENT_MISMATCH')
    if intent.authorization_context != {'audience': audience, 'auth_epoch': str(auth_epoch)}:
        raise SummaryRejected('SUMMARY_AUTHORIZATION_CHANGED')
    if intent.status == 'committed':
        receipt = summary_receipt(intent)
        task_id = receipt['resource_id'] if receipt['resource_type'] == 'task' else Summary.objects.get(
            id=receipt['resource_id'], patient=patient, requested_by=actor).task_id
        return summary_task_view(Task.objects.get(id=task_id))
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise SummaryRejected('SUMMARY_INTENT_EXPIRED')
    _, _, context = admission_context(actor, patient, auth_epoch, audience, settings.SJIA_ENVIRONMENT)
    inputs = {'scope': data, 'context': context}
    now = timezone.now()
    task = Task.objects.create(actor=actor, patient=patient, task_type='summary', schema_version=SCHEMA,
        input_refs=inputs, input_sha256=compute_checksum(inputs), status='accepted', status_version=1,
        created_at=now, deadline_at=now + timedelta(seconds=context['task_policy']['queue_wait_seconds']
            + context['task_policy']['execution_seconds']))
    saved = commit_intent(intent=intent, action='summary_create', resource_type='task', resource_id=task.id)
    task.request_row = saved
    task.save(update_fields=['request_row'])
    emit_event(event_type='summary.requested', schema_version=SCHEMA, aggregate_id=task.id,
        aggregate_version=1, correlation_id=intent.request_id, resource_refs={'task_id': str(task.id)}, task=task)
    summary_receipt(intent)
    return summary_task_view(task)


@transaction.atomic
def retry_summary_intent(*, actor, auth_epoch, audience, intent_id, expected_generation):
    from family_content.models import EnvironmentGate
    read_environment_gate_shared(settings.SJIA_ENVIRONMENT)
    actor = Principal.objects.select_for_update(no_key=True).get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=actor, operation_id='summary_create').first()
    if intent is None:
        raise SummaryRejected('SUMMARY_INTENT_NOT_FOUND')
    summary_permission(actor, intent.patient, auth_epoch, audience)
    if str(intent.generation) != expected_generation or not summary_request_view(intent)['may_retry']:
        raise SummaryRejected('SUMMARY_RETRY_NOT_AVAILABLE')
    intent.generation += 1
    if intent.generation > 9_223_372_036_854_775_807:
        raise SummaryRejected('GENERATION_RANGE_EXCEEDED')
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=300)
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.authorization_context = {'audience': audience, 'auth_epoch': str(auth_epoch)}
    intent.save(update_fields=['generation', 'prepared_at', 'expires_at', 'status', 'error_code', 'completed_at', 'authorization_context'])
    return intent
