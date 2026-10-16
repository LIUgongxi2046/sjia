"""Local asynchronous summary rendering: immutable admission, current authority."""
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from assessment.provenance import entity_reference
from configurations.evidence import deployed_artifact_checksum
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from identity.models import Patient, Principal
from summaries.inputs import SummaryInput
from summaries.service import (SummaryRejected, current_summary_config,
    generate_summary, summary_publication_gate)
from tasks.models import ConsumerReceipt, Task, TaskAttempt
from tasks.outbox import record_receipt
from tasks.service import acquire_lease, create_attempt

SCHEMA = 'summary-task-2'


class SummaryDeferred(Exception):
    def __init__(self, seconds, max_retries):
        self.seconds = seconds
        self.max_retries = max_retries
        super().__init__('SUMMARY_CONCURRENCY_WAIT')


def lock_summary_scope(actor_id, patient_id, environment):
    # Same prefix for admission, worker, cancellation and failure. In particular,
    # do not hold Task before the permission evaluator's Gate/Principal locks.
    gate = EnvironmentGate.objects.select_for_update().get(environment=environment)
    actor = Principal.objects.select_for_update().get(id=actor_id)
    patient = Patient.objects.select_for_update().get(id=patient_id)
    return gate, actor, patient


def admission_context(actor, patient, epoch, audience, environment):
    from summaries.submission import summary_permission

    gate, actor, patient = lock_summary_scope(actor.id, patient.id, environment)
    if not gate.enabled or patient.status != 'active':
        raise SummaryRejected('SUMMARY_PATIENT_UNAVAILABLE')
    permission = summary_permission(actor, patient, epoch, audience)
    template = current_summary_config('summary', environment)
    visibility = current_summary_config('visibility', environment)
    if summary_publication_gate(environment, template, visibility) is None:
        raise SummaryRejected('SUMMARY_PUBLICATION_UNAVAILABLE')
    runtime = ConfigVersion.objects.select_for_update().filter(id=gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None or configuration_qualification_reason(runtime, environment) is not None:
        raise SummaryRejected('SUMMARY_RUNTIME_UNAVAILABLE')
    policies = [row for row in runtime.payload['tasks'] if row['task_type'] == 'summary']
    if len(policies) != 1:
        raise SummaryRejected('SUMMARY_TASK_POLICY_UNAVAILABLE')
    return actor, patient, {
        'environment': environment, 'auth_epoch': str(epoch), 'audience': audience,
        'patient_id': str(patient.id), 'actor_id': str(actor.id),
        'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'actor': entity_reference(actor), 'grant': entity_reference(permission.grant),
        'field_codes': sorted(permission.grant.field_codes),
        'template_id': str(template.id), 'visibility_policy_id': str(visibility.id),
        'template_checksum': template.checksum, 'visibility_checksum': visibility.checksum,
        'release_id': str(gate.active_release_id), 'publication_epoch': str(gate.publication_epoch),
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'implementation_sha256': deployed_artifact_checksum(template), 'task_policy': policies[0],
    }


def event_bound(event, task):
    from requests.service import compute_digest

    saved = task.request_row
    target = f'/api/patients/{task.patient_id}/summaries'
    return bool(event.event_type == 'summary.requested' and event.schema_version == SCHEMA
        and event.aggregate_id == task.id and event.aggregate_version == 1
        and event.resource_refs == {'task_id': str(task.id)} and event.task_id == task.id
        and saved is not None and saved.status == 'committed' and saved.resource_type == 'task'
        and saved.resource_id == task.id and saved.actor_id == task.actor_id and saved.patient_id == task.patient_id
        and saved.action == 'summary_create' and event.correlation_id == saved.request_id
        and saved.completed_at is not None and saved.target_key == target
        and saved.payload_sha256 == compute_digest('summary_create', target, '1.0.0', task.input_refs.get('scope')))


def current_task(event):
    seed = Task.objects.get(id=event.task_id, task_type='summary', schema_version=SCHEMA)
    _, actor, patient = lock_summary_scope(seed.actor_id, seed.patient_id, settings.SJIA_ENVIRONMENT)
    task = Task.objects.select_for_update().get(id=seed.id)
    if (not event_bound(event, task)
            or task.input_sha256 != compute_checksum(task.input_refs)
            or task.input_refs.get('context', {}).get('environment') != settings.SJIA_ENVIRONMENT):
        raise SummaryRejected('SUMMARY_TASK_INPUT_INVALID')
    return task, actor, patient


def check_context(task, actor, patient):
    expected = task.input_refs['context']
    actor, patient, actual = admission_context(actor, patient, int(expected['auth_epoch']),
        expected['audience'], expected['environment'])
    if actual != expected:
        raise SummaryRejected('SUMMARY_INPUT_CHANGED')
    SummaryInput.model_validate(task.input_refs['scope'])
    return actor, patient


def terminal_receipt(task, event):
    if task.status not in ('completed', 'cancelled', 'failed', 'superseded'):
        return False
    from summaries.models import Summary
    effect = Summary.objects.get(task_id=task.id).id if task.status == 'completed' else task.id
    receipt, _ = ConsumerReceipt.objects.get_or_create(consumer='summary', event=event,
        defaults={'effect_resource_id': effect, 'processed_at': timezone.now()})
    if receipt.effect_resource_id != effect:
        raise SummaryRejected('SUMMARY_CONSUMER_RECEIPT_INVALID')
    return True


def execute_summary(event, owner):
    observed_generation = None
    try:
        with transaction.atomic():
            task, actor, patient = current_task(event)
            if observed_generation is None:
                observed_generation = task.lease_generation
            if terminal_receipt(task, event):
                return
            check_context(task, actor, patient)
            policy = task.input_refs['context']['task_policy']
            now = timezone.now()
            if task.deadline_at <= now or (task.status in ('accepted', 'queued')
                    and task.created_at + timedelta(seconds=policy['queue_wait_seconds']) <= now):
                raise SummaryRejected('SUMMARY_DEADLINE_EXCEEDED')
            if task.status == 'running' and task.lease_until and task.lease_until > now:
                return
            if TaskAttempt.objects.filter(task=task).count() >= policy['max_attempts']:
                raise SummaryRejected('SUMMARY_ATTEMPT_LIMIT_EXCEEDED')
            inflight = Task.objects.filter(task_type='summary', schema_version=SCHEMA, status='running',
                lease_until__gt=now, input_refs__context__runtime_id=task.input_refs['context']['runtime_id']).exclude(id=task.id).count()
            if inflight >= policy['max_concurrency']:
                # Requeue the same event through Celery, not by racing the
                # dispatcher's published_at write after its original publish.
                raise SummaryDeferred(policy['heartbeat_seconds'],
                    policy['queue_wait_seconds'] // policy['heartbeat_seconds'] + 1)
            task.deadline_at = min(task.deadline_at, now + timedelta(seconds=policy['execution_seconds']))
            task.save(update_fields=['deadline_at'])
            if not acquire_lease(task.id, owner, now + timedelta(seconds=policy['lease_seconds'])):
                return
            task.refresh_from_db()
            attempt = create_attempt(task, task.lease_generation)
        observed_generation = attempt.lease_generation
        # The accepted/running state is durable before materialization. A cancel,
        # grant change or new record between transactions is rechecked below.
        with transaction.atomic():
            task, actor, patient = current_task(event)
            if observed_generation is None:
                observed_generation = task.lease_generation
            if terminal_receipt(task, event):
                return
            if task.status != 'running' or task.lease_owner != owner or task.lease_generation != attempt.lease_generation:
                return
            if task.lease_until is None or task.lease_until <= timezone.now() or task.deadline_at <= timezone.now():
                raise SummaryRejected('SUMMARY_DEADLINE_EXCEEDED')
            actor, patient = check_context(task, actor, patient)
            data = SummaryInput.model_validate(task.input_refs['scope']).model_dump()
            summary = generate_summary(patient=patient, requested_by=actor,
                from_date=date.fromisoformat(data['from_date']), to_date=date.fromisoformat(data['to_date']),
                scope_timezone=data['scope_timezone'], include_unknown_time=data['include_unknown_time'],
                field_codes=task.input_refs['context']['field_codes'], environment=settings.SJIA_ENVIRONMENT,
                record_kinds=data['record_kinds'], include_source_status=data['include_source_status'], execution_task=task)
            attempt.status, attempt.finished_at = 'completed', timezone.now()
            attempt.save(update_fields=['status', 'finished_at'])
            record_receipt('summary', event, summary.id)
    except SummaryDeferred:
        raise
    except Exception as error:
        record_summary_failure(event, owner, getattr(error, 'reason_code', 'SUMMARY_EXECUTION_FAILED'),
            lease_generation=observed_generation)
        raise


@transaction.atomic
def record_summary_failure(event, owner, code, *, lease_generation=None):
    seed = Task.objects.get(id=event.task_id, task_type='summary', schema_version=SCHEMA)
    lock_summary_scope(seed.actor_id, seed.patient_id, settings.SJIA_ENVIRONMENT)
    task = Task.objects.select_for_update().get(id=seed.id)
    if not event_bound(event, task):
        return
    if lease_generation is not None and task.lease_generation != lease_generation:
        return
    if task.status not in ('accepted', 'queued') and not (task.status == 'running'
            and task.lease_owner == owner and lease_generation == task.lease_generation):
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
    if not ConsumerReceipt.objects.filter(consumer='summary', event=event).exists():
        record_receipt('summary', event, task.id)


@transaction.atomic
def cancel_summary(*, task_id, actor, auth_epoch, audience):
    from summaries.submission import summary_permission
    from tasks.service import cancel_task

    seed = Task.objects.get(id=task_id, actor=actor, task_type='summary')
    _, actor, patient = lock_summary_scope(actor.id, seed.patient_id, settings.SJIA_ENVIRONMENT)
    summary_permission(actor, patient, auth_epoch, audience)
    return cancel_task(seed.id)
