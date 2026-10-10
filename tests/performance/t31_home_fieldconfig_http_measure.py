import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPErrorProcessor, HTTPCookieProcessor, Request, build_opener

from jsonschema import Draft202012Validator, FormatChecker, RefResolver


ROOT = Path(__file__).resolve().parents[2]
PATIENTS = {
    'A': ('simulation-family', 'e88faef5-8bf6-4256-b5a5-865c699103d6'),
    'B': ('simulation-family-b', '405514ce-9335-4705-a750-d811148bda4c'),
}
FIELD_CONFIG_KINDS = ('observation', 'report', 'therapy')


class KeepHttpStatusProcessor(HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


class HttpSession:
    def __init__(self):
        self.opener = build_opener(KeepHttpStatusProcessor, HTTPCookieProcessor(CookieJar()))

    def request(self, method, url, payload=None, headers=None, timeout=45):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
        request_headers = {'Accept': 'application/json', **(headers or {})}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request = Request(url, data=body, headers=request_headers, method=method)
        response = self.opener.open(request, timeout=timeout)
        return response.status, response.read()


def validate_target(base_url):
    parsed = urlsplit(base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.port != 8001 or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise RuntimeError('本检查仅允许主窗口指定的本机模拟 API 8001。')
    if not os.environ.get('SJIA_SIMULATION_PASSWORD'):
        raise RuntimeError('缺少模拟账号凭据 SJIA_SIMULATION_PASSWORD。')
    return parsed.geturl().rstrip('/')


def load_validators():
    protocol_path = ROOT / 'backend-design/protocol.schema.json'
    protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(protocol)
    protocol_resolver = RefResolver(base_uri=protocol_path.as_uri(), referrer=protocol)
    protocol_common = {'resolver': protocol_resolver, 'format_checker': FormatChecker()}
    protocol_validators = {
        name: Draft202012Validator({
            '$schema': protocol['$schema'], '$defs': protocol['$defs'],
            '$ref': f'#/$defs/{name}',
        }, **protocol_common)
        for name in ('HomeView', 'FieldConfigView')
    }
    dataset_path = ROOT / 'data-design/dataset.schema.json'
    dataset = json.loads(dataset_path.read_text(encoding='utf-8'))
    Draft202012Validator.check_schema(dataset)
    source_validator = Draft202012Validator({
        '$schema': dataset['$schema'], '$defs': dataset['$defs'],
        '$ref': '#/$defs/sourceReference',
    }, resolver=RefResolver(base_uri=dataset_path.as_uri(), referrer=dataset),
        format_checker=FormatChecker())
    return protocol_validators, source_validator


def parse_timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def login_and_check_relation(session, base_url, group, patient_id):
    bootstrap_status, bootstrap_content = session.request('GET', f'{base_url}/api/auth/bootstrap', timeout=10)
    if bootstrap_status != 200:
        raise RuntimeError(f'{group} 组模拟会话初始化返回 HTTP {bootstrap_status}。')
    csrf_token = json.loads(bootstrap_content)['csrf_token']
    login_status, login_content = session.request('POST', f'{base_url}/api/auth/login',
        payload={'username': PATIENTS[group][0], 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': csrf_token}, timeout=10)
    if login_status != 200:
        raise RuntimeError(f'{group} 组模拟账号登录返回 HTTP {login_status}。')
    relations_status, relations_content = session.request('GET', f'{base_url}/api/relations', timeout=10)
    if relations_status != 200:
        raise RuntimeError(f'{group} 组模拟关联查询返回 HTTP {relations_status}。')
    rows = json.loads(relations_content)['relations']
    current = datetime.now(timezone.utc)
    active = [row for row in rows if row['patient_id'] == patient_id and row['status'] == 'active'
        and row.get('revoked_at') is None and parse_timestamp(row['valid_from']) <= current
        and (row['valid_until'] is None or parse_timestamp(row['valid_until']) > current)]
    return {
        'bootstrap_http_status': bootstrap_status,
        'login_http_status': login_status,
        'relation_http_status': relations_status,
        'active_relation_count': len(active),
        'active_relation_ids': [row['relation_id'] for row in active],
        'patient_id': patient_id,
        'valid': len(active) == 1,
    }


def schema_errors(validator, value):
    return [
        {'path': '/'.join(str(part) for part in error.absolute_path), 'message': error.message}
        for error in validator.iter_errors(value)
    ]


def measure_endpoint(session, base_url, group, patient_id, path, schema_name, validator, expected_kind=None):
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    status, raw = session.request('GET', f'{base_url}{path}')
    elapsed_ms = (time.perf_counter() - started) * 1000
    completed_at = datetime.now(timezone.utc).isoformat()
    raw_text = raw.decode('utf-8')
    payload = json.loads(raw_text)
    issues = schema_errors(validator, payload) if status == 200 else []
    response_patient_id = payload.get('patient_id') if isinstance(payload, dict) else None
    response_kind = payload.get('kind') if isinstance(payload, dict) else None
    identity_valid = response_patient_id == patient_id
    kind_valid = expected_kind is None or response_kind == expected_kind
    return {
        'group': group,
        'endpoint': path,
        'http_status': status,
        'started_at': started_at,
        'completed_at': completed_at,
        'elapsed_ms': round(elapsed_ms, 3),
        'response_bytes': len(raw),
        'response_sha256': hashlib.sha256(raw).hexdigest(),
        'schema': schema_name,
        'schema_valid': status == 200 and not issues,
        'schema_errors': issues,
        'response_patient_id': response_patient_id,
        'patient_identity_valid': identity_valid,
        'expected_kind': expected_kind,
        'response_kind': response_kind,
        'response_kind_valid': kind_valid,
        'successful': status == 200 and not issues and identity_valid and kind_valid,
        'response_body_raw': raw_text,
        'response_body': payload,
    }


def validate_clinical_sources(home_result, source_validator):
    if not home_result['successful']:
        return {'status': 'NOT_RUN_HOME_RESPONSE_NOT_VALID', 'source_count': 0, 'errors': []}
    body = home_result['response_body']
    clinical = body.get('clinical') if isinstance(body, dict) else None
    if clinical is None:
        return {'status': 'NOT_RUN_NO_PUBLISHED_CONTENT', 'source_count': 0, 'errors': []}
    refs = clinical['source_refs']
    errors = []
    for index, source in enumerate(refs):
        for issue in schema_errors(source_validator, source):
            errors.append({'index': index, **issue})
    return {
        'status': 'PASSED' if not errors else 'FAILED',
        'source_count': len(refs),
        'errors': errors,
        'all_source_references_valid': not errors,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--report', default='docs/testing/T31-home-fieldconfig-http-simulation-report.json')
    parser.add_argument('--qualification-updated-at', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--runtime-id')
    parser.add_argument('--api-process-id')
    args = parser.parse_args()
    base_url = validate_target(args.base_url)
    protocol_validators, source_validator = load_validators()
    results = []
    relation_checks = {}
    for group, (account, patient_id) in PATIENTS.items():
        session = HttpSession()
        relation_check = login_and_check_relation(session, base_url, group, patient_id)
        relation_checks[group] = relation_check
        if not relation_check['valid']:
            raise RuntimeError(f'{group} 组没有唯一且有效的模拟患儿关联。')
        results.append(measure_endpoint(session, base_url, group, patient_id,
            f'/api/patients/{patient_id}/home', 'HomeView', protocol_validators['HomeView']))
        for kind in FIELD_CONFIG_KINDS:
            results.append(measure_endpoint(session, base_url, group, patient_id,
            f'/api/patients/{patient_id}/field-config?kind={kind}', 'FieldConfigView',
                protocol_validators['FieldConfigView'], expected_kind=kind))
    clinical_sources = {
        'A': validate_clinical_sources(results[0], source_validator),
        'B': validate_clinical_sources(results[4], source_validator),
    }
    report_path = (ROOT / args.report).resolve()
    if ROOT not in report_path.parents:
        raise RuntimeError('T31 HTTP 报告路径必须位于项目目录内。')
    successful = sum(item['successful'] for item in results)
    report = {
        'task': 'T31',
        'classification': 'SIMULATION_ONLY_HOME_AND_FIELDCONFIG_HTTP_CAPTURE',
        'simulation_only': True,
        'measured_at': datetime.now(timezone.utc).isoformat(),
        'qualification': {
            'updated_at': args.qualification_updated_at,
            'release_id': args.release_id,
            'runtime_id': args.runtime_id,
        },
        'target': {
            'api': base_url,
            'api_process_id_reported_by_main': args.api_process_id,
            'account_groups': {group: {'account': account, 'patient_id': patient_id}
                for group, (account, patient_id) in PATIENTS.items()},
            'relations': relation_checks,
            'requests': 8,
            'concurrency': 1,
            'parallel_assessment_note': '主窗口告知 B 组评估可能并行；本报告按各回应实际状态和延迟记录，不将单次样本解释为容量验收。',
        },
        'runtime_versions': {
            'python': sys.version.split()[0],
            'jsonschema': importlib.metadata.version('jsonschema'),
        },
        'source_sha256': {
            'measurement_program': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'protocol_schema': hashlib.sha256((ROOT / 'backend-design/protocol.schema.json').read_bytes()).hexdigest(),
            'dataset_schema': hashlib.sha256((ROOT / 'data-design/dataset.schema.json').read_bytes()).hexdigest(),
        },
        'api_responses': results,
        'clinical_source_references': clinical_sources,
        'summary': {
            'request_count': len(results),
            'success_count': successful,
            'failure_count': len(results) - successful,
            'failure_rate': (len(results) - successful) / len(results),
            'p95_ms': None,
            'p99_ms': None,
            'latency_note': '每个端点为单次实际请求；记录单次延迟，不计算 P95/P99。',
        },
        'effects': {
            'database_writes': 0,
            'queue_consumption': 0,
            'provider_calls': 0,
            'external_sends': 0,
        },
        'acceptance': {
            'http_and_strict_schema': 'PASSED' if successful == len(results) else 'FAILED',
            'real_capacity': 'NOT_RUN',
            'clinical_scale': 'NOT_RUN',
            'device_wechat_visual': 'NOT_RUN',
        },
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    reread = json.loads(report_path.read_text(encoding='utf-8'))
    if reread != report:
        raise RuntimeError('T31 Home/FieldConfig HTTP 报告保存后重读内容不一致。')
    report['saved_and_reread'] = True
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if json.loads(report_path.read_text(encoding='utf-8')) != report:
        raise RuntimeError('T31 Home/FieldConfig HTTP 完成状态保存后重读内容不一致。')
    print(json.dumps({
        'report': str(report_path),
        'saved_and_reread': True,
        'request_count': len(results),
        'success_count': successful,
        'failure_count': len(results) - successful,
        'latencies_ms': [{'group': item['group'], 'endpoint': item['endpoint'], 'elapsed_ms': item['elapsed_ms']}
            for item in results],
        'clinical_source_references': clinical_sources,
        'schema_errors': [{'group': item['group'], 'endpoint': item['endpoint'], 'errors': item['schema_errors']}
            for item in results if item['schema_errors']],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
