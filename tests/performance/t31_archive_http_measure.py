import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPErrorProcessor, HTTPCookieProcessor, Request, build_opener
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from archives.search_schemas import VALIDATORS


SAMPLE_COUNT = 20
QUERY = {'limit': 20}


class KeepHttpStatusProcessor(HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


class HttpSession:
    def __init__(self):
        self.opener = build_opener(KeepHttpStatusProcessor)
        self.opener.add_handler(HTTPCookieProcessor(CookieJar()))

    def request(self, method, url, payload=None, headers=None, timeout=30):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=request_headers, method=method)
        response = self.opener.open(request, timeout=timeout)
        return response.status, response.read()


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))]


def validate_environment(base_url):
    parsed = urlsplit(base_url)
    database = settings.DATABASES['default']
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.port != 8001 or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise RuntimeError('T31 档案 HTTP 测量只允许本机主窗口模拟 API 端口 8001。')
    if (database['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation'
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise RuntimeError('T31 档案 HTTP 测量只允许本机 sjia_simulation 模拟数据库。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        identity = cursor.fetchone()
        cursor.execute('SHOW server_version')
        server_version = cursor.fetchone()[0]
    if identity != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise RuntimeError('T31 档案 HTTP 测量数据库身份不符。')
    return parsed.geturl().rstrip('/'), server_version


def login(session, base_url):
    status, content = session.request('GET', f'{base_url}/api/auth/bootstrap', timeout=10)
    if status != 200:
        raise RuntimeError(f'模拟会话初始化失败：HTTP {status}')
    bootstrap = json.loads(content)
    csrf_token = bootstrap['csrf_token']
    status, content = session.request('POST', f'{base_url}/api/auth/login',
        payload={'username': 'simulation-family', 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    if status != 200:
        raise RuntimeError(f'模拟账号认证失败：HTTP {status}')
    return json.loads(content)['csrf_token']


def authorized_patient(session, base_url):
    status, content = session.request('GET', f'{base_url}/api/relations', timeout=10)
    if status != 200:
        raise RuntimeError(f'本人关联读取失败：HTTP {status}')
    relation_rows = json.loads(content)['relations']
    current_time = now()
    active = [row for row in relation_rows if row['status'] == 'active'
        and parse_datetime(row['valid_from']) <= current_time
        and (row['valid_until'] is None or parse_datetime(row['valid_until']) > current_time)]
    if not active:
        raise RuntimeError('模拟账号没有有效本人关联。')
    return active[0]['patient_id']


def measure_one(session, endpoint, patient_id, csrf_token, expected_digest):
    started = time.perf_counter()
    status, content = session.request('POST', endpoint, payload=QUERY,
        headers={'X-CSRFToken': csrf_token}, timeout=30)
    elapsed_ms = (time.perf_counter() - started) * 1000
    payload = json.loads(content)
    schema_errors = list(VALIDATORS['ArchiveSearchView'].iter_errors(payload))
    items = payload.get('items') if isinstance(payload, dict) else None
    digest = None
    if isinstance(items, list):
        canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
        digest = hashlib.sha256(canonical).hexdigest()
    schema_valid = not schema_errors
    expected_digest_match = expected_digest is None or digest == expected_digest
    successful = status == 200 and schema_valid and isinstance(items, list)
    return {
        'sample': None,
        'http_status': status,
        'elapsed_ms': elapsed_ms,
        'response_bytes': len(content),
        'error_code': payload.get('code') if isinstance(payload, dict) and status >= 400 else None,
        'schema_valid': schema_valid,
        'schema_error': schema_errors[0].message if schema_errors else None,
        'matched_count': payload.get('matched_count') if isinstance(payload, dict) else None,
        'item_count': len(items) if isinstance(items, list) else None,
        'items_sha256': digest,
        'matches_first_response_items': expected_digest_match,
        'successful': successful,
    }


def source_hashes():
    paths = ('backend/archives/search.py', 'backend/archives/search_context.py',
        'backend/configurations/qualification.py', 'backend/configurations/evidence.py')
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-archive-http-simulation-report.json'))
    args = parser.parse_args()
    base_url, postgres_version = validate_environment(args.base_url)
    session = HttpSession()
    csrf_token = login(session, base_url)
    patient_id = authorized_patient(session, base_url)
    endpoint = f'{base_url}/api/v2/patients/{patient_id}/archives/search'
    results = []
    expected_digest = None
    for sample_number in range(1, SAMPLE_COUNT + 1):
        result = measure_one(session, endpoint, patient_id, csrf_token, expected_digest)
        result['sample'] = sample_number
        if sample_number == 1:
            expected_digest = result['items_sha256']
        results.append(result)
    login_status, _ = session.request('POST', f'{base_url}/api/session/logout', payload={},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    times = [row['elapsed_ms'] for row in results]
    successes = sum(row['successful'] and row['matches_first_response_items'] for row in results)
    failures = SAMPLE_COUNT - successes
    report = {
        'task': 'T31',
        'classification': 'SIMULATION_ARCHIVE_HTTP_FIXED_QUERY',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(), 'postgresql': postgres_version},
        'target': {'api_base_url': base_url, 'database': 'sjia_simulation', 'schema': 'sjia',
            'account': 'simulation-family', 'simulation_only': True, 'external_requests': 0},
        'authentication': {'login_count_outside_samples': 1, 'login_status': 200,
            'relation_read_count_outside_samples': 1, 'logout_status': login_status},
        'query': {'method': 'POST', 'path': '/api/v2/patients/{authorized_patient_id}/archives/search',
            'body': QUERY, 'query_sha256': hashlib.sha256(json.dumps(QUERY, sort_keys=True).encode()).hexdigest(),
            'samples': SAMPLE_COUNT, 'concurrency': 1, 'reused_authenticated_session': True},
        'response': {'successes': successes, 'failures': failures, 'failure_rate': failures / SAMPLE_COUNT,
            'http_statuses': sorted({row['http_status'] for row in results}),
            'schema_valid_count': sum(row['schema_valid'] for row in results),
            'all_items_match_first_response': all(row['matches_first_response_items'] for row in results),
            'matched_count_values': sorted({row['matched_count'] for row in results if row['matched_count'] is not None}),
            'item_count_values': sorted({row['item_count'] for row in results if row['item_count'] is not None}),
            'items_sha256_values': sorted({row['items_sha256'] for row in results if row['items_sha256'] is not None}),
            'mean_response_bytes': sum(row['response_bytes'] for row in results) / SAMPLE_COUNT},
        'latency_ms': {'p50': percentile(times, 0.50), 'p95': percentile(times, 0.95), 'p99': percentile(times, 0.99),
            'minimum': min(times), 'maximum': max(times), 'samples': times,
            'measurement_scope': 'HTTP request round trip only; schema validation excluded'},
        'samples': results,
        'source_sha256': source_hashes(),
        'side_effects': {'records_written': 0, 'worker_or_beat_started': False, 'queue_consumption': 0,
            'provider_calls': 0, 'notifications_sent': 0},
        'scope': {'real_capacity': 'NOT_RUN', 'clinical_scale': 'NOT_RUN', 'approved_capacity_target': 'NOT_PROVIDED'},
        'saved_and_reread': True,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('档案 HTTP 测量报告保存后回读不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
