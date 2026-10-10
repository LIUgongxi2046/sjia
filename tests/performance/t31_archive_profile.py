import argparse
import cProfile
import hashlib
import io
import json
import os
import pstats
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from authorization.models import AccessGrant
from identity.models import PrincipalCredential
from relations.models import Relation
from archives.search_schemas import checked_search


PROFILE_TARGETS = {
    'configuration_qualification_reason', 'deployed_artifact_checksum',
    'inspect_approved_evidence', 'inspect_validation_report',
    'read_bytes', 'open', 'read', 'ArchiveSearchContext', 'archive_search',
}


def source_hashes():
    paths = ('backend/archives/search.py', 'backend/archives/search_context.py',
        'backend/configurations/qualification.py', 'backend/configurations/evidence.py')
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def function_row(key, values):
    filename, line_number, function = key
    path = Path(filename)
    if path.is_absolute() and path.is_relative_to(ROOT):
        filename = path.relative_to(ROOT).as_posix()
    else:
        filename = path.name
    primitive_calls, total_calls, self_seconds, cumulative_seconds, _ = values
    return {'function': function, 'file': filename, 'line': line_number,
        'calls': total_calls, 'self_time_ms': self_seconds * 1000,
        'cumulative_time_ms': cumulative_seconds * 1000}


def percentile(values, percentage):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int((len(ordered) * percentage + 0.999999999)))) - 1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-archive-profile-simulation-report.json'))
    args = parser.parse_args()
    if (settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or settings.SJIA_ENVIRONMENT != 'simulation'
            or settings.DATABASES['default']['HOST'] not in ('127.0.0.1', 'localhost')):
        raise RuntimeError('T31 请求剖析只允许连接本机 sjia_simulation 模拟数据库。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        database_identity = cursor.fetchone()
        cursor.execute('SHOW server_version')
        postgres_version = cursor.fetchone()[0]
        cursor.execute("SELECT current_setting('max_connections')::integer, current_setting('superuser_reserved_connections')::integer, count(*) FROM pg_stat_activity WHERE datname = current_database()")
        connection_budget = cursor.fetchone()
    if (database_identity[0:2] != ('sjia_simulation', 'sjia')
            or database_identity[2].replace(' ', '') != 'sjia,public'):
        raise RuntimeError('T31 请求剖析数据库身份不符。')
    credential = PrincipalCredential.objects.get(username='simulation-family')
    relation = Relation.objects.filter(guardian=credential.principal, status='active').order_by('id').first()
    if relation is None:
        raise RuntimeError('模拟家庭账号没有有效本人关系。')
    if not AccessGrant.objects.filter(principal=credential.principal, patient_id=relation.patient_id,
            action='read_raw', purpose='family_observation', revoked_at__isnull=True).exists():
        raise RuntimeError('模拟家庭账号没有档案读取授权。')
    client = Client()
    login = client.post('/api/auth/login', data=json.dumps({'username': credential.username,
        'password': os.environ['SJIA_SIMULATION_PASSWORD']}), content_type='application/json')
    if login.status_code != 200:
        raise RuntimeError(f'模拟家庭账号登录失败：HTTP {login.status_code}')
    route = f'/api/v2/patients/{relation.patient_id}/archives/search'
    profiler = cProfile.Profile()
    with CaptureQueriesContext(connection) as captured:
        started = time.perf_counter()
        profiler.enable()
        response = client.post(route, data=json.dumps({'limit': 20}), content_type='application/json')
        profiler.disable()
        elapsed_ms = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise RuntimeError(f'获准档案搜索失败：HTTP {response.status_code} {response.content.decode()}')
    body = json.loads(response.content)
    result = checked_search('ArchiveSearchView', body)
    query_times = [float(query['time']) * 1000 for query in captured.captured_queries]
    select_count = sum(query['sql'].lstrip().lower().startswith('select ')
        for query in captured.captured_queries)
    profile_stats = pstats.Stats(profiler)
    top_functions = [function_row(key, values) for key, values in
        sorted(profile_stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:40]]
    targeted = [function_row(key, values) for key, values in profile_stats.stats.items()
        if key[2] in PROFILE_TARGETS or key[2] == '__init__' and key[0].endswith('/archives/search_context.py')]
    targeted.sort(key=lambda row: row['cumulative_time_ms'], reverse=True)
    query_rows = sorted(query_times, reverse=True)
    report = {'task': 'T31', 'classification': 'SIMULATION_SINGLE_REQUEST_CPROFILE',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(),
            'postgresql': postgres_version},
        'environment': {'database': database_identity[0], 'schema': database_identity[1],
            'search_path': database_identity[2], 'database_port': settings.DATABASES['default']['PORT'],
            'runtime': 'simulation', 'simulation_root': str(settings.SIMULATION_ROOT),
            'account': credential.username, 'model_requests': 0, 'worker_or_beat_started': False,
            'queue_consumption': 0, 'notifications_sent': 0},
        'request': {'method': 'POST', 'path': route, 'http_status': response.status_code,
            'response_schema': 'ArchiveSearchView', 'response_schema_valid': True,
            'matched_count': result['matched_count'], 'response_bytes': len(response.content),
            'request_count': 1, 'session_login_outside_profile': True,
            'profiled_request_elapsed_ms': elapsed_ms,
            'profile_instrumentation': 'cProfile and CaptureQueriesContext'},
        'database': {'max_connections': connection_budget[0],
            'reserved_connections': connection_budget[1], 'sessions_before_request': connection_budget[2],
            'captured_queries': len(captured.captured_queries), 'select_queries': select_count,
            'sum_query_elapsed_ms': sum(query_times),
            'max_query_elapsed_ms': max(query_times, default=0),
            'query_elapsed_p95_ms': percentile(query_times, .95) if query_times else 0,
            'top_query_elapsed_ms': query_rows[:10]},
        'profile': {'total_calls': profile_stats.total_calls,
            'primitive_calls': profile_stats.prim_calls,
            'top_functions_by_cumulative_time': top_functions,
            'qualification_and_file_io_functions': targeted},
        'source_sha256': source_hashes(),
        'saved_and_reread': True,
        'scope': {'simulated_data_only': True, 'real_capacity': 'NOT_RUN',
            'clinical_scale': 'NOT_RUN'}}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('T31 请求剖析报告保存后回读不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
