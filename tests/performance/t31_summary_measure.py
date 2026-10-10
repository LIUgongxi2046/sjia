import argparse
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'tests' / 'operations'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'window4_simulation_settings')

import django

django.setup()

from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate
from identity.models import PrincipalCredential
from relations.models import Relation
from summaries.models import Summary


def percentile(values, percentage):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentage) - 1))]


def read_summary_list(patient_id, username, password, validator):
    client = Client()
    login = client.post('/api/auth/login', data=json.dumps({
        'username': username, 'password': password}), content_type='application/json')
    if login.status_code != 200:
        raise RuntimeError(f'模拟账号认证失败：HTTP {login.status_code}')
    with CaptureQueriesContext(connection) as captured:
        started = time.perf_counter()
        response = client.get(f'/api/patients/{patient_id}/summaries')
        elapsed_ms = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise RuntimeError(f'摘要目录接口失败：HTTP {response.status_code} {response.content.decode()}')
    body = json.loads(response.content)
    if body.get('patient_id') != str(patient_id) or not isinstance(body.get('summaries'), list):
        raise RuntimeError('摘要目录接口返回的患儿或摘要集合无效。')
    errors = list(validator.iter_errors(body))
    if errors:
        raise RuntimeError('摘要目录响应未通过 SummaryList Schema：' + '; '.join(
            '/'.join(str(part) for part in error.absolute_path) + ':' + error.validator
            for error in errors[:10]))
    select_count = sum(query['sql'].lstrip().lower().startswith('select ')
        for query in captured.captured_queries)
    return {'elapsed_ms': elapsed_ms, 'response_bytes': len(response.content),
        'summary_count': len(body['summaries']), 'schema_valid': True, 'select_count': select_count,
        'http_status': response.status_code}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--requests', type=int, default=20)
    parser.add_argument('--concurrency', type=int, default=4)
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-summary-simulation-report.json'))
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.concurrency > args.requests:
        raise ValueError('请求数和并发数范围无效。')
    simulation_root = Path(settings.SIMULATION_ROOT).resolve()
    isolated_parent = (ROOT / '.planning-work' / 'parallel' / 'window4').resolve()
    if (settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or settings.SJIA_ENVIRONMENT != 'simulation'
            or simulation_root.parent != isolated_parent
            or not simulation_root.name.startswith('isolated')):
        raise RuntimeError('摘要性能检查只能使用窗口四独立模拟数据库。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        database_identity = cursor.fetchone()
        cursor.execute('SHOW server_version')
        postgres_version = cursor.fetchone()[0]
    if database_identity != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise RuntimeError('摘要性能检查数据库身份不符。')
    summary_schema = json.loads((ROOT / 'backend' / 'summaries' / 'read.schema.json').read_text())
    record_schema = json.loads((ROOT / 'backend' / 'records' / 'search.schema.json').read_text())
    registry = Registry().with_resource('record-search.schema.json', Resource.from_contents(record_schema))
    Draft202012Validator.check_schema(summary_schema)
    validator = Draft202012Validator({**summary_schema, '$ref': '#/$defs/SummaryList'},
        registry=registry, format_checker=FormatChecker())
    credential = PrincipalCredential.objects.get(username='simulation-family')
    relation = Relation.objects.filter(guardian=credential.principal, status='active').order_by('id').first()
    if relation is None:
        raise RuntimeError('模拟账号没有有效本人关联。')
    configs = []
    for kind in ('summary', 'visibility'):
        rows = ConfigVersion.objects.filter(kind=kind, schema_version=f'{kind}-1', status='enabled').order_by('id')
        configs.append({'kind': kind, 'enabled_count': rows.count(), 'qualified': [
            {'config_id': str(config.id), 'reason': configuration_qualification_reason(config, 'simulation')}
            for config in rows]})
    gate = EnvironmentGate.objects.filter(environment='simulation').first()
    gate_ready = bool(gate is not None and gate.enabled and gate.active_release_id is not None)
    qualified_counts = {row['kind']: sum(config['reason'] is None for config in row['qualified'])
        for row in configs}
    generation_ready = gate_ready and all(qualified_counts.get(kind) == 1 for kind in ('summary', 'visibility'))
    password = os.environ['SJIA_SIMULATION_PASSWORD']
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(read_summary_list, relation.patient_id, credential.username, password, validator)
            for _ in range(args.requests)]
        for future in as_completed(futures):
            results.append(future.result())
    wall_ms = (time.perf_counter() - started) * 1000
    elapsed = [row['elapsed_ms'] for row in results]
    response_bytes = [row['response_bytes'] for row in results]
    select_counts = [row['select_count'] for row in results]
    saved_summaries = Summary.objects.filter(patient_id=relation.patient_id).count()
    report = {'task': 'T31', 'classification': 'SIMULATION_SUMMARY_QUERY_MEASUREMENT',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(),
            'postgresql': postgres_version},
        'environment': {'database': database_identity[0], 'schema': database_identity[1],
            'search_path': database_identity[2], 'runtime': 'simulation',
            'isolated_root': str(simulation_root), 'shared_queue_consumption': 0,
            'worker_or_beat_started': False, 'external_model_requests': 0,
            'notifications_sent': 0},
        'load': {'requests': args.requests, 'concurrency': args.concurrency,
            'operations': len(results), 'wall_ms': wall_ms, 'failures': 0,
            'failure_rate': 0, 'p95_ms': percentile(elapsed, .95),
            'p99_ms': percentile(elapsed, .99),
            'schema_valid_responses': sum(row['schema_valid'] for row in results),
            'mean_response_bytes': statistics.mean(response_bytes),
            'select_count_min': min(select_counts), 'select_count_max': max(select_counts)},
        'data': {'active_family_relation_count': 1,
            'patient_id': str(relation.patient_id), 'saved_summary_count_before_and_after': saved_summaries,
            'summary_count_per_response': sorted({row['summary_count'] for row in results})},
        'generation_preflight': {'qualified_summary_and_visibility_configs': configs,
            'active_release_ready': gate_ready,
            'active_release_id': str(gate.active_release_id) if gate and gate.active_release_id else None,
            'ready': generation_ready,
            'generation': 'NOT_RUN' if not generation_ready else 'NOT_REQUESTED'},
        'saved_and_reread': True,
        'scope': {'simulated_data_only': True, 'clinical_acceptance': 'NOT_RUN',
            'real_capacity': 'NOT_RUN'}}
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('T31 摘要性能报告保存后回读内容不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
