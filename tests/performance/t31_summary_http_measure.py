import argparse
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
import uuid
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
from django.db import connection
from django.utils.dateparse import parse_datetime
from django.utils.timezone import now
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from records.models import Record, RecordVersion
from records.service import read_version
from summaries.models import Summary, SummaryItem


SAMPLE_COUNT = 20
UUID_PATTERN = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$')


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


def canonical_sha256(value):
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def validate_environment(base_url):
    parsed = urlsplit(base_url)
    database = settings.DATABASES['default']
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.port != 8001 or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise RuntimeError('T31 摘要 HTTP 测量只允许本机主窗口模拟 API 端口 8001。')
    if (database['NAME'] != 'sjia_simulation' or settings.SJIA_ENVIRONMENT != 'simulation'
            or database['HOST'] not in ('127.0.0.1', 'localhost')):
        raise RuntimeError('T31 摘要 HTTP 测量只允许本机 sjia_simulation 模拟数据库。')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), current_setting(%s)', ['search_path'])
        identity = cursor.fetchone()
        cursor.execute('SHOW server_version')
        server_version = cursor.fetchone()[0]
    if identity != ('sjia_simulation', 'sjia', 'sjia,public'):
        raise RuntimeError('T31 摘要 HTTP 测量数据库身份不符。')
    return parsed.geturl().rstrip('/'), server_version


def load_validators():
    summary_schema = json.loads((ROOT / 'backend' / 'summaries' / 'read.schema.json').read_text())
    record_schema = json.loads((ROOT / 'backend' / 'records' / 'search.schema.json').read_text())
    registry = Registry().with_resource('record-search.schema.json', Resource.from_contents(record_schema))
    Draft202012Validator.check_schema(summary_schema)
    common = {'registry': registry, 'format_checker': FormatChecker()}
    list_validator = Draft202012Validator({**summary_schema, '$ref': '#/$defs/SummaryList'}, **common)
    detail_validator = Draft202012Validator({**summary_schema, '$ref': '#/$defs/SummaryView'}, **common)
    return list_validator, detail_validator


