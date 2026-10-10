import argparse
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPErrorProcessor
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection
from django.db import transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from archives.models import ArchiveDocument
from archives.models import ArchivePageAttempt
from files.models import FileObject
from authorization.models import AccessGrant
from identity.models import Patient, PrincipalCredential
from relations.models import Relation
from tasks.models import Task, TaskAttempt
from archives.search_schemas import checked_search


def percentile(values, percentage):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentage) - 1))]


class KeepHttpStatusProcessor(HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


class HttpSession:
    def __init__(self):
        self.opener = build_opener(KeepHttpStatusProcessor)
        self.opener.add_handler(HTTPCookieProcessor(CookieJar()))

    def request(self, method, url, payload=None, headers=None, timeout=30):
        body = json.dumps(payload).encode() if payload is not None else None
        request_headers = dict(headers or {})
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=request_headers, method=method)
        response = self.opener.open(request, timeout=timeout)
        content = response.read()
        return response.status, content


def login(session, base_url):
    status, content = session.request('GET', f'{base_url}/api/auth/bootstrap', timeout=10)
    bootstrap = json.loads(content)
    if status != 200:
        raise RuntimeError(f'模拟会话初始化失败：HTTP {status}')
    csrf = bootstrap['csrf_token']
    status, content = session.request('POST', f'{base_url}/api/auth/login', payload={
        'username': 'simulation-family', 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': csrf}, timeout=10)
    if status != 200:
        raise RuntimeError(f'模拟账号认证失败：HTTP {status}')
    return json.loads(content)['csrf_token']


def query_once(base_url, denied_patient_id=None, include_authorized_search=True):
    session = HttpSession()
    csrf = login(session, base_url)
    status, content = session.request('GET', f'{base_url}/api/relations', timeout=20)
    if status != 200:
        raise RuntimeError(f'本人关联读取失败：HTTP {status}')
    current = next((row for row in json.loads(content)['relations'] if row['status'] == 'active'), None)
    if current is None:
        raise RuntimeError('模拟账号没有有效本人关联。')
    patient_id = current['patient_id']
    targets = []
    if include_authorized_search:
        targets.append(('archives_search', 'POST',
            f'{base_url}/api/v2/patients/{patient_id}/archives/search', {'limit': 20}, 200, None))
    targets.append(('summary_list', 'GET', f'{base_url}/api/patients/{patient_id}/summaries', None, 200, None))
    if denied_patient_id is not None:
        targets.append(('authorization_denied', 'POST',
            f'{base_url}/api/v2/patients/{denied_patient_id}/archives/search', {'limit': 20}, 403, 'CASE_ACTION_DENIED'))
    observations = []
    for name, method, url, payload, expected_status, expected_error_code in targets:
        started = time.perf_counter()
        status, content = session.request(method, url, payload=payload,
            headers={'X-CSRFToken': csrf} if method == 'POST' else None, timeout=30)
        elapsed_ms = (time.perf_counter() - started) * 1000
        response_body = json.loads(content)
        schema_valid = None
        if name == 'archives_search' and status == 200:
            checked_search('ArchiveSearchView', response_body)
            schema_valid = True
        error_code = response_body.get('code') if status >= 400 else None
        observations.append({'name': name, 'status': status, 'elapsed_ms': elapsed_ms,
            'response_bytes': len(content), 'error_code': error_code,
            'schema_valid': schema_valid,
            'expected_status': expected_status, 'expected_error_code': expected_error_code,
            'success': status == expected_status and (expected_error_code is None or error_code == expected_error_code)
                and (schema_valid is not False)})
    session.request('POST', f'{base_url}/api/session/logout', headers={'X-CSRFToken': csrf}, timeout=10)
    return observations


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--requests', type=int, default=20)
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--authorization-only', action='store_true')
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-simulation-report.json'))
    args = parser.parse_args()
    if settings.DATABASES['default']['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation':
        raise RuntimeError('只允许在 sjia_simulation 模拟配置中执行。')
    if args.requests < 1 or args.concurrency < 1 or args.concurrency > args.requests:
        raise ValueError('请求数和并发数范围无效。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        database_identity = cursor.fetchone()
        cursor.execute('SHOW server_version')
        postgres_version = cursor.fetchone()[0]
        cursor.execute("SELECT current_setting('max_connections')::integer, current_setting('superuser_reserved_connections')::integer, count(*) FROM pg_stat_activity WHERE datname = current_database()")
        connection_budget = cursor.fetchone()
        cursor.execute('SELECT pg_database_size(current_database())')
        database_bytes = cursor.fetchone()[0]
        if database_identity != ('sjia_simulation', 'sjia', 'sjia,public'):
            raise RuntimeError('数据库身份与模拟范围不符。')
        query = ArchiveDocument.objects.order_by('id').query
        sql, parameters = query.sql_with_params()
        cursor.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql, parameters)
        query_plan = cursor.fetchone()[0][0]
    principal = PrincipalCredential.objects.get(username='simulation-family').principal
    active_relation = Relation.objects.filter(guardian=principal, status='active').order_by('id').first()
    if active_relation is None:
        raise RuntimeError('模拟账号没有有效本人关联。')
    related_patient_ids = Relation.objects.filter(guardian=principal, status='active').values_list('patient_id', flat=True)
    denied_patient = Patient.objects.filter(status='active').exclude(id__in=related_patient_ids).order_by('id').values_list('id', flat=True).first()
    if args.authorization_only and denied_patient is None:
        raise RuntimeError('模拟库中没有与当前账号无关联的患儿，无法完成授权拒绝路径检查。')
    grant_query = AccessGrant.objects.filter(principal=principal, patient_id=active_relation.patient_id,
        action='read_raw', purpose='family_observation').order_by('-valid_from')
    grant_sql, grant_parameters = grant_query.query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + grant_sql, grant_parameters)
        authorization_query_plan = cursor.fetchone()[0][0]
    plan_client = Client()
    login_response = plan_client.post('/api/auth/login', data=json.dumps({
        'username': 'simulation-family', 'password': os.environ['SJIA_SIMULATION_PASSWORD']}),
        content_type='application/json')
    if login_response.status_code != 200:
        raise RuntimeError(f'查询计划采集的模拟会话认证失败：HTTP {login_response.status_code}')
    with CaptureQueriesContext(connection) as archive_search_queries:
        plan_response = plan_client.post(f'/api/v2/patients/{active_relation.patient_id}/archives/search',
            data=json.dumps({'limit': 20}), content_type='application/json')
    if plan_response.status_code != 200:
        raise RuntimeError(f'查询计划采集的获准档案搜索失败：HTTP {plan_response.status_code}')
    plan_search_result = checked_search('ArchiveSearchView', json.loads(plan_response.content))
    select_queries = []
    seen_sql = set()
    for query in archive_search_queries.captured_queries:
        sql = query['sql'].strip().rstrip(';')
        if (not sql.lower().startswith('select ') or ' for update' in sql.lower()
                or sql in seen_sql):
            continue
        seen_sql.add(sql)
        select_queries.append({'elapsed_ms': float(query['time']) * 1000, 'sql': sql})
    observed_query_plans = []
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            for query in sorted(select_queries, key=lambda value: value['elapsed_ms'], reverse=True)[:5]:
                cursor.execute('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + query['sql'])
                observed_query_plans.append({'observed_query_elapsed_ms': query['elapsed_ms'],
                    'plan': cursor.fetchone()[0][0]})
    started = time.perf_counter()
    rows = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(query_once, args.base_url.rstrip('/'), str(denied_patient) if denied_patient else None,
            not args.authorization_only)
            for _ in range(args.requests)]
        for future in as_completed(futures):
            rows.extend(future.result())
    wall_ms = (time.perf_counter() - started) * 1000
    grouped = {}
    for row in rows:
        grouped.setdefault(row['name'], []).append(row)
    endpoints = {}
    for name, observations in grouped.items():
        times = [row['elapsed_ms'] for row in observations]
        endpoints[name] = {'samples': len(observations), 'successes': sum(row['success'] for row in observations),
            'failures': sum(not row['success'] for row in observations), 'failure_rate': sum(not row['success'] for row in observations) / len(observations),
            'p95_ms': percentile(times, .95), 'p99_ms': percentile(times, .99),
            'mean_response_bytes': statistics.mean(row['response_bytes'] for row in observations),
            'schema_valid_responses': sum(row['schema_valid'] is True for row in observations),
            'http_statuses': sorted({row['status'] for row in observations}),
            'error_codes': sorted({row['error_code'] for row in observations if row['error_code']})}
    tasks = list(Task.objects.order_by('created_at').values('id', 'created_at'))
    task_times = {str(row['id']): row['created_at'] for row in tasks}
    queue_wait_ms = [(attempt.started_at - task_times[str(attempt.task_id)]).total_seconds() * 1000
        for attempt in TaskAttempt.objects.filter(task_id__in=task_times, started_at__isnull=False)]
    page_attempts = list(ArchivePageAttempt.objects.order_by('started_at').values('status', 'duration_ms', 'cost', 'currency'))
    durations = [row['duration_ms'] for row in page_attempts if row['duration_ms'] is not None]
    costs = [row for row in page_attempts if row['cost'] is not None]
    file_shapes = {}
    file_rows = list(FileObject.objects.filter(purpose='original', status='available').values('media_type', 'size_bytes'))
    for row in file_rows:
        key = f"{row['media_type']}:{row['size_bytes']}"
        file_shapes[key] = file_shapes.get(key, 0) + 1
    asset_files = [path for path in (ROOT / 'guardian-h5/dist').rglob('*') if path.is_file()]
    assets = {'file_count': len(asset_files), 'total_bytes': sum(path.stat().st_size for path in asset_files),
        'largest_assets': sorted([{'name': path.relative_to(ROOT / 'guardian-h5/dist').as_posix(), 'bytes': path.stat().st_size}
            for path in asset_files], key=lambda value: value['bytes'], reverse=True)[:10]}
    report = {'task': 'T31', 'classification': 'SIMULATION_MEASUREMENT_ONLY', 'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(), 'postgresql': postgres_version},
        'database': {'name': database_identity[0], 'schema': database_identity[1], 'search_path': database_identity[2],
            'size_bytes': database_bytes, 'max_connections': connection_budget[0],
            'superuser_reserved_connections': connection_budget[1], 'observed_sessions_before_load': connection_budget[2]},
        'load': {'simulation_target': {'requests': args.requests, 'concurrency': args.concurrency},
            'requests': args.requests, 'concurrency': args.concurrency, 'simulated_file_shapes': file_shapes,
            'simulated_original_file_count': len(file_rows),
            'simulated_original_bytes': sum(row['size_bytes'] for row in file_rows),
            'elapsed_wall_ms': wall_ms, 'actual_api_operations': len(rows), 'endpoints': endpoints,
            'authorization_only': args.authorization_only,
            'authorized_archive_search': 'NOT_RUN_AUTHORIZATION_ONLY' if args.authorization_only else 'MEASURED'},
        'query_plan': query_plan, 'authorization_query_plan': authorization_query_plan,
        'archive_search_query_capture': {'request_status': plan_response.status_code,
            'response_schema_valid': True, 'matched_count': plan_search_result['matched_count'],
            'captured_select_query_count': len(select_queries),
            'explained_query_count': len(observed_query_plans), 'plans_ranked_by_captured_query_time': observed_query_plans},
        'authorization_probe': {'authorized_patient': 'MEASURED_IN_ARCHIVES_SEARCH',
            'unrelated_patient_available': denied_patient is not None,
            'denied_patient_probe': 'MEASURED' if denied_patient else 'NOT_RUN_NO_UNRELATED_SIMULATION_PATIENT'},
        'archive_document_rows': ArchiveDocument.objects.count(),
        'database_connection_budget': {'configured_django_connection_age_seconds': settings.DATABASES['default']['CONN_MAX_AGE'],
            'maximum_connections': connection_budget[0], 'sessions_before_load': connection_budget[2],
            'workers_started_by_measurement': 0, 'broker_consumption': 0},
        'queue_wait': {'observed_attempts': len(queue_wait_ms), 'p95_ms': percentile(queue_wait_ms, .95) if queue_wait_ms else None,
            'p99_ms': percentile(queue_wait_ms, .99) if queue_wait_ms else None},
        'archive_page_execution': {'attempts': len(page_attempts), 'measured_durations': len(durations),
            'p95_ms': percentile(durations, .95) if durations else None, 'p99_ms': percentile(durations, .99) if durations else None,
            'known_cost_attempts': len(costs), 'known_cost_total': str(sum((row['cost'] for row in costs), start=0)),
            'currencies': sorted({row['currency'] for row in costs if row['currency']})},
        'frontend_resources': assets, 'external_provider_calls_during_measurement': 0,
        'frontend_runtime_measurements': {'first_visit': 'NOT_RUN', 'repeat_visit': 'NOT_RUN',
            'main_thread': 'NOT_RUN', 'memory': 'NOT_RUN', 'reason': '本工具只读取静态构建产物，未启动可视浏览器或设备。'},
        'summary_generation_time': 'NOT_RUN', 'model_execution_time': 'NOT_RUN',
        'approved_capacity_target': 'NOT_PROVIDED', 'real_capacity': 'NOT_RUN', 'clinical_scale': 'NOT_RUN',
        'saved_and_reread': True}
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    reread = json.loads(path.read_text())
    if reread != report:
        raise RuntimeError('测量报告保存后回读内容不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
