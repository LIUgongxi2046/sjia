"""Bounded support reconciliation of local metadata; never calls a provider."""
from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from authorization.policy import evaluate
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from relations.inputs import ApplicationIntentInput, ApplicationWriteHeaders
from requests.models import WriteIntent
from requests.service import commit_intent, compute_digest, prepare_intent
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tasks.outbox import emit_event, record_receipt
from tasks.service import TaskRejected, acquire_lease, create_attempt

SCHEMA = 'reconcile-task-1'
LIMIT = 100


class ReconcileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_status_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19)]
    expected_lease_generation: Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)$', max_length=19)]


class ReconcileIntentInput(ApplicationIntentInput):
    operation_id: Literal['task_reconcile']


class ReconcileDeferred(Exception):
    def __init__(self, seconds, max_retries):
        self.seconds, self.max_retries = seconds, max_retries
        super().__init__('RECONCILE_CONCURRENCY_WAIT')


def target_task(target):
    route = resolve(target)
    if route.url_name != 'task_reconcile' or reverse('task_reconcile', kwargs=route.kwargs) != target:
        raise TaskRejected('RECONCILE_TARGET_INVALID')
    task = Task.objects.get(id=route.kwargs['task_id'])
    if task.task_type == 'reconcile' or task.patient_id is None:
        raise TaskRejected('RECONCILE_TARGET_NOT_SUPPORTED')
    return task


def lock_scope(actor_id, patient_id):
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    actor = Principal.objects.select_for_update().get(id=actor_id)
    patient = Patient.objects.select_for_update().get(id=patient_id)
    return gate, actor, patient


def permission(actor, patient, epoch, audience):
    if audience != 'support':
        raise TaskRejected('SUPPORT_AUDIENCE_REQUIRED')
    service = evaluate(principal=actor, patient=None, action='reconcile_task', purpose='clinical',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=epoch)
    case = evaluate(principal=actor, patient=patient, action='read_case', purpose='clinical',
        environment=settings.SJIA_ENVIRONMENT, session_auth_epoch=epoch, whole_resource=True)
    if case.grant.record_scope != 'all' or patient.status != 'active':
        raise TaskRejected('RECONCILE_CASE_SCOPE_DENIED')
    return service, case


def context(actor, patient, epoch, audience):
    gate, actor, patient = lock_scope(actor.id, patient.id)
    service, case = permission(actor, patient, epoch, audience)
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None or configuration_qualification_reason(runtime, settings.SJIA_ENVIRONMENT) is not None:
        raise TaskRejected('RECONCILE_RUNTIME_UNAVAILABLE')
    policies = [p for p in runtime.payload['tasks'] if p['task_type'] == 'reconcile']
    if len(policies) != 1:
        raise TaskRejected('RECONCILE_POLICY_UNAVAILABLE')
    return {'environment': settings.SJIA_ENVIRONMENT, 'actor_id': str(actor.id), 'patient_id': str(patient.id),
        'auth_epoch': str(epoch), 'audience': audience, 'runtime_id': str(runtime.id),
        'runtime_checksum': runtime.checksum, 'task_policy': policies[0],
        'service_grant_id': str(service.service_grant.id), 'case_grant_id': str(case.grant.id)}


def snapshot(task):
    return {'task_id': str(task.id), 'patient_id': str(task.patient_id), 'actor_id': str(task.actor_id),
        'task_type': task.task_type, 'schema_version': task.schema_version, 'status': task.status,
        'status_version': str(task.status_version), 'lease_generation': str(task.lease_generation),
        'input_sha256': task.input_sha256, 'request_row_id': str(task.request_row_id) if task.request_row_id else None}