def login(session, base_url, account):
    status, content = session.request('GET', f'{base_url}/api/auth/bootstrap', timeout=10)
    if status != 200:
        raise RuntimeError(f'模拟会话初始化失败：HTTP {status}')
    bootstrap = json.loads(content)
    csrf_token = bootstrap['csrf_token']
    status, content = session.request('POST', f'{base_url}/api/auth/login',
        payload={'username': account, 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    if status != 200:
        raise RuntimeError(f'模拟账号认证失败：HTTP {status}')
    return json.loads(content)['csrf_token']


def authorized_patient(session, base_url, expected_patient_id):
    status, content = session.request('GET', f'{base_url}/api/relations', timeout=10)
    if status != 200:
        raise RuntimeError(f'本人关联读取失败：HTTP {status}')
    relation_rows = json.loads(content)['relations']
    current_time = now()
    active = [row for row in relation_rows if row['status'] == 'active'
        and parse_datetime(row['valid_from']) <= current_time
        and (row['valid_until'] is None or parse_datetime(row['valid_until']) > current_time)]
    matching = [row for row in active if row['patient_id'] == expected_patient_id]
    if len(matching) != 1:
        raise RuntimeError('模拟账号与指定患儿之间没有唯一有效关联。')
    return matching[0]['patient_id']


def validate_body(validator, body):
    errors = list(validator.iter_errors(body))
    return errors


def summary_references_valid(summary):
    for item in summary['items']:
        snapshot = item['snapshot']
        if item['record_version_id'] != snapshot['record_version_id']:
            return False
        if item['field_id'] != snapshot['field']['field_id']:
            return False
    return True


def verify_saved_summary_content(payload, expected_patient_id, summary_id):
    summary = Summary.objects.filter(id=summary_id, patient_id=expected_patient_id).first()
    if summary is None:
        raise RuntimeError('数据库中没有该患儿的指定摘要记录。')
    saved_items = list(SummaryItem.objects.filter(summary_id=summary_id,
        patient_id=expected_patient_id).order_by('ordinal'))
    returned_items = payload['items']
    if not returned_items or len(returned_items) != len(saved_items):
        return {'valid': False, 'item_count': len(returned_items), 'saved_item_count': len(saved_items),
            'matched_items': 0, 'content_matches': 0, 'items': []}
    versions = {}
    for version_id in {item.record_version_id for item in saved_items}:
        version = RecordVersion.objects.filter(id=version_id,
            patient_id=expected_patient_id).first()
        if version is None:
            raise RuntimeError('摘要引用的记录版本不存在或属于其他患儿。')
        record = Record.objects.filter(id=version.record_id, patient_id=expected_patient_id).first()
        if record is None:
            raise RuntimeError('摘要引用的记录不存在或属于其他患儿。')
        versions[version.id] = (version, record, read_version(version, record))
    item_results = []
    matched_items = 0
    content_matches = 0
    for returned, saved in zip(returned_items, saved_items, strict=True):
        version, record, record_view = versions[saved.record_version_id]
        snapshot = returned['snapshot']
        actual_fields = {field['field_id']: field for field in record_view['fields']}
        actual_field = actual_fields.get(str(saved.field_id))
        actual_metadata = {'record_id': str(record.id), 'record_version_id': str(version.id),
            'kind': record_view['kind'], 'origin_type': record_view['origin_type'],
            'source_organization': record_view['source_organization'],
            'quality_status': record_view['quality_status']}
        reference_match = (returned['record_version_id'] == str(saved.record_version_id)
            and returned['field_id'] == str(saved.field_id)
            and returned['ordinal'] == saved.ordinal
            and saved.record_version_id == version.id
            and saved.snapshot == {**actual_metadata, **snapshot['field']}
            and {key: snapshot[key] for key in actual_metadata} == actual_metadata
            and actual_field is not None)
        content_match = reference_match and snapshot['field'] == actual_field
        matched_items += int(reference_match)
        content_matches += int(content_match)
        item_results.append({'ordinal': returned['ordinal'],
            'record_version_id': returned['record_version_id'], 'field_id': returned['field_id'],
            'snapshot_content_sha256': canonical_sha256(snapshot),
            'record_version_reference_valid': reference_match,
            'field_content_matches_saved_record_version': content_match})
    return {'valid': matched_items == len(saved_items) and content_matches == len(saved_items),
        'item_count': len(returned_items), 'saved_item_count': len(saved_items),
        'matched_items': matched_items, 'content_matches': content_matches, 'items': item_results}


def measure_list(session, endpoint, expected_patient_id, summary_id, validator, sample_number, first_digest):
    started = time.perf_counter()
    status, content = session.request('GET', endpoint, timeout=30)
    elapsed_ms = (time.perf_counter() - started) * 1000
    payload = json.loads(content)
    errors = validate_body(validator, payload) if status == 200 else []
    summaries = payload.get('summaries') if isinstance(payload, dict) else None
    matching = [row for row in summaries or [] if row.get('summary_id') == summary_id]
    identity_valid = payload.get('patient_id') == expected_patient_id if isinstance(payload, dict) else False
    contains_target = len(matching) == 1 and matching[0].get('patient_id') == expected_patient_id
    digest = canonical_sha256(payload) if isinstance(payload, dict) else None
    schema_valid = status == 200 and not errors
    successful = schema_valid and identity_valid and contains_target
    return {
        'sample': sample_number, 'http_status': status, 'elapsed_ms': elapsed_ms,
        'response_bytes': len(content), 'schema_valid': schema_valid,
        'schema_error': errors[0].message if errors else None,
        'patient_identity_valid': identity_valid, 'target_summary_present_once': contains_target,
        'summary_count': len(summaries) if isinstance(summaries, list) else None,
        'target_summary_sha256': canonical_sha256(matching[0]) if contains_target else None,
        'response_sha256': digest,
        'matches_first_response': first_digest is None or digest == first_digest,
        'successful': successful,
    }


def measure_detail(session, endpoint, expected_patient_id, summary_id, validator, sample_number, first_digest, source_verification):
    started = time.perf_counter()
    status, content = session.request('GET', endpoint, timeout=30)
    elapsed_ms = (time.perf_counter() - started) * 1000
    payload = json.loads(content)
    errors = validate_body(validator, payload) if status == 200 else []
    identity_valid = (payload.get('summary_id') == summary_id
        and payload.get('patient_id') == expected_patient_id) if isinstance(payload, dict) else False
    references_valid = summary_references_valid(payload) if status == 200 and not errors else False
    regeneration_clear = payload.get('needs_regeneration') is False if isinstance(payload, dict) else False
    if sample_number == 1 and status == 200 and not errors:
        source_verification.update(verify_saved_summary_content(payload, expected_patient_id, summary_id))
    source_valid = source_verification.get('valid', False)
    digest = canonical_sha256(payload) if isinstance(payload, dict) else None
    schema_valid = status == 200 and not errors
    successful = schema_valid and identity_valid and regeneration_clear and references_valid and source_valid
    return {
        'sample': sample_number, 'http_status': status, 'elapsed_ms': elapsed_ms,
        'response_bytes': len(content), 'schema_valid': schema_valid,
        'schema_error': errors[0].message if errors else None,
        'summary_and_patient_identity_valid': identity_valid,
        'needs_regeneration_false': regeneration_clear,
        'record_version_and_field_references_valid': references_valid,
        'saved_database_and_record_field_content_valid': source_valid,
        'verified_saved_item_count': source_verification.get('content_matches'),
        'summary_item_count': len(payload['items']) if isinstance(payload, dict) and isinstance(payload.get('items'), list) else None,
        'response_sha256': digest,
        'matches_first_response': first_digest is None or digest == first_digest,
        'successful': successful,
    }


def source_hashes():
    paths = ('backend/api/views_family2.py', 'backend/summaries/service.py',
        'backend/summaries/read.schema.json', 'backend/records/search.schema.json')
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in paths}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--summary-id', required=True)
    parser.add_argument('--patient-id', required=True)
    parser.add_argument('--account', choices=('simulation-family', 'simulation-family-b'), default='simulation-family')
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--report', default=str(ROOT / 'docs/testing/T31-summary-http-simulation-report.json'))
    args = parser.parse_args()
    try:
        summary_id = str(uuid.UUID(args.summary_id))
    except ValueError as error:
        raise ValueError('--summary-id 必须是有效 UUID。') from error
    if not UUID_PATTERN.fullmatch(args.summary_id):
        raise ValueError('--summary-id 必须使用标准 UUID 格式。')
    patient_id = str(uuid.UUID(args.patient_id))
    base_url, postgres_version = validate_environment(args.base_url)
    list_validator, detail_validator = load_validators()
    session = HttpSession()
    csrf_token = login(session, base_url, args.account)
    patient_id = authorized_patient(session, base_url, patient_id)
    list_endpoint = f'{base_url}/api/patients/{patient_id}/summaries'
    detail_endpoint = f'{base_url}/api/summaries/{summary_id}'
    list_results = []
    detail_results = []
    first_list_digest = None
    first_target_digest = None
    first_detail_digest = None
    source_verification = {}
    for sample_number in range(1, SAMPLE_COUNT + 1):
        list_result = measure_list(session, list_endpoint, patient_id, summary_id,
            list_validator, sample_number, first_list_digest)
        if sample_number == 1:
            if not list_result['successful']:
                raise RuntimeError('准确 summary_id 未出现在该模拟账号获准患儿的摘要列表中。')
            first_list_digest = list_result['response_sha256']
            first_target_digest = list_result['target_summary_sha256']
        list_result['target_summary_matches_first'] = list_result['target_summary_sha256'] == first_target_digest
        list_results.append(list_result)
        detail_result = measure_detail(session, detail_endpoint, patient_id, summary_id,
            detail_validator, sample_number, first_detail_digest, source_verification)
        if sample_number == 1:
            if not detail_result['successful']:
                raise RuntimeError('摘要详情的 Schema、患儿标识或来源引用检查失败。')
            first_detail_digest = detail_result['response_sha256']
        detail_results.append(detail_result)
    logout_status, _ = session.request('POST', f'{base_url}/api/session/logout', payload={},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    list_times = [row['elapsed_ms'] for row in list_results]
    detail_times = [row['elapsed_ms'] for row in detail_results]
    list_successes = sum(row['successful'] and row['target_summary_matches_first'] for row in list_results)
    detail_successes = sum(row['successful'] and row['matches_first_response'] for row in detail_results)
    list_failures = SAMPLE_COUNT - list_successes
    detail_failures = SAMPLE_COUNT - detail_successes
    report = {
        'task': 'T31', 'classification': 'SIMULATION_SUMMARY_HTTP_LIST_AND_DETAIL',
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'runtime_versions': {'python': sys.version.split()[0], 'django': django.get_version(), 'postgresql': postgres_version},
        'target': {'api_base_url': base_url, 'database': 'sjia_simulation', 'schema': 'sjia',
            'account': args.account, 'simulation_only': True, 'external_requests': 0},
        'authentication': {'login_count_outside_samples': 1, 'login_status': 200,
            'relation_read_count_outside_samples': 1, 'logout_status': logout_status},
        'input': {'summary_id': summary_id, 'patient_id': patient_id,
            'summary_was_created_by_this_tool': False, 'exact_id_required_before_any_api_call': True},
        'load': {'samples_per_endpoint': SAMPLE_COUNT, 'concurrency': 1,
            'operations': {'summary_list': len(list_results), 'summary_detail': len(detail_results)},
            'shared_authenticated_session': True, 'same_patient_and_summary_for_all_samples': True},
        'summary_list': {'path': '/api/patients/{authorized_patient_id}/summaries',
            'successes': list_successes, 'failures': list_failures, 'failure_rate': list_failures / SAMPLE_COUNT,
            'http_statuses': sorted({row['http_status'] for row in list_results}),
            'schema_valid_count': sum(row['schema_valid'] for row in list_results),
            'target_present_once_count': sum(row['target_summary_present_once'] for row in list_results),
            'all_responses_match_first': all(row['matches_first_response'] for row in list_results),
            'all_target_summaries_match_first': all(row['target_summary_matches_first'] for row in list_results),
            'summary_count_values': sorted({row['summary_count'] for row in list_results if row['summary_count'] is not None}),
            'response_bytes_mean': statistics.mean(row['response_bytes'] for row in list_results)},
        'summary_detail': {'path': '/api/summaries/{summary_id}',
            'successes': detail_successes, 'failures': detail_failures, 'failure_rate': detail_failures / SAMPLE_COUNT,
            'http_statuses': sorted({row['http_status'] for row in detail_results}),
            'schema_valid_count': sum(row['schema_valid'] for row in detail_results),
            'identity_valid_count': sum(row['summary_and_patient_identity_valid'] for row in detail_results),
            'needs_regeneration_false_count': sum(row['needs_regeneration_false'] for row in detail_results),
            'source_references_valid_count': sum(row['record_version_and_field_references_valid'] for row in detail_results),
            'all_responses_match_first': all(row['matches_first_response'] for row in detail_results),
            'item_count_values': sorted({row['summary_item_count'] for row in detail_results if row['summary_item_count'] is not None}),
            'response_bytes_mean': statistics.mean(row['response_bytes'] for row in detail_results)},
        'latency_ms': {
            'summary_list': {'p50': percentile(list_times, .50), 'p95': percentile(list_times, .95),
                'p99': percentile(list_times, .99), 'minimum': min(list_times), 'maximum': max(list_times), 'samples': list_times},
            'summary_detail': {'p50': percentile(detail_times, .50), 'p95': percentile(detail_times, .95),
                'p99': percentile(detail_times, .99), 'minimum': min(detail_times), 'maximum': max(detail_times), 'samples': detail_times},
            'measurement_scope': 'HTTP request round trip only; JSON parsing and schema validation excluded'},
        'samples': {'summary_list': list_results, 'summary_detail': detail_results},
        'source_sha256': source_hashes(),
        'side_effects': {'records_written': 0, 'summaries_created': 0, 'worker_or_beat_started': False,
            'queue_consumption': 0, 'provider_calls': 0, 'notifications_sent': 0},
        'scope': {'development_target': 'SIMULATION_ONLY', 'real_capacity': 'NOT_RUN',
            'clinical_scale': 'NOT_RUN', 'approved_capacity_target': 'NOT_PROVIDED'},
        'saved_and_reread': True,
    }
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if json.loads(report_path.read_text()) != report:
        raise RuntimeError('摘要 HTTP 测量报告保存后回读内容不一致。')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
