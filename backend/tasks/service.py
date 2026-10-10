import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Least
from django.utils import timezone

from tasks.models import OutboxEvent, Task, TaskAttempt

LOCAL_TASK_TYPES = (Task.TaskType.FILE_VALIDATION, Task.TaskType.ASSESSMENT,
    Task.TaskType.ALERT_EVALUATION, Task.TaskType.SUMMARY)

class TaskRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


def input_digest(input_refs: dict) -> str:
    canonical = json.dumps(input_refs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@transaction.atomic
def register_task(
    *,
    actor,
    task_type,
    schema_version,
    input_refs,
    patient=None,
    request_row=None,
    deadline_seconds=300,
) -> Task:
    if task_type not in Task.TaskType.values:
        raise TaskRejected("TASK_TYPE_UNREGISTERED")
    now = timezone.now()
    return Task.objects.create(
        actor=actor,
        patient=patient,
        request_row=request_row,
        task_type=task_type,
        schema_version=schema_version,
        input_refs=input_refs,
        input_sha256=input_digest(input_refs),
        status=Task.Status.ACCEPTED,
        status_version=1,
        created_at=now,
        deadline_at=now + timedelta(seconds=deadline_seconds),
    )


def _conditional_update(task_id, *, conditions, updates) -> int:
    return Task.objects.filter(id=task_id).filter(*conditions).update(**updates)


def enqueue(task_id) -> int:
    """Dispatcher 的 queued 更新只作用于仍为 accepted 的任务，不覆盖 running 或终态。"""
    return _conditional_update(
        task_id,
        conditions=[Q(status=Task.Status.ACCEPTED)],
        updates={"status": Task.Status.QUEUED, "status_version": F("status_version") + 1},
    )


@transaction.atomic
def acquire_lease(task_id, owner, lease_until) -> int:
    now = timezone.now()
    if not owner:
        raise TaskRejected('TASK_LEASE_OWNER_REQUIRED')
    task = Task.objects.select_for_update().filter(id=task_id).first()
    if task is None or task.deadline_at <= now or lease_until <= now:
        return 0
    if task.status not in (Task.Status.ACCEPTED, Task.Status.QUEUED, Task.Status.RUNNING):
        return 0
    if task.lease_until is not None and task.lease_until > now:
        return 0
    if task.status == Task.Status.RUNNING:
        attempts = TaskAttempt.objects.select_for_update().filter(task=task, lease_generation=task.lease_generation)
        # 已开始的外部或对象操作必须核查原结果，不能自动重新执行。
        if attempts.exists() and task.task_type not in LOCAL_TASK_TYPES:
            attempts.filter(status=TaskAttempt.Status.RUNNING).update(status=TaskAttempt.Status.UNCERTAIN,
                finished_at=now, error_code='TASK_LEASE_EXPIRED_RESULT_UNKNOWN')
            task.status = Task.Status.AWAITING_RESOLUTION
            task.status_version += 1
            task.save(update_fields=['status', 'status_version'])
            return 0
        attempts.filter(status=TaskAttempt.Status.RUNNING).update(status=TaskAttempt.Status.SUPERSEDED,
            finished_at=now, error_code='TASK_LEASE_EXPIRED')
    task.status, task.lease_owner = Task.Status.RUNNING, owner
    task.lease_until = min(lease_until, task.deadline_at)
    task.lease_generation += 1
    task.status_version += 1
    task.save(update_fields=['status', 'lease_owner', 'lease_until', 'lease_generation', 'status_version'])
    return 1


def heartbeat(task_id, lease_generation, lease_until) -> int:
    now = timezone.now()
    if lease_until <= now:
        return 0
    return _conditional_update(
        task_id,
        conditions=[Q(lease_generation=lease_generation), Q(status=Task.Status.RUNNING),
            Q(lease_until__gt=now), Q(deadline_at__gt=now)],
        updates={"lease_until": Least(Value(lease_until), F('deadline_at'))},
    )


def complete_task(task_id, lease_generation, result_type, result_id) -> int:
    now = timezone.now()
    return _conditional_update(
        task_id,
        conditions=[Q(lease_generation=lease_generation), Q(status=Task.Status.RUNNING),
            Q(lease_until__gt=now), Q(deadline_at__gt=now)],
        updates={"status": Task.Status.COMPLETED, "status_version": F("status_version") + 1, "lease_until": now},
    )


def fail_task(task_id, lease_generation, error_code) -> int:
    now = timezone.now()
    return _conditional_update(
        task_id,
        conditions=[Q(lease_generation=lease_generation), Q(status=Task.Status.RUNNING),
            Q(lease_until__gt=now), Q(deadline_at__gt=now)],
        updates={"status": Task.Status.FAILED, "status_version": F("status_version") + 1, "lease_until": now},
    )


@transaction.atomic
def cancel_task(task_id) -> int:
    task = Task.objects.select_for_update().filter(id=task_id).first()
    if task is None or task.status not in (Task.Status.ACCEPTED, Task.Status.QUEUED,
            Task.Status.RUNNING, Task.Status.AWAITING_CONFIRMATION):
        return 0
    now = timezone.now()
    attempts = TaskAttempt.objects.select_for_update().filter(task=task,
        lease_generation=task.lease_generation, status=TaskAttempt.Status.RUNNING)
    local = task.task_type in LOCAL_TASK_TYPES
    attempts.update(status=TaskAttempt.Status.CANCELLED if local else TaskAttempt.Status.UNCERTAIN,
        finished_at=now, error_code='TASK_CANCELLED' if local else 'TASK_CANCELLED_RESULT_UNKNOWN')
    task.status = Task.Status.CANCELLED
    task.status_version += 1
    task.lease_until = now
    task.save(update_fields=['status', 'status_version', 'lease_until'])
    return 1


@transaction.atomic
def create_attempt(task, lease_generation, provider_request_id=None) -> TaskAttempt:
    task = Task.objects.select_for_update().get(id=task.id)
    if task.lease_generation != lease_generation:
        raise TaskRejected("LEASE_GENERATION_MISMATCH")
    now = timezone.now()
    if task.status != Task.Status.RUNNING or task.lease_until is None or task.lease_until <= now or task.deadline_at <= now:
        raise TaskRejected('TASK_LEASE_UNAVAILABLE')
    if TaskAttempt.objects.filter(task=task, lease_generation=lease_generation).exists():
        raise TaskRejected('TASK_ATTEMPT_ALREADY_EXISTS')
    number = TaskAttempt.objects.filter(task=task).count() + 1
    return TaskAttempt.objects.create(
        task=task,
        attempt_number=number,
        lease_generation=lease_generation,
        provider_request_id=provider_request_id,
        started_at=timezone.now(),
        status=TaskAttempt.Status.RUNNING,
    )


@transaction.atomic
def finish_attempt(attempt, status, error_code=None, usage_evidence=None) -> None:
    if status not in TaskAttempt.Status.values or status == TaskAttempt.Status.RUNNING:
        raise TaskRejected('TASK_ATTEMPT_FINAL_STATUS_INVALID')
    if usage_evidence is not None and not isinstance(usage_evidence, dict):
        raise TaskRejected('TASK_ATTEMPT_USAGE_INVALID')
    task = Task.objects.select_for_update().get(id=attempt.task_id)
    current = TaskAttempt.objects.select_for_update().get(id=attempt.id, task=task)
    now = timezone.now()
    if current.status == TaskAttempt.Status.RUNNING:
        current.status, current.error_code = status, error_code
        if task.lease_generation != current.lease_generation:
            current.status, current.error_code = TaskAttempt.Status.SUPERSEDED, 'LEASE_GENERATION_MISMATCH'
        elif status == TaskAttempt.Status.COMPLETED and task.status not in (
                Task.Status.COMPLETED, Task.Status.AWAITING_CONFIRMATION):
            if (task.status != Task.Status.RUNNING or task.lease_until is None
                    or task.lease_until <= now or task.deadline_at <= now):
                current.status = (TaskAttempt.Status.SUPERSEDED if task.task_type in LOCAL_TASK_TYPES
                    else TaskAttempt.Status.UNCERTAIN)
                current.error_code = 'TASK_COMPLETION_AUTHORITY_UNAVAILABLE'
        current.finished_at = now
    # 取消及过期尝试保持原状态，迟到的实际计量继续保存。
    if usage_evidence is not None:
        if current.usage_evidence is not None and not isinstance(current.usage_evidence, dict):
            raise TaskRejected('TASK_ATTEMPT_USAGE_INVALID')
        current.usage_evidence = {**(current.usage_evidence or {}), **usage_evidence}
    current.save(update_fields=['status', 'error_code', 'usage_evidence', 'finished_at'])
    attempt.status, attempt.error_code = current.status, current.error_code
    attempt.usage_evidence, attempt.finished_at = current.usage_evidence, current.finished_at