@transaction.atomic
def prepare_reconcile_intent(*, actor, auth_epoch, audience, data, retention_policy):
    data = ReconcileIntentInput.model_validate(data)
    target = target_task(data.target_key)
    _, actor, patient = lock_scope(actor.id, target.patient_id)
    permission(actor, patient, auth_epoch, audience)
    payload = ReconcileInput.model_validate(data.payload).model_dump()
    return prepare_intent(actor=actor, patient=patient, operation_id='task_reconcile', target_key=data.target_key,
        request_id=UUID(data.request_id), payload=payload, schema_version='1.0.0', retention_policy=retention_policy,
        authorization_context={'audience': audience, 'auth_epoch': str(auth_epoch)}, recovery_payload=payload)


def task_view(task):
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    return {'task_id': str(task.id), 'task_type': task.task_type, 'status': task.status,
        'status_version': str(task.status_version), 'target_task_id': task.input_refs['target']['task_id'],
        'poll_after_ms': 1500 if task.status in ('accepted', 'queued', 'running') else None,
        'error_code': attempt.error_code if attempt else None,
        'report': (attempt.usage_evidence or {}).get('reconciliation_report') if attempt else None}


@transaction.atomic
def submit_reconcile(*, actor, auth_epoch, audience, target, data, headers):
    seed = target_task(target)
    _, actor, patient = lock_scope(actor.id, seed.patient_id)
    permission(actor, patient, auth_epoch, audience)
    data = ReconcileInput.model_validate(data).model_dump()
    headers = ApplicationWriteHeaders.model_validate(headers)
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor,
        patient=patient, operation_id='task_reconcile', target_key=target)
    if (str(intent.request_id) != headers.request_id or str(intent.generation) != headers.generation
        or intent.recovery_payload != data or intent.payload_sha256 != compute_digest('task_reconcile', target, '1.0.0', data)
        or intent.authorization_context != {'audience': audience, 'auth_epoch': str(auth_epoch)}):
        raise TaskRejected('RECONCILE_INTENT_MISMATCH')
    if intent.status == 'committed':
        task = Task.objects.get(request_row=intent.write_request, task_type='reconcile', schema_version=SCHEMA)
        if not request_bound(task):
            raise TaskRejected('RECONCILE_RECEIPT_INVALID')
        return task_view(task)
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise TaskRejected('RECONCILE_INTENT_EXPIRED')
    seed = Task.objects.select_for_update().get(id=seed.id)
    if str(seed.status_version) != data['expected_status_version'] or str(seed.lease_generation) != data['expected_lease_generation']:
        raise TaskRejected('RECONCILE_TARGET_CHANGED')
    inputs = {'target': snapshot(seed), 'scope': data, 'context': context(actor, patient, auth_epoch, audience)}
    policy = inputs['context']['task_policy']
    now = timezone.now()
    task = Task.objects.create(actor=actor, patient=patient, task_type='reconcile', schema_version=SCHEMA,
        input_refs=inputs, input_sha256=compute_checksum(inputs), status='accepted', status_version=1,
        created_at=now, deadline_at=now + timedelta(seconds=policy['queue_wait_seconds'] + policy['execution_seconds']))
    task.request_row = commit_intent(intent=intent, action='task_reconcile', resource_type='task', resource_id=task.id)
    task.save(update_fields=['request_row'])
    emit_event(event_type='reconcile.requested', schema_version=SCHEMA, aggregate_id=task.id,
        aggregate_version=1, correlation_id=intent.request_id,
        resource_refs={'task_id': str(task.id), 'target_task_id': str(seed.id)}, task=task)
    return task_view(task)


def request_bound(task):
    saved = task.request_row
    if saved is None:
        return False
    target = '/api/support/tasks/' + task.input_refs.get('target', {}).get('task_id', '') + '/reconcile'
    intents = WriteIntent.objects.filter(write_request=saved, actor_id=task.actor_id, patient_id=task.patient_id,
        operation_id='task_reconcile', target_key=target, status='committed', request_id=saved.request_id,
        payload_sha256=saved.payload_sha256, recovery_payload=task.input_refs.get('scope'),
        authorization_context={'audience': 'support', 'auth_epoch': task.input_refs.get('context', {}).get('auth_epoch')})
    return bool(saved.status == 'committed' and saved.completed_at is not None and saved.resource_type == 'task'
        and saved.resource_id == task.id and saved.actor_id == task.actor_id and saved.patient_id == task.patient_id
        and saved.action == 'task_reconcile' and saved.target_key == target
        and saved.payload_sha256 == compute_digest('task_reconcile', target, '1.0.0', task.input_refs.get('scope'))
        and intents.count() == 1)


