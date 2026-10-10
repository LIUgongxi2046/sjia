from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from archives.models import ArchivePageAttempt
from configurations.models import ConfigVersion
from tasks.models import Task, TaskAttempt


INFLIGHT = frozenset(('accepted', 'queued', 'running', 'cancel_requested', 'uncertain', 'awaiting_resolution'))


def _provider_started(*, task_attempt, archive_attempt=None):
    task_evidence = task_attempt.usage_evidence or {}
    provider_started = bool(task_attempt.provider_request_id or task_evidence.get('provider_called')
        or task_evidence.get('provider_request_id')
        or task_evidence.get('phase') in ('provider_started', 'provider_finished'))
    if archive_attempt is None:
        return provider_started or task_attempt.status == TaskAttempt.Status.UNCERTAIN
    evidence = archive_attempt.usage_evidence or {}
    return bool(provider_started or archive_attempt.provider_request_id or evidence.get('provider_request_id')
        or evidence.get('provider_called') or evidence.get('phase') in ('provider_started', 'provider_finished')
        or archive_attempt.status == 'uncertain' or task_attempt.status == TaskAttempt.Status.UNCERTAIN)


def _valid_amount(value, code, reject):
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise reject(code)
    return amount


def _retain_unknown_reservation(task, attempt_id, currency, reject, unknown_reservations):
    reserved = _valid_amount(task.input_refs['reserved_cost'], 'PROVIDER_RESERVATION_INVALID', reject)
    if reserved <= 0:
        raise reject('PROVIDER_BUDGET_UNKNOWN')
    if task.schema_version == 'archive-page-1':
        quota = ConfigVersion.objects.get(id=task.input_refs['quota_id'], kind='provider', schema_version='archive-quota-1')
        captured_currency = quota.payload['currency']
    else:
        captured_currency = task.input_refs['provider_entry']['currency']
    if captured_currency != currency:
        raise reject('PROVIDER_BUDGET_CURRENCY_CHANGED')
    unknown_reservations.append({'task_id': str(task.id), 'attempt_id': str(attempt_id),
        'reserved_cost': str(reserved), 'currency': currency, 'actual_cost': None,
        'provider_result': 'unknown'})
    return reserved


def _task_cost(task, task_attempts, currency, reject, unknown_reservations):
    if task.schema_version == 'archive-page-1':
        page_attempts = list(ArchivePageAttempt.objects.filter(task_id=task.id).order_by('attempt_number'))
        task_attempt_by_id = {str(item.id): item for item in task_attempts}
        measured = Decimal('0')
        needs_reservation = task.status in ('accepted', 'queued', 'running', 'cancel_requested') and (
            not page_attempts or page_attempts[-1].cost is not None)
        if task_attempts and not page_attempts:
            raise reject('PROVIDER_BUDGET_EVIDENCE_INVALID')
        for page_attempt in page_attempts:
            task_attempt = task_attempt_by_id.get(str(page_attempt.task_attempt_id))
            if task_attempt is None:
                raise reject('PROVIDER_BUDGET_EVIDENCE_INVALID')
            if page_attempt.cost is not None:
                if page_attempt.currency != currency:
                    raise reject('PROVIDER_BUDGET_CURRENCY_CHANGED')
                measured += _valid_amount(page_attempt.cost, 'PROVIDER_COST_INVALID', reject)
            elif _provider_started(task_attempt=task_attempt, archive_attempt=page_attempt):
                measured += _retain_unknown_reservation(task, task_attempt.id, currency, reject, unknown_reservations)
            elif task.status in INFLIGHT:
                needs_reservation = True
        if needs_reservation:
            return measured + _valid_amount(task.input_refs['reserved_cost'], 'PROVIDER_RESERVATION_INVALID', reject)
        return measured

    measured = Decimal('0')
    needs_reservation = task.status in INFLIGHT and not task_attempts
    for attempt in task_attempts:
        evidence = attempt.usage_evidence or {}
        if _provider_started(task_attempt=attempt):
            if evidence.get('actual_cost') is None:
                measured += _retain_unknown_reservation(task, attempt.id, currency, reject, unknown_reservations)
                continue
            if evidence.get('currency') != currency:
                raise reject('PROVIDER_BUDGET_CURRENCY_CHANGED')
            measured += _valid_amount(evidence['actual_cost'], 'PROVIDER_COST_INVALID', reject)
        elif task.status in INFLIGHT and attempt.status == TaskAttempt.Status.RUNNING:
            needs_reservation = True
        elif attempt.status == TaskAttempt.Status.UNCERTAIN:
            measured += _retain_unknown_reservation(task, attempt.id, currency, reject, unknown_reservations)
    if needs_reservation:
        return measured + _valid_amount(task.input_refs['reserved_cost'], 'PROVIDER_RESERVATION_INVALID', reject)
    return measured


