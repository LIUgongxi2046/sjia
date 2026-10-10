import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPErrorProcessor, HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connections, transaction
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from jsonschema import Draft202012Validator, FormatChecker, RefResolver
from family_content.gate_locks import read_environment_gate_shared


PATIENT_A = 'e88faef5-8bf6-4256-b5a5-865c699103d6'
FAMILY_ACCOUNT = 'simulation-family'


class KeepHttpStatusProcessor(HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


class HttpSession:
    def __init__(self):
        self.opener = build_opener(KeepHttpStatusProcessor, HTTPCookieProcessor(CookieJar()))

    def request(self, method, url, payload=None, headers=None, timeout=30):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=request_headers, method=method)
        response = self.opener.open(request, timeout=timeout)
        return response.status, response.read()


def validate_target(base_url, patient_id):
    parsed = urlsplit(base_url)
    database = settings.DATABASES['default']
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.port != 8001 or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ImproperlyConfigured('T31 API competition is restricted to local API port 8001.')
    if (database['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation'
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise ImproperlyConfigured('T31 API competition is restricted to local sjia_simulation.')
    with connections['default'].cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        identity = cursor.fetchone()
    if identity != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise ImproperlyConfigured('T31 API competition database identity does not match.')
    return parsed.geturl().rstrip('/')


def load_validators():
    schema_path = ROOT / 'backend-design/protocol.schema.json'
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    resolver = RefResolver(base_uri=schema_path.as_uri(), referrer=schema)
    common = {'format_checker': FormatChecker()}
    return {
        name: Draft202012Validator({
            '$schema': schema['$schema'], '$defs': schema['$defs'],
            '$ref': f'#/$defs/{name}',
        }, resolver=resolver, **common)
        for name in ('HomeView', 'FieldConfigView')
    }


def login_and_check_relation(session, base_url, patient_id):
    status, content = session.request('GET', f'{base_url}/api/auth/bootstrap', timeout=10)
    if status != 200:
        raise RuntimeError(f'Simulation bootstrap returned HTTP {status}.')
    csrf_token = json.loads(content)['csrf_token']
    status, content = session.request('POST', f'{base_url}/api/auth/login',
        payload={'username': FAMILY_ACCOUNT, 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    if status != 200:
        raise RuntimeError(f'Simulation family login returned HTTP {status}.')
    status, content = session.request('GET', f'{base_url}/api/relations', timeout=10)
    if status != 200:
        raise RuntimeError(f'Simulation family relation read returned HTTP {status}.')
    relations = json.loads(content)['relations']
    current_time = now()
    active = [relation for relation in relations if relation['patient_id'] == patient_id
        and relation['status'] == 'active'
        and parse_datetime(relation['valid_from']) <= current_time
        and (relation['valid_until'] is None or parse_datetime(relation['valid_until']) > current_time)]
    if len(active) != 1:
        raise RuntimeError('Simulation family account does not have one active relation to patient A.')
    return {'bootstrap_http_status': 200, 'login_http_status': 200,
        'active_relation_count': len(active), 'patient_id': patient_id}


def hold_environment_gate_read(environment, ready, release):
    connections.close_all()
    try:
        with transaction.atomic():
            gate = read_environment_gate_shared(environment)
            if gate.environment != environment:
                raise AssertionError('Shared gate helper returned another environment.')
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                pid = cursor.fetchone()[0]
            ready.update({'pid': pid, 'environment': gate.environment, 'version': gate.version})
            ready['event'].set()
            if not release.wait(60):
                raise TimeoutError('Shared environment gate read lock exceeded its release deadline.')
            transaction.set_rollback(True)
        return {'pid': pid, 'transaction_rolled_back': True}
    finally:
        connections.close_all()
        ready['event'].set()


def measure_endpoint(session, base_url, path, expected_schema, patient_id, validator):
    started = time.perf_counter()
    status, content = session.request('GET', f'{base_url}{path}', timeout=45)
    elapsed_ms = (time.perf_counter() - started) * 1000
    payload = json.loads(content)
    errors = list(validator.iter_errors(payload)) if status == 200 else []
    response_patient_id = payload.get('patient_id') if isinstance(payload, dict) else None
    identity_valid = response_patient_id == patient_id
    identity_source = 'response_patient_id_required_by_HomeView_and_FieldConfigView'
    successful = status == 200 and not errors and identity_valid
    return {'endpoint': path, 'http_status': status, 'elapsed_ms': elapsed_ms,
        'response_bytes': len(content), 'response_sha256': hashlib.sha256(content).hexdigest(),
        'schema': expected_schema, 'schema_valid': status == 200 and not errors,
        'schema_error': errors[0].message if errors else None,
        'response_patient_id_present': response_patient_id is not None,
        'request_patient_path_matches': f'/patients/{patient_id}/' in path,
        'patient_identity_source': identity_source,
        'patient_identity_valid': identity_valid, 'successful': successful}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--patient-id', default=PATIENT_A)
    parser.add_argument('--report', default='docs/testing/T31-environment-gate-api-simulation-report.json')
    args = parser.parse_args()
    base_url = validate_target(args.base_url, args.patient_id)
    validators = load_validators()
    session = HttpSession()
    relation_check = login_and_check_relation(session, base_url, args.patient_id)
    reader_ready = {'event': threading.Event()}
    reader_release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)
    reader = executor.submit(hold_environment_gate_read, settings.SJIA_ENVIRONMENT,
        reader_ready, reader_release)
    endpoint_results = []
    try:
        if not reader_ready['event'].wait(45):
            raise TimeoutError('Production shared gate helper did not hold its PostgreSQL read lock.')
        if 'pid' not in reader_ready:
            reader.result(timeout=1)
            raise AssertionError('Assessment read did not expose its held transaction.')
        endpoint_results.append(measure_endpoint(session, base_url,
            f'/api/patients/{args.patient_id}/home', 'HomeView', args.patient_id, validators['HomeView']))
        endpoint_results.append(measure_endpoint(session, base_url,
            f'/api/patients/{args.patient_id}/field-config?kind=report', 'FieldConfigView',
            args.patient_id, validators['FieldConfigView']))
        reader_release.set()
        reader_result = reader.result(timeout=15)
    finally:
        reader_release.set()
        executor.shutdown(wait=True, cancel_futures=True)
    report_path = (ROOT / args.report).resolve()
    if ROOT not in report_path.parents:
        raise ImproperlyConfigured('Report path must remain inside the project directory.')
    report = {
        'task': 'T31', 'classification': 'SIMULATION_API_READS_DURING_ENVIRONMENT_GATE_READ_LOCK',
        'passed': all(item['successful'] for item in endpoint_results),
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version()},
        'target': {'api': base_url, 'database': 'sjia_simulation', 'schema': 'sjia',
            'environment': settings.SJIA_ENVIRONMENT, 'patient_group': 'A',
            'simulation_only': True, 'credentials': [FAMILY_ACCOUNT]},
        'environment_gate_read_lock': {'environment': reader_ready['environment'],
            'version': reader_ready['version'], 'session_pid': reader_ready['pid'],
            'service': 'family_content.gate_locks.read_environment_gate_shared',
            'held_while_api_requests_ran': True, 'transaction_rolled_back': reader_result['transaction_rolled_back']},
        'full_assessment_read_lock': 'NOT_RUN',
        'family_authorization': relation_check,
        'api_requests': endpoint_results,
        'summary': {'requests': len(endpoint_results),
            'successes': sum(result['successful'] for result in endpoint_results),
            'failures': sum(not result['successful'] for result in endpoint_results),
            'failure_rate': sum(not result['successful'] for result in endpoint_results) / len(endpoint_results),
            'p95_ms': None, 'p99_ms': None,
            'latency_limit_note': '单次端点样本不计算 P95/P99；该结果只验证锁竞争期间请求可完成。'},
        'effects': {'database_writes_committed': 0, 'gate_read_transaction_rolled_back': True,
            'queue_consumption': 0, 'provider_calls': 0, 'external_requests': 0,
            'notifications_or_messages_sent': 0},
        'scope': {'shared_api_instance': '主窗口明确准入的模拟 API 8001',
            'production_capacity': 'NOT_RUN', 'clinical_data': 'NOT_RUN',
            'devices_wechat_visual': 'NOT_RUN'},
        'source_sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
            for path in ('tests/performance/t31_gate_lock_api_measure.py',
                'backend/family_content/gate_locks.py',
                'backend/api/views_family.py', 'backend-design/protocol.schema.json')},
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('T31 API lock report differs from its saved contents.')
    print(json.dumps(report, ensure_ascii=False))
    if not report['passed']:
        raise AssertionError('Home or field configuration API did not pass its measured checks.')


if __name__ == '__main__':
    main()