def event_bound(event, task):
    return bool(task.input_sha256 == compute_checksum(task.input_refs) and request_bound(task)
        and event.event_type == 'reconcile.requested' and event.schema_version == SCHEMA
        and event.task_id == task.id and event.aggregate_id == task.id and event.aggregate_version == 1
        and event.correlation_id == task.request_row.request_id
        and event.resource_refs == {'task_id': str(task.id), 'target_task_id': task.input_refs['target']['task_id']})


def summary_request_bound(target):
    """A local failure must refer to the exact committed summary admission."""
    saved = target.request_row
    if saved is None:
        return False
    path = reverse('summary_create', kwargs={'patient_id': target.patient_id})
    payload = target.input_refs.get('scope')
    context = target.input_refs.get('context', {})
    digest = compute_digest('summary_create', path, '1.0.0', payload)
    return bool(saved.target_key == path and saved.payload_sha256 == digest and saved.completed_at is not None
        and WriteIntent.objects.filter(write_request=saved, actor_id=target.actor_id, patient_id=target.patient_id,
            operation_id='summary_create', target_key=path, request_id=saved.request_id,
            payload_sha256=digest, recovery_payload=payload, status='committed',
            authorization_context={'audience': 'family', 'auth_epoch': context.get('auth_epoch')}).count() == 1)


def inspect_target(task):
    """Inspect bounded exact receipts. Only fence and fail an expired uncommitted local summary."""
    from summaries.models import Summary
    from summaries.jobs import event_bound as summary_event_bound, check_context
    target = Task.objects.select_for_update().get(id=task.input_refs['target']['task_id'], patient_id=task.patient_id)
    if target.task_type == 'reconcile':
        raise TaskRejected('RECONCILE_RECURSIVE_TARGET')
    attempts = list(TaskAttempt.objects.select_for_update().filter(task=target).order_by('attempt_number')[:LIMIT + 1])
    events = list(OutboxEvent.objects.select_for_update().filter(task=target).order_by('occurred_at')[:LIMIT + 1])
    receipts = list(ConsumerReceipt.objects.filter(event__task=target).order_by('processed_at')[:LIMIT + 1])
    report = {'target': snapshot(target), 'outcome': 'domain_review_required', 'changed': False,
        'attempts': [{'attempt_id': str(a.id), 'number': a.attempt_number, 'lease_generation': str(a.lease_generation),
            'status': a.status, 'provider_request_present': bool(a.provider_request_id)} for a in attempts[:LIMIT]],
        'events': [{'event_id': str(e.id), 'event_type': e.event_type} for e in events[:LIMIT]],
        'receipts': [{'receipt_id': str(r.id), 'event_id': str(r.event_id), 'consumer': r.consumer,
            'effect_resource_id': str(r.effect_resource_id)} for r in receipts[:LIMIT]]}
    if any(len(rows) > LIMIT for rows in (attempts, events, receipts)):
        report['reason'] = 'RECONCILE_INSPECTION_LIMIT'
        return report
    if snapshot(target) != task.input_refs['target']:
        report['reason'] = 'RECONCILE_TARGET_CHANGED'
        return report
    now = timezone.now()
    # Known terminal state is observed only; this does not attest its clinical result.
    if target.status in ('completed', 'failed', 'cancelled', 'superseded'):
        report['outcome'] = 'terminal_state_observed'
        return report
    if (target.task_type != 'summary' or target.schema_version != 'summary-task-2'
        or target.status != 'running' or target.lease_until is None or target.lease_until > now
        or target.input_sha256 != compute_checksum(target.input_refs)
        or Summary.objects.filter(task_id=target.id).exists() or receipts
        or len(events) != 1 or not summary_event_bound(events[0], target) or not summary_request_bound(target)
        or any(a.provider_request_id or a.status not in ('running', 'superseded') for a in attempts)
        or not any(a.lease_generation == target.lease_generation and a.status == 'running' for a in attempts)):
        report['reason'] = 'RECONCILE_NO_PROVEN_LOCAL_RECOVERY'
        return report
    actor = Principal.objects.select_for_update().get(id=target.actor_id)
    patient = Patient.objects.get(id=target.patient_id)
    check_context(target, actor, patient)
    TaskAttempt.objects.filter(task=target, status='running').update(status='failed', finished_at=now,
        error_code='RECONCILE_EXPIRED_LOCAL_LEASE')
    target.status, target.status_version = 'failed', target.status_version + 1
    target.lease_generation += 1
    target.lease_owner, target.lease_until = None, now
    target.save(update_fields=['status', 'status_version', 'lease_generation', 'lease_owner', 'lease_until'])
    # The original event and its receipts remain untouched. An operator must
    # submit a fresh intent to request new execution; reconciliation never sends.
    report.update(outcome='local_summary_lease_expired', changed=True, target_after=snapshot(target))
    return report