def check_provider_budget(*, runtime, billing_scope_key, currency, daily_budget, reserved_cost,
        budget_timezone, max_concurrency, reject, now=None, additional_tasks=1):
    """在已锁定 EnvironmentGate 的事务中核查同计费账号的跨配置版本费用。"""
    entries = [item for item in runtime.payload['providers']
        if item.get('billing_scope_key') == billing_scope_key and item.get('enabled')]
    if not entries or any(item.get('currency') != currency or item.get('daily_budget') is None
            or Decimal(item['daily_budget']) != Decimal(daily_budget) for item in entries):
        raise reject('PROVIDER_BUDGET_CURRENCY_CHANGED')

    provider_ids = {item['config_id'] for item in entries}
    provider_rows = list(ConfigVersion.objects.filter(id__in=provider_ids, kind='provider'))
    if len(provider_rows) != len(provider_ids):
        raise reject('PROVIDER_BUDGET_CONFIGURATION_MISSING')
    config_keys = {row.config_key for row in provider_rows}
    historical_ids = [str(item) for item in ConfigVersion.objects.filter(
        config_key__in=config_keys, kind='provider').values_list('id', flat=True)]
    tasks = list(Task.objects.filter(input_refs__provider_config_id__in=[str(item) for item in historical_ids],
        input_refs__environment=settings.SJIA_ENVIRONMENT).order_by('id'))
    inflight = [task for task in tasks if task.status in INFLIGHT]
    if additional_tasks < 1 or len(inflight) + additional_tasks > max_concurrency:
        raise reject('PROVIDER_CONCURRENCY_LIMIT')
    current = now or timezone.now()
    local_day = current.astimezone(ZoneInfo(budget_timezone)).date()
    total = Decimal('0')
    unknown_reservations = []
    with localcontext() as arithmetic:
        arithmetic.prec = 80
        for task in tasks:
            if task.created_at.astimezone(ZoneInfo(budget_timezone)).date() != local_day and task not in inflight:
                continue
            attempts = list(TaskAttempt.objects.filter(task=task).order_by('attempt_number'))
            total += _task_cost(task, attempts, currency, reject, unknown_reservations)
        requested_reservation = _valid_amount(reserved_cost, 'PROVIDER_RESERVATION_INVALID', reject)
        limit = _valid_amount(daily_budget, 'PROVIDER_BUDGET_INVALID', reject)
        if total + requested_reservation > limit:
            raise reject('PROVIDER_DAILY_BUDGET_EXCEEDED')
    return {'billing_scope_key': billing_scope_key, 'currency': currency,
        'budget_date': local_day.isoformat(), 'existing_cost_or_reservation': str(total),
        'requested_reservation': str(requested_reservation), 'daily_budget': str(limit),
        'inflight_count': len(inflight), 'provider_config_ids': sorted(historical_ids),
        'unknown_reservations': unknown_reservations,
        'unknown_reserved_cost': str(sum((Decimal(row['reserved_cost']) for row in unknown_reservations), Decimal('0'))),
        'budget_state': 'unknown' if unknown_reservations else 'available'}
