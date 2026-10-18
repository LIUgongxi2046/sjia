from decimal import Decimal

from api.base import ApiError
from archives.models import ArchivePageAttempt
from configurations.models import ConfigVersion
from recognition.provider_budget import _task_cost
from tasks.models import TaskAttempt


INFLIGHT = frozenset(('accepted', 'queued', 'running', 'cancel_requested'))


def provider_started(attempt):
    evidence = attempt.usage_evidence or {}
    return bool(attempt.provider_request_id or evidence.get('phase') in ('provider_started', 'provider_finished')
        or evidence.get('provider_called') is True or evidence.get('provider_request_id')
        or evidence.get('requests') or attempt.status == 'uncertain')


def budget_cost(task, page_attempts=None, task_attempts=None):
    if task.schema_version != 'archive-page-1':
        raise ApiError('ARCHIVE_BUDGET_TASK_INVALID', 409)
    pages = list(ArchivePageAttempt.objects.filter(task_id=task.id)) if page_attempts is None else page_attempts
    attempts = list(TaskAttempt.objects.filter(task=task)) if task_attempts is None else task_attempts
    if any(row.task_id != task.id for row in pages + attempts):
        raise ApiError('ARCHIVE_BUDGET_TASK_INVALID', 409)
    quota = ConfigVersion.objects.get(id=task.input_refs['quota_id'], kind='provider', schema_version='archive-quota-1')
    # 批次与账号共用费用计算，未知回执继续占用原有预留金额。
    return _task_cost(task, attempts, quota.payload['currency'],
        lambda code: ApiError(code, 409), [], page_attempts=pages)


def batch_budget_status(pages, attempts):
    if any(row.cost is None and (row.status == 'uncertain'
            or row.finished_at is not None and provider_started(row)) for row in attempts):
        return 'unknown'
    if any(page.status in ('queued', 'processing', 'cancel_requested') for page in pages):
        return 'reserved'
    return 'released'
