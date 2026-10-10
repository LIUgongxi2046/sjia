from decimal import Decimal

from api.base import ApiError
from archives.models import ArchivePageAttempt
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
    if pages and all(row.cost is not None for row in pages):
        costs = [Decimal(row.cost) for row in pages]
        if any(not cost.is_finite() or cost < 0 for cost in costs):
            raise ApiError('ARCHIVE_PROVIDER_COST_INVALID', 409)
        return sum(costs, Decimal('0'))
    uncertain = task.status in ('uncertain', 'awaiting_resolution') or any(
        row.cost is None and (row.status == 'uncertain' or row.finished_at is not None and provider_started(row))
        for row in pages)
    if task.status not in INFLIGHT and any(provider_started(row) for row in attempts):
        uncertain = True
    if uncertain:
        raise ApiError('ARCHIVE_BUDGET_INDETERMINATE', 409)
    if task.status in ('cancelled', 'failed', 'superseded') and not any(provider_started(row) for row in attempts):
        return Decimal('0')
    reservation = Decimal(task.input_refs['reserved_cost'])
    if not reservation.is_finite() or reservation < 0:
        raise ApiError('ARCHIVE_RESERVATION_INVALID', 409)
    return reservation


def batch_budget_status(pages, attempts):
    if any(row.cost is None and (row.status == 'uncertain'
            or row.finished_at is not None and provider_started(row)) for row in attempts):
        return 'unknown'
    if any(page.status in ('queued', 'processing', 'cancel_requested') for page in pages):
        return 'reserved'
    return 'released'