def execute_reconcile(event, owner):
    failure_generation = None
    try:
        with transaction.atomic():
            seed = Task.objects.get(id=event.task_id, task_type='reconcile', schema_version=SCHEMA)
            _, actor, patient = lock_scope(seed.actor_id, seed.patient_id)
            task = Task.objects.select_for_update().get(id=seed.id)
            failure_generation = task.lease_generation
            if not event_bound(event, task):
                raise TaskRejected('RECONCILE_EVENT_INVALID')
            expected = task.input_refs['context']
            if context(actor, patient, int(expected['auth_epoch']), expected['audience']) != expected:
                raise TaskRejected('RECONCILE_CONTEXT_CHANGED')
            receipt = ConsumerReceipt.objects.filter(consumer='reconcile', event=event).first()
            if receipt:
                if receipt.effect_resource_id != task.id:
                    raise TaskRejected('RECONCILE_RECEIPT_INVALID')
                return
            if task.status in ('completed', 'failed', 'cancelled', 'superseded'):
                return
            policy, now = expected['task_policy'], timezone.now()
            if task.deadline_at <= now or task.created_at + timedelta(seconds=policy['queue_wait_seconds']) <= now:
                raise TaskRejected('RECONCILE_DEADLINE_EXCEEDED')
            if task.status == 'running' and task.lease_until and task.lease_until > now:
                return
            if TaskAttempt.objects.filter(task=task).count() >= policy['max_attempts']:
                raise TaskRejected('RECONCILE_ATTEMPT_LIMIT')
            if Task.objects.filter(task_type='reconcile', status='running', lease_until__gt=now,
                    input_refs__context__runtime_id=expected['runtime_id']).exclude(id=task.id).count() >= policy['max_concurrency']:
                raise ReconcileDeferred(policy['heartbeat_seconds'], policy['queue_wait_seconds'] // policy['heartbeat_seconds'] + 1)
            task.deadline_at = min(task.deadline_at, now + timedelta(seconds=policy['execution_seconds']))
            task.save(update_fields=['deadline_at'])
            if not acquire_lease(task.id, owner, now + timedelta(seconds=policy['lease_seconds'])):
                return
            task.refresh_from_db()
            attempt = create_attempt(task, task.lease_generation)
            report = inspect_target(task)
            if task.deadline_at <= timezone.now() or task.lease_until <= timezone.now():
                raise TaskRejected('RECONCILE_DEADLINE_EXCEEDED')
            task.status, task.status_version, task.lease_until = 'completed', task.status_version + 1, timezone.now()
            task.save(update_fields=['status', 'status_version', 'lease_until'])
            attempt.status, attempt.finished_at = 'completed', timezone.now()
            attempt.usage_evidence = {'reconciliation_report': report, 'event_id': str(event.id)}
            attempt.save(update_fields=['status', 'finished_at', 'usage_evidence'])
            record_receipt('reconcile', event, task.id)
    except ReconcileDeferred:
        raise
    except Exception as exc:
        record_reconcile_failure(event, owner, getattr(exc, 'reason_code', 'RECONCILE_EXECUTION_FAILED'),
            expected_generation=failure_generation)
        raise


@transaction.atomic
def record_reconcile_failure(event, owner, code, *, expected_generation=None):
    seed = Task.objects.get(id=event.task_id, task_type='reconcile', schema_version=SCHEMA)
    lock_scope(seed.actor_id, seed.patient_id)
    task = Task.objects.select_for_update().get(id=seed.id)
    if not event_bound(event, task):
        return
    # The worker transaction rolls back on failure, so compare its entry generation.
    # A generic Celery callback has no generation authority over a running task.
    if expected_generation is not None and task.lease_generation != expected_generation:
        return
    if expected_generation is None and (task.status not in ('accepted', 'queued') or task.lease_generation != 0):
        return
    if task.status not in ('accepted', 'queued') and not (task.status == 'running'
            and expected_generation is not None and task.lease_owner == owner
            and task.lease_until is not None and task.lease_until <= timezone.now()):
        return
    task.status, task.status_version, task.lease_until = 'failed', task.status_version + 1, timezone.now()
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    attempts = TaskAttempt.objects.filter(task=task, lease_generation=task.lease_generation, status='running')
    if attempts.exists():
        attempts.update(status='failed', error_code=code, finished_at=timezone.now())
    else:
        TaskAttempt.objects.create(task=task, attempt_number=TaskAttempt.objects.filter(task=task).count() + 1,
            lease_generation=task.lease_generation, started_at=timezone.now(), finished_at=timezone.now(),
            status='failed', error_code=code)
    record_receipt('reconcile', event, task.id)


@transaction.atomic
def read_reconcile(*, actor, auth_epoch, audience, task_id):
    seed = Task.objects.get(id=task_id, task_type='reconcile', schema_version=SCHEMA)
    _, actor, patient = lock_scope(actor.id, seed.patient_id)
    permission(actor, patient, auth_epoch, audience)
    task = Task.objects.get(id=seed.id)
    if not request_bound(task) or task.input_sha256 != compute_checksum(task.input_refs):
        raise TaskRejected('RECONCILE_TASK_INVALID')
    return task_view(task)


@transaction.atomic
def reconciliation_request_view(*, actor, auth_epoch, audience, intent):
    if intent.operation_id != 'task_reconcile' or intent.actor_id != actor.id or intent.patient_id is None:
        raise TaskRejected('RECONCILE_REQUEST_INVALID')
    _, actor, patient = lock_scope(actor.id, intent.patient_id)
    permission(actor, patient, auth_epoch, audience)
    target = target_task(intent.target_key)
    payload = ReconcileInput.model_validate(intent.recovery_payload).model_dump()
    if target.patient_id != patient.id or intent.payload_sha256 != compute_digest('task_reconcile', intent.target_key, '1.0.0', payload):
        raise TaskRejected('RECONCILE_REQUEST_INVALID')
    task = None
    if intent.status == 'committed':
        task = Task.objects.get(request_row=intent.write_request, task_type='reconcile', schema_version=SCHEMA)
        if not request_bound(task) or task.input_sha256 != compute_checksum(task.input_refs):
            raise TaskRejected('RECONCILE_RECEIPT_INVALID')
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'status': intent.status, 'generation': str(intent.generation),
        'expires_at': intent.expires_at.isoformat(), 'task': task_view(task) if task else None}
