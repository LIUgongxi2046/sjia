import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection
from django.utils import timezone as django_timezone
from identity.models import PrincipalCredential
from tasks.models import Task, TaskAttempt
from tasks.service import acquire_lease, cancel_task, create_attempt, finish_attempt, register_task


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T57-attempt-terminal-simulation-report.json'))
    args = parser.parse_args()

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        database = cursor.fetchone()
        cursor.execute('SHOW server_version')
        postgresql = cursor.fetchone()[0]
    if database[0:2] != ('sjia_simulation', 'sjia') or database[2].replace(' ', '') != 'sjia,public':
        raise RuntimeError('T57 终态检查只能使用 sjia_simulation.sjia。')
    if settings.SJIA_ENVIRONMENT != 'simulation' or not str(settings.SIMULATION_ROOT).endswith('isolated-model-20261009-run5'):
        raise RuntimeError('T57 终态检查没有使用窗口四专用模拟环境。')

    actor = PrincipalCredential.objects.get(username='simulation-support').principal
    namespace = f'window4-t57-terminal-{uuid4()}'
    now = django_timezone.now()

    local = register_task(actor=actor, task_type=Task.TaskType.ASSESSMENT,
        schema_version='window4-terminal-local-1', input_refs={'simulation_marker': namespace})
    if acquire_lease(local.id, f'{namespace}-local', now + timedelta(minutes=1)) != 1:
        raise RuntimeError('本地模拟任务未取得执行权。')
    local_attempt = create_attempt(local, lease_generation=1)
    if cancel_task(local.id) != 1:
        raise RuntimeError('本地模拟任务取消未生效。')
    local.refresh_from_db()
    local_attempt.refresh_from_db()

    external = register_task(actor=actor, task_type=Task.TaskType.OCR,
        schema_version='window4-terminal-external-1', input_refs={'simulation_marker': namespace,
            'external_request': False})
    if acquire_lease(external.id, f'{namespace}-external', now + timedelta(minutes=1)) != 1:
        raise RuntimeError('模拟 OCR 任务未取得执行权。')
    external_attempt = create_attempt(external, lease_generation=1)
    if cancel_task(external.id) != 1:
        raise RuntimeError('模拟 OCR 任务取消未生效。')
    finish_attempt(external_attempt, TaskAttempt.Status.COMPLETED,
        usage_evidence={'usage': {'prompt_tokens': 3, 'completion_tokens': 2},
            'simulated_late_result': True})
    external.refresh_from_db()
    external_attempt.refresh_from_db()

    confirmation = register_task(actor=actor, task_type=Task.TaskType.OCR,
        schema_version='window4-terminal-confirmation-1', input_refs={'simulation_marker': namespace,
            'external_request': False})
    if acquire_lease(confirmation.id, f'{namespace}-confirmation', now + timedelta(minutes=1)) != 1:
        raise RuntimeError('待确认模拟任务未取得执行权。')
    confirmation_attempt = create_attempt(confirmation, lease_generation=1)
    Task.objects.filter(id=confirmation.id, status=Task.Status.RUNNING).update(
        status=Task.Status.AWAITING_CONFIRMATION, status_version=2)
    finish_attempt(confirmation_attempt, TaskAttempt.Status.COMPLETED,
        usage_evidence={'usage': {'prompt_tokens': 4, 'completion_tokens': 1},
            'simulated_confirmation_result': True})
    confirmation.refresh_from_db()
    confirmation_attempt.refresh_from_db()

    if local.status != Task.Status.CANCELLED or local_attempt.status != TaskAttempt.Status.CANCELLED:
        raise RuntimeError('本地取消没有同步任务尝试终态。')
    if (external.status != Task.Status.CANCELLED or external_attempt.status != TaskAttempt.Status.UNCERTAIN
            or external_attempt.usage_evidence.get('usage', {}).get('prompt_tokens') != 3):
        raise RuntimeError('外部结果未知时的取消状态或迟到用量保存不符合预期。')
    if (confirmation.status != Task.Status.AWAITING_CONFIRMATION
            or confirmation_attempt.status != TaskAttempt.Status.COMPLETED
            or confirmation_attempt.usage_evidence.get('usage', {}).get('prompt_tokens') != 4):
        raise RuntimeError('待人工确认任务未保留任务状态、完成尝试及实际用量。')

    report = {'task': 'T57', 'classification': 'SIMULATION_TASK_ATTEMPT_TERMINAL_CHECK',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'environment': {'database': database[0], 'schema': database[1], 'search_path': database[2],
            'postgresql': postgresql, 'runtime': 'simulation', 'isolated_root': str(settings.SIMULATION_ROOT),
            'external_requests': 0, 'notifications_sent': 0, 'worker_or_beat_started': False,
            'shared_queue_consumption': 0},
        'local_cancel': {'task_id': str(local.id), 'task_status': local.status,
            'attempt_id': str(local_attempt.id), 'attempt_status': local_attempt.status,
            'finding': 'PASS'},
        'external_cancel_and_late_usage': {'task_id': str(external.id), 'task_status': external.status,
            'attempt_id': str(external_attempt.id), 'attempt_status': external_attempt.status,
            'late_usage_saved': external_attempt.usage_evidence,
            'finding': 'PASS'},
        'awaiting_confirmation_completion': {'task_id': str(confirmation.id),
            'task_status': confirmation.status, 'attempt_id': str(confirmation_attempt.id),
            'attempt_status': confirmation_attempt.status,
            'usage_saved': confirmation_attempt.usage_evidence, 'finding': 'PASS'},
        'saved_and_reread': True}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('T57 终态检查报告保存后回读内容不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
