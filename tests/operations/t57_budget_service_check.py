import argparse
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction
from api.base import ApiError
from archives import budget as service
from archives.models import ArchiveBatch, ArchivePage, ArchivePageAttempt
from archives.page_actions import check_budget
from archives.page_execution import update_batch
from configurations.models import ConfigVersion
from identity.models import Patient, PrincipalCredential
from tasks.models import Task, TaskAttempt
from tasks.service import register_task


def make_scenario(*, actor, page, task_status, attempt_status, archive_attempt_status,
        provider_started, label):
    patient = Patient.objects.get(id=page.patient_id)
    task = register_task(actor=actor, patient=patient, task_type=Task.TaskType.OCR,
        schema_version='archive-page-1', input_refs={'simulation_scenario': label,
            'archive_page_id': str(page.id), 'reserved_cost': '1', 'environment': 'simulation'})
    now = datetime.now(timezone.utc)
    finished_at = None if attempt_status == TaskAttempt.Status.RUNNING else now
    page_finished_at = None if archive_attempt_status == 'running' else now
    Task.objects.filter(id=task.id).update(status=task_status, status_version=2)
    provider_request_id = f'SIMULATION-{label}-{uuid4()}' if provider_started else None
    task_attempt = TaskAttempt.objects.create(task=task, attempt_number=1, lease_generation=1,
        provider_request_id=provider_request_id, started_at=now - timedelta(seconds=2),
        finished_at=finished_at, error_code=None, status=attempt_status,
        usage_evidence={'simulation_scenario': label, 'phase': 'provider_finished'} if provider_started else None)
    page_attempt = ArchivePageAttempt.objects.create(patient_id=page.patient_id, page_id=page.id,
        page_version=page.version, task_id=task.id, task_attempt_id=task_attempt.id,
        attempt_number=ArchivePageAttempt.objects.filter(page_id=page.id, page_version=page.version).count() + 1,
        input_sha256=task.input_sha256, model_version='simulation-budget-probe',
        provider_request_id=provider_request_id, started_at=now - timedelta(seconds=2),
        finished_at=page_finished_at, duration_ms=2000 if page_finished_at else None, cost=None, currency='CNY',
        usage_evidence={'simulation_scenario': label, 'phase': 'provider_finished'} if provider_started else None,
        status=archive_attempt_status, error_code=None)
    saved_task = Task.objects.get(id=task.id)
    saved_task_attempt = TaskAttempt.objects.get(id=task_attempt.id, task=saved_task)
    saved_page_attempt = ArchivePageAttempt.objects.get(id=page_attempt.id, task_id=saved_task.id)
    return saved_task, saved_task_attempt, saved_page_attempt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T57-budget-service-simulation-report.json'))
    args = parser.parse_args()
    simulation_root = Path(settings.SIMULATION_ROOT).resolve()
    expected_parent = (ROOT / '.planning-work' / 'parallel' / 'window4').resolve()
    if (settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or settings.SJIA_ENVIRONMENT != 'simulation'
            or simulation_root.parent != expected_parent
            or not simulation_root.name.startswith('isolated')):
        raise RuntimeError('T57 预算检查只能使用窗口四独立模拟环境。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        database = cursor.fetchone()
        cursor.execute('SHOW server_version')
        postgresql = cursor.fetchone()[0]
    if database != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise RuntimeError('T57 预算检查数据库身份不符。')

    assertions = unittest.TestCase()
    actor = PrincipalCredential.objects.get(username='simulation-family').principal
    page = ArchivePage.objects.order_by('available_at', 'id').first()
    if page is None:
        raise RuntimeError('隔离模拟库没有可用于预算检查的档案页。')
    known_task_ids = ArchivePageAttempt.objects.filter(cost__isnull=False).values_list('task_id', flat=True)
    existing_known_task = Task.objects.filter(schema_version='archive-page-1', patient_id=page.patient_id,
        id__in=known_task_ids).order_by('created_at').first()
    if existing_known_task is None:
        raise RuntimeError('隔离模拟库没有已计价档案页任务。')
    known_rows = list(ArchivePageAttempt.objects.filter(task_id=existing_known_task.id))
    expected_known_cost = sum((Decimal(row.cost) for row in known_rows), Decimal('0'))
    actual_known_cost = service.budget_cost(existing_known_task)
    if actual_known_cost != expected_known_cost:
        raise RuntimeError('预算服务返回费用与已保存计价尝试合计不符。')
    source_batch = ArchiveBatch.objects.get(id=page.batch_id)
    quota = ConfigVersion.objects.get(id=source_batch.quota_policy_id)
    try:
        check_budget(quota, source_batch, [page])
        caller_result = {'caller': 'archives.page_actions.check_budget', 'result': 'ALLOWED',
            'finding': 'PASS'}
    except ApiError as error:
        if error.code not in ('ARCHIVE_BUDGET_INDETERMINATE', 'ARCHIVE_BUDGET_CURRENCY_CHANGED') \
                or error.http_status != 409:
            raise
        caller_result = {'caller': 'archives.page_actions.check_budget', 'result': error.code,
            'http_status': error.http_status, 'finding': 'PASS'}

    scenarios = {}
    with transaction.atomic():
        cancelled, cancelled_attempt, cancelled_page_attempt = make_scenario(actor=actor, page=page,
            task_status=Task.Status.CANCELLED, attempt_status=TaskAttempt.Status.CANCELLED,
            archive_attempt_status='cancelled', provider_started=False, label='cancelled_before_provider')
        cancelled_cost = service.budget_cost(cancelled)
        if cancelled_cost != Decimal('0'):
            raise RuntimeError('未外发取消任务费用应为 0。')
        scenarios['cancelled_before_provider'] = {'task_id': str(cancelled.id),
            'task_attempt_id': str(cancelled_attempt.id), 'archive_page_attempt_id': str(cancelled_page_attempt.id),
            'cost': str(cancelled_cost), 'saved_and_reread': True, 'finding': 'PASS'}

        inflight, inflight_attempt, inflight_page_attempt = make_scenario(actor=actor, page=page,
            task_status=Task.Status.QUEUED, attempt_status=TaskAttempt.Status.RUNNING,
            archive_attempt_status='running', provider_started=False, label='queued_reservation')
        reserved = service.budget_cost(inflight)
        if reserved != Decimal('1'):
            raise RuntimeError('进行中任务没有保留配置额度。')
        scenarios['inflight_reservation'] = {'task_id': str(inflight.id),
            'task_attempt_id': str(inflight_attempt.id), 'archive_page_attempt_id': str(inflight_page_attempt.id),
            'reserved_cost': str(reserved), 'saved_and_reread': True, 'finding': 'PASS'}

        external_scenarios = [
            ('completed_after_provider', Task.Status.COMPLETED, TaskAttempt.Status.COMPLETED, 'failed'),
            ('cancelled_after_provider', Task.Status.CANCELLED, TaskAttempt.Status.CANCELLED, 'uncertain'),
            ('uncertain_after_provider', Task.Status.UNCERTAIN, TaskAttempt.Status.UNCERTAIN, 'uncertain'),
        ]
        for label, task_status, attempt_status, archive_status in external_scenarios:
            task, task_attempt, archive_attempt = make_scenario(actor=actor, page=page,
                task_status=task_status, attempt_status=attempt_status,
                archive_attempt_status=archive_status, provider_started=True, label=label)
            with assertions.assertRaises(service.ApiError) as error_info:
                service.budget_cost(task)
            error = error_info.exception
            if error.code != 'ARCHIVE_BUDGET_INDETERMINATE' or error.http_status != 409:
                raise RuntimeError(f'{label} 没有返回 ARCHIVE_BUDGET_INDETERMINATE 409。')
            finding = {'error_code': error.code, 'http_status': error.http_status, 'finding': 'PASS'}
            reread_task = Task.objects.get(id=task.id)
            reread_task_attempt = TaskAttempt.objects.get(id=task_attempt.id, task=reread_task)
            reread_archive_attempt = ArchivePageAttempt.objects.get(id=archive_attempt.id, task_id=reread_task.id)
            if (reread_task.input_refs.get('simulation_scenario') != label
                    or reread_task_attempt.provider_request_id != task_attempt.provider_request_id
                    or reread_archive_attempt.usage_evidence.get('simulation_scenario') != label):
                raise RuntimeError(f'{label} 模拟档案费用记录重新读取不一致。')
            scenarios[label] = {'task_id': str(task.id), 'task_attempt_id': str(task_attempt.id),
                'archive_page_attempt_id': str(archive_attempt.id), **finding, 'saved_and_reread': True}

        batch = ArchiveBatch.objects.get(id=page.batch_id)
        batch_pages = list(ArchivePage.objects.filter(batch_id=batch.id))
        batch_attempts = list(ArchivePageAttempt.objects.filter(page_id__in=[item.id for item in batch_pages]))
        batch_state = service.batch_budget_status(batch_pages, batch_attempts)
        if batch_state != 'unknown':
            raise RuntimeError('存在外发未知费用时，批次预算状态应为 unknown。')
        update_batch(batch.id)
        batch.refresh_from_db()
        if batch.budget_status != 'unknown':
            raise RuntimeError('批次预算状态保存后读取不一致。')

    report = {'task': 'T57', 'classification': 'SIMULATION_BUDGET_SERVICE_BOUNDARY_CHECK',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(),
            'postgresql': postgresql},
        'environment': {'database': database[0], 'schema': database[1], 'search_path': database[2],
            'runtime': 'simulation', 'isolated_root': str(simulation_root), 'external_model_requests': 0,
            'external_sends': 0, 'worker_or_beat_started': False, 'shared_queue_consumption': 0},
        'budget_service_source': str(ROOT / 'backend' / 'archives' / 'budget.py'),
        'known_cost_sum': {'task_id': str(existing_known_task.id), 'attempt_count': len(known_rows),
            'expected': str(expected_known_cost), 'actual': str(actual_known_cost), 'finding': 'PASS'},
        'scenarios': scenarios, 'budget_callers': [caller_result,
            {'caller': 'archives.page_execution.update_batch', 'batch_id': str(batch.id),
                'status': batch.budget_status, 'finding': 'PASS'}],
        'batch_budget_status': {'batch_id': str(batch.id),
            'status': batch.budget_status, 'finding': 'PASS'}, 'saved_and_reread': True}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('T57 预算服务报告保存后回读不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
