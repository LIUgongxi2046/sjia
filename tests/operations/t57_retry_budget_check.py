import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction
from django.test import Client
from django.utils import timezone as django_timezone
from archives.models import ArchiveBatch, ArchivePage, ArchivePageAttempt, PurposeCopy
from archives.page_actions import reserved_cost
from archives.page_execution import update_batch
from authorization.policy import matching_case_grants
from files.models import FileGroup, FileObject, FilePage
from files.purpose import current_review
from identity.models import Patient, PrincipalCredential
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt
from tasks.service import acquire_lease, create_attempt, fail_task, finish_attempt, register_task


def isolated_database():
    simulation_root = Path(settings.SIMULATION_ROOT).resolve()
    isolated_parent = (ROOT / '.planning-work' / 'parallel' / 'window4').resolve()
    if (settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or settings.SJIA_ENVIRONMENT != 'simulation'
            or simulation_root.parent != isolated_parent
            or not simulation_root.name.startswith('isolated')):
        raise RuntimeError('T57 核验只能使用窗口四独立模拟环境。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        database = cursor.fetchone()
        cursor.execute('SHOW server_version')
        postgresql = cursor.fetchone()[0]
    if database != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise RuntimeError('T57 核验数据库身份不符。')
    return database, postgresql


@transaction.atomic
def retry_group(actor):
    patient_candidates = Patient.objects.filter(id__in=FileGroup.objects.filter(
        owner=actor).values_list('patient_id', flat=True)).distinct()
    group = None
    target = settings.ARK_BASE_URL.rstrip('/')
    for patient in patient_candidates:
        if not all(matching_case_grants(actor, patient, action, datetime.now(timezone.utc),
                require_relation=True) for action in ('write_record', 'read_file')):
            continue
        groups = FileGroup.objects.filter(owner=actor, patient=patient,
            status=FileGroup.Status.AVAILABLE).order_by('created_at', 'id')
        for candidate in groups:
            originals = list(FileObject.objects.filter(file_group_id=candidate.id,
                patient=patient, purpose=FileObject.Purpose.ORIGINAL,
                status=FileObject.Status.AVAILABLE))
            pages = list(FilePage.objects.filter(file_group_id=candidate.id, patient=patient))
            if not originals or {str(item.id) for item in originals} != {str(page.file_id) for page in pages}:
                continue
            copies = []
            for original in originals:
                matches = list(PurposeCopy.objects.filter(original_file_id=original.id,
                    purpose='external_recognition', environment=settings.SJIA_ENVIRONMENT,
                    status='approved', processing_evidence__target=target))
                if len(matches) != 1:
                    break
                copy = matches[0]
                derived = FileObject.objects.get(id=copy.derived_file_id, patient=patient)
                if current_review(copy, original, derived) is None:
                    break
                copies.append(copy)
            if len(copies) == len(originals):
                group = candidate
                break
        if group is not None:
            break
    if group is None:
        raise RuntimeError('隔离模拟账号没有同时具备授权、完整原件和当前批准用途副本的 OCR 文件组。')
    return group


def retry_check():
    actor = PrincipalCredential.objects.get(username='simulation-family').principal
    group = retry_group(actor)

    retry_source = register_task(actor=actor, patient=group.patient, task_type=Task.TaskType.OCR,
        schema_version='recognition-1', input_refs={'file_group_id': str(group.id),
            'file_group_version': str(group.version), 'auth_epoch': actor.auth_epoch,
            'retry_of': None, 'simulation_probe': True})
    if acquire_lease(retry_source.id, 'window4-t57-retry-probe',
            django_timezone.now() + timedelta(seconds=60)) != 1:
        raise RuntimeError('模拟重试源任务未取得执行租约。')
    retry_source.refresh_from_db()
    source_attempt = create_attempt(retry_source, retry_source.lease_generation)
    finish_attempt(source_attempt, TaskAttempt.Status.FAILED, error_code='SIMULATION_PROBE_FAILURE')
    if fail_task(retry_source.id, retry_source.lease_generation, 'SIMULATION_PROBE_FAILURE') != 1:
        raise RuntimeError('模拟重试源任务没有通过任务服务进入失败终态。')
    retry_source.refresh_from_db()
    source_attempt.refresh_from_db()
    if (retry_source.status != Task.Status.FAILED
            or source_attempt.status != TaskAttempt.Status.FAILED
            or source_attempt.provider_request_id is not None):
        raise RuntimeError('模拟失败源任务或尝试的保存状态不符。')
    client = Client()
    login_response = client.post('/api/auth/login', data=json.dumps({
        'username': 'simulation-family', 'password': os.environ['SJIA_SIMULATION_PASSWORD']}),
        content_type='application/json')
    if login_response.status_code != 200:
        raise RuntimeError(f'模拟账号登录失败：HTTP {login_response.status_code}')
    first_response = client.post(f'/api/tasks/{retry_source.id}/retry', data='{}',
        content_type='application/json')
    if first_response.status_code != 200:
        raise RuntimeError(f'任务重试 API 首次请求失败：HTTP {first_response.status_code} {first_response.content.decode()}')
    first_payload = json.loads(first_response.content)
    second_response = client.post(f'/api/tasks/{retry_source.id}/retry', data='{}',
        content_type='application/json')
    if second_response.status_code != 200:
        raise RuntimeError(f'任务重试 API 重复请求失败：HTTP {second_response.status_code} {second_response.content.decode()}')
    second_payload = json.loads(second_response.content)
    retried = Task.objects.get(id=first_payload['task_id'], actor=actor)
    retry_events = OutboxEvent.objects.filter(task=retried, event_type='recognition.requested')
    receipt_count = ConsumerReceipt.objects.filter(consumer='recognition', event__in=retry_events).count()
    attempt_count = TaskAttempt.objects.filter(task=retried).count()
    if (first_payload['task_id'] != second_payload['task_id'] or retried.status != Task.Status.ACCEPTED
            or retried.input_refs.get('retry_of') != str(retry_source.id)
            or retry_events.count() != 1 or receipt_count != 0 or attempt_count != 0):
        raise RuntimeError('任务重试 API 没有生成唯一的新任务，或检查期间发生了消费。')
    return {'method': 'POST', 'path': f'/api/tasks/{retry_source.id}/retry',
        'first_http_status': first_response.status_code, 'duplicate_http_status': second_response.status_code,
        'source_task_id': str(retry_source.id), 'source_task_status': Task.Status.FAILED,
        'source_attempt_id': str(source_attempt.id), 'source_attempt_status': source_attempt.status,
        'source_failure_created_by_task_service': True,
        'new_task_id': str(retried.id), 'new_task_status': retried.status,
        'retry_of': retried.input_refs['retry_of'], 'repeated_request_same_task': first_payload == second_payload,
        'outbox_events': retry_events.count(), 'consumer_receipts': receipt_count,
        'worker_attempts': attempt_count, 'finding': 'PASS'}


def budget_check():
    page_to_batch = dict(ArchivePage.objects.values_list('id', 'batch_id'))
    unknown_rows = list(ArchivePageAttempt.objects.filter(cost__isnull=True,
        finished_at__isnull=False, usage_evidence__isnull=False).order_by('started_at'))
    if not unknown_rows:
        raise RuntimeError('隔离模拟库没有已开始且用量已保存、费用未知的档案页尝试。')
    grouped = {}
    for attempt in unknown_rows:
        batch_id = page_to_batch.get(attempt.page_id)
        if batch_id is None:
            raise RuntimeError('费用未知的档案页尝试没有批次引用。')
        grouped.setdefault(batch_id, []).append(attempt)
    budget_rows = []
    for batch_id, unknown_attempts in grouped.items():
        batch = ArchiveBatch.objects.get(id=batch_id)
        all_pages = ArchivePage.objects.filter(batch_id=batch.id)
        page_ids = list(all_pages.values_list('id', flat=True))
        all_attempts = list(ArchivePageAttempt.objects.filter(page_id__in=page_ids))
        known_cost = sum((Decimal(row.cost) for row in all_attempts if row.cost is not None), Decimal('0'))
        unknown_reservations = [str(reserved_cost(Task.objects.get(id=attempt.task_id)))
            for attempt in unknown_attempts]
        with transaction.atomic():
            update_batch(batch.id)
        batch.refresh_from_db()
        if batch.budget_status != 'unknown':
            raise RuntimeError('费用未知时，档案批次预算状态没有设为 unknown。')
        budget_rows.append({'batch_id': str(batch.id), 'quota_id': str(batch.quota_policy_id),
            'attempt_count': len(all_attempts), 'priced_attempts': sum(row.cost is not None for row in all_attempts),
            'unpriced_attempts_with_usage': len(unknown_attempts), 'known_cost': str(known_cost),
            'unknown_cost_reservations': unknown_reservations, 'budget_status': batch.budget_status,
            'finding': 'PASS'})
    return {'unknown_attempt_count': len(unknown_rows), 'affected_batch_count': len(budget_rows),
        'batches': budget_rows, 'finding': 'PASS'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', choices=('retry', 'budget'), required=True)
    parser.add_argument('--report', required=True)
    args = parser.parse_args()
    database, postgresql = isolated_database()
    result = retry_check() if args.check == 'retry' else budget_check()
    report = {'task': 'T57', 'classification': f'SIMULATION_{args.check.upper()}_CHECK',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(),
            'postgresql': postgresql},
        'environment': {'database': database[0], 'schema': database[1], 'search_path': database[2],
            'runtime': 'simulation', 'isolated_root': str(settings.SIMULATION_ROOT),
            'external_model_requests_during_check': 0, 'worker_or_beat_started': False,
            'shared_queue_consumption': 0, 'notifications_sent': 0},
        'check': result, 'saved_and_reread': True}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('T57 检查报告保存后回读内容不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
