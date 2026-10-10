import argparse
import hashlib
import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import django
import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'
django.setup()

from django.conf import settings
from django.db import connection
from identity.models import Patient


def checked(client, method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code == expected, (method, path, response.status_code, response.text[:500])
    return response


parser = argparse.ArgumentParser(description='T60本地模拟业务演示；只使用SIMULATION资料与账号')
parser.add_argument('--web', default='http://127.0.0.1:5184')
parser.add_argument('--api', default='http://127.0.0.1:8001')
parser.add_argument('--output', default='.planning-work/parallel/window1/t60-simulation-demo.json')
parser.add_argument('--positive-check', action='store_true', help='按准确对象清单只读核验正向业务链')
parser.add_argument('--positive-manifest', help='SIMULATION_ONLY正向业务对象清单JSON路径')
parser.add_argument('--fixture-file', help='正向核验使用的明确模拟账号与患儿资料清单')
parser.add_argument('--positive-output', default='.planning-work/parallel/window1/t60-positive-verification.json')
args = parser.parse_args()
for address in (args.web, args.api):
    parsed = urlparse(address)
    assert parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and not parsed.username
output = (ROOT / args.output).resolve()
assert output.is_relative_to(ROOT / '.planning-work')
assert settings.SJIA_ENVIRONMENT == 'simulation'
assert settings.SIMULATION_ROOT.name == '.simulation-work'

with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
    database, schema, database_port = cursor.fetchone()
assert (database, schema, database_port) == ('sjia_simulation', 'sjia', 5433)
positive_fixture = None
if args.fixture_file:
    fixture_path = (ROOT / args.fixture_file).resolve()
    assert args.positive_check and fixture_path.is_relative_to(ROOT / '.simulation-work')
    positive_fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    assert positive_fixture['mode'] == 'SIMULATION_ONLY' and positive_fixture['family_username']
    patient = Patient.objects.get(id=positive_fixture['patient_id'], status='active')
else:
    patient = Patient.objects.get(display_id='SIMULATION模拟患儿A', status='active')
if args.positive_check:
    positive_output = (ROOT / args.positive_output).resolve()
    assert positive_output.is_relative_to(ROOT / '.planning-work/parallel/window1')
    required = ('patient_id', 'relation_id', 'task_id', 'evaluation_id', 'alert_id', 'recipient_id',
        'receipt_ids', 'summary_id', 'source_versions')
    if not args.positive_manifest:
        result = {'task_id': 'T60', 'mode': 'SIMULATION_ONLY', 'status': 'NOT_RUN',
            'reason': 'POSITIVE_MANIFEST_NOT_PROVIDED', 'missing_manifest_fields': list(required),
            'database': {'name': database, 'schema': schema, 'port': database_port},
            'business_writes': 0, 'model_calls': 0, 'external_calls': 0}
        positive_output.parent.mkdir(parents=True, exist_ok=True)
        positive_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        assert json.loads(positive_output.read_text(encoding='utf-8')) == result
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(0)
    manifest_path = (ROOT / args.positive_manifest).resolve()
    assert manifest_path.is_relative_to(ROOT / '.planning-work/parallel/window1')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest.get('schema_version') == 't60-positive-demo-manifest-1'
    assert manifest.get('mode') == 'SIMULATION_ONLY'
    missing = [key for key in required if key not in manifest]
    assert not missing, {'missing_manifest_fields': missing}
    assert str(patient.id) == manifest['patient_id']
    assert manifest['source_versions'] and manifest['receipt_ids']
    source_versions = manifest['source_versions']
    assert all(set(item) == {'record_id', 'record_version_id', 'is_current'} for item in source_versions)
    identifiers = [manifest[key] for key in ('patient_id', 'relation_id', 'task_id', 'evaluation_id',
        'alert_id', 'recipient_id', 'summary_id')]
    identifiers.extend(manifest['receipt_ids'])
    for item in source_versions:
        identifiers.extend((item['record_id'], item['record_version_id']))
    assert all(str(uuid.UUID(value)) == value for value in identifiers)
    assert all(type(item['is_current']) is bool for item in source_versions)

    def login_family():
        client = httpx.Client(base_url=args.web, timeout=30, trust_env=False)
        bootstrap = checked(client, 'GET', '/api/auth/bootstrap').json()
        session = checked(client, 'POST', '/api/auth/login', json={
            'username': positive_fixture['family_username'] if positive_fixture else 'simulation-family',
            'password': os.environ['SJIA_SIMULATION_PASSWORD']},
            headers={'X-CSRFToken': bootstrap['csrf_token']}).json()
        assert session['audience'] == 'family'
        return client

    def login_support():
        client = httpx.Client(base_url=args.api, timeout=30, trust_env=False)
        bootstrap = checked(client, 'GET', '/api/auth/bootstrap').json()
        session = checked(client, 'POST', '/api/v2/support/session/login', json={
            'username': positive_fixture.get('support_username', 'simulation-support') if positive_fixture else 'simulation-support',
            'password': os.environ['SJIA_SIMULATION_PASSWORD']},
            headers={'X-CSRFToken': bootstrap['csrf_token']}).json()
        assert session['audience'] == 'support'
        return client

    support = login_support()
    family = login_family()
    relation = checked(family, 'GET', f"/api/relations/{manifest['relation_id']}").json()
    assert relation.get('patient_id') == manifest['patient_id']
    task = checked(support, 'GET', f"/api/v2/support/alert-evaluation-tasks/{manifest['task_id']}").json()
    assert task.get('task_id') == manifest['task_id'] and task.get('task_type') == 'alert_evaluation'
    assert task.get('status') == 'completed' and task.get('result_ref') == manifest['evaluation_id']
    evaluation = checked(support, 'GET', f"/api/v2/support/alert-evaluations/{manifest['evaluation_id']}").json()
    assert evaluation.get('evaluation_id') == manifest['evaluation_id']
    assert evaluation.get('patient_id') == manifest['patient_id']
    assert evaluation.get('task', {}).get('task_id') == manifest['task_id']
    assert evaluation.get('status') == 'completed'
    assert evaluation.get('validity') == 'valid' and evaluation.get('decision') == 'met'
    selected_refs_json = json.dumps(evaluation.get('selected_refs'), ensure_ascii=False)
    for item in source_versions:
        assert item['record_version_id'] in selected_refs_json

    alert = checked(family, 'GET', f"/api/alerts/{manifest['alert_id']}").json()
    assert alert.get('alert_id') == manifest['alert_id']
    assert alert.get('content_state') == 'available' and alert.get('validity') == 'valid'
    alert_refs = {item['record_version_id'] for item in alert['content']['source_refs']}
    assert {item['record_version_id'] for item in source_versions}.issubset(alert_refs)
    notification = checked(family, 'GET', f"/api/v2/notification-records/{manifest['recipient_id']}").json()
    assert notification.get('patient_id') == manifest['patient_id']
    assert notification.get('alert_id') == manifest['alert_id']
    assert notification.get('delivery_status') == 'delivered' and notification.get('read_at')
    receipt_ids = {receipt['receipt_id'] for attempt in notification.get('attempts', []) for receipt in attempt.get('receipts', [])}
    assert set(manifest['receipt_ids']).issubset(receipt_ids)
    summary = checked(family, 'GET', f"/api/summaries/{manifest['summary_id']}").json()
    assert summary.get('summary_id') == manifest['summary_id']
    assert summary.get('patient_id') == manifest['patient_id']
    assert summary.get('validity') == 'valid' and summary.get('needs_regeneration') is False
    summary_versions = {item['record_version_id'] for item in summary.get('items', [])}
    assert {item['record_version_id'] for item in source_versions}.issubset(summary_versions)

    versions = []
    for item in source_versions:
        current_record = checked(family, 'GET', f"/api/records/{item['record_id']}").json()
        version = checked(family, 'GET', f"/api/record-versions/{item['record_version_id']}").json()
        assert current_record.get('patient_id') == manifest['patient_id']
        assert version.get('patient_id') == manifest['patient_id']
        assert version.get('record_id') == item['record_id']
        matches_current = current_record.get('record_version_id') == item['record_version_id']
        assert matches_current is item['is_current']
        versions.append({'record_id': item['record_id'], 'record_version_id': item['record_version_id'],
            'quality_status': current_record.get('quality_status'), 'current_version_id': current_record.get('record_version_id'),
            'is_current': matches_current, 'source_quality_status': version.get('quality_status'),
            'fact_refs': version.get('fact_refs', [])})
    checked(family, 'POST', '/api/session/logout', expected=200, json={},
        headers={'X-CSRFToken': checked(family, 'GET', '/api/session').json()['csrf_token']})
    result = {'task_id': 'T60', 'mode': 'SIMULATION_ONLY', 'status': 'PASS',
        'manifest': str(manifest_path.relative_to(ROOT)), 'patient_id': manifest['patient_id'],
        'task': {'task_id': task['task_id'], 'status': task['status'], 'result_ref': task['result_ref']},
        'evaluation': {'evaluation_id': evaluation['evaluation_id'], 'status': evaluation['status'], 'validity': evaluation.get('validity')},
        'alert': {'alert_id': alert['alert_id'], 'validity': alert['validity'], 'content_state': alert['content_state']},
        'notification': {'recipient_id': notification['recipient_id'], 'delivery_status': notification['delivery_status'],
            'receipt_ids': sorted(receipt_ids)},
        'summary': {'summary_id': summary['summary_id'], 'validity': summary['validity'],
            'needs_regeneration': summary['needs_regeneration']},
        'source_versions': versions, 'business_writes': 0, 'model_calls': 0, 'external_calls': 0,
        'clinical': 'NOT_RUN', 'visual': 'NOT_RUN', 'actual_device': 'NOT_RUN', 'wechat': 'NOT_RUN',
        'official_rules': 'NOT_PROVIDED', 'display_authorization': 'NOT_PROVIDED'}
    positive_output.parent.mkdir(parents=True, exist_ok=True)
    positive_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    assert json.loads(positive_output.read_text(encoding='utf-8')) == result
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0)
t40_flow = json.loads((ROOT / '.planning-work/parallel/window1/t40-positive-provider-disposition.json').read_text(encoding='utf-8'))
t40_readback = json.loads((ROOT / '.planning-work/parallel/window1/t40-positive-provider-disposition-readback.json').read_text(encoding='utf-8'))
assert t40_flow['mode'] == 'SIMULATION' and t40_flow['status'] == 'PASS'
assert t40_readback['mode'] == 'SIMULATION' and t40_readback['status'] == 'PASS'
assert t40_flow['patient_id'] == str(patient.id)
assert t40_readback['original_file_id'] == t40_flow['file_id']

report = json.loads(output.read_text(encoding='utf-8')) if output.exists() else None
if report is None:
    run_id = str(uuid.uuid4())
    report = {
        'task_id': 'T60',
        'mode': 'SIMULATION_ONLY',
        'status': 'RUNNING',
        'run_id': run_id,
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(),
        'django': django.get_version(),
        'web': args.web,
        'api': args.api,
        'database': {'name': database, 'schema': schema, 'port': database_port},
        'patient_id': str(patient.id),
        'steps': [],
        'business_scope': {
            'clinical_result_created': False,
            'alert_or_notification_created': False,
            'summary_created': False,
            'external_service_called': False,
            'model_called': False,
        },
        'unrun': {
            'clinical_evaluation': 'NOT_RUN; no independent clinical reference set is available',
            'positive_alert_and_notification_delivery': 'NOT_RUN; no current qualified alert or recipient is created by this demo',
            'summary_generation': 'NOT_RUN; this run reads saved summary state only and does not process shared tasks',
            'real_device_wechat_visual_production': 'NOT_RUN',
            'official_rules_submission_and_display_authorization': 'NOT_PROVIDED',
        },
    }
else:
    assert report['task_id'] == 'T60' and report['mode'] == 'SIMULATION_ONLY'
    assert report['status'] == 'RUNNING' and report['database']['name'] == database
    assert report['patient_id'] == str(patient.id)
run_id = report['run_id']
output.parent.mkdir(parents=True, exist_ok=True)


def save_checkpoint():
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    saved = json.loads(output.read_text(encoding='utf-8'))
    assert saved == report


def step(name, details):
    existing = next((item for item in report['steps'] if item['name'] == name), None)
    if existing is None:
        report['steps'].append({'name': name, **details})
    else:
        existing.update(details)
    save_checkpoint()


save_checkpoint()
runtime_step = next((item for item in report['steps'] if item['name'] == 'simulation_runtime_ready'), None)
if runtime_step is None:
    with httpx.Client(base_url=args.api, timeout=30, trust_env=False) as api:
        readiness = checked(api, 'GET', '/internal/readiness?environment=simulation').json()
        assert readiness['status'] == 'ready' and all(readiness['dependencies'].values())
        assert readiness['loaded_config_version']['environment'] == 'simulation'
        runtime_id = readiness['loaded_config_version']['runtime_config_id']
    step('simulation_runtime_ready', {'runtime_config_id': runtime_id, 'database': database})

with httpx.Client(base_url=args.web, timeout=30, trust_env=False) as web:
    bootstrap = checked(web, 'GET', '/api/auth/bootstrap').json()
    login = checked(web, 'POST', '/api/auth/login', expected=200,
        json={'username': 'simulation-family', 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': bootstrap['csrf_token']}).json()
    csrf = login['csrf_token']
    session = checked(web, 'GET', '/api/session').json()
    assert session['audience'] == 'family'
    relation_list = checked(web, 'GET', '/api/relations').json()
    assert str(patient.id) in json.dumps(relation_list)
    session_cookies = web.cookies
step('family_login_and_patient_selection', {'audience': 'family', 'patient_id': str(patient.id), 'relation_count': len(relation_list.get('relations', []))})

with httpx.Client(base_url=args.web, timeout=30, trust_env=False, cookies=session_cookies) as web:
    source_bytes = checked(web, 'GET', f'/api/files/{t40_flow["file_id"]}/content').content
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    assert source_sha256 == t40_readback['original_checksum']
    field_config = checked(web, 'GET', f'/api/patients/{patient.id}/field-config?kind=observation').json()
    step('saved_original_read_and_observation_configuration', {
        'file_id': t40_flow['file_id'], 'file_bytes': len(source_bytes), 'file_sha256': source_sha256,
        'field_config_state': field_config.get('state'), 'field_config_version': field_config.get('config_version'),
        'writable_fields': [item['field_code'] for item in field_config.get('fields', []) if item.get('writable')],
    })
    if field_config.get('state') != 'available':
        report['status'] = 'BLOCKED_PREREQUISITE'
        report['blocked_reason'] = 'OBSERVATION_FIELD_CONFIGURATION_NOT_AVAILABLE'
        report['blocked_field_config_state'] = field_config.get('state')
        save_checkpoint()
        raise RuntimeError('Observation field configuration is not available for this simulation run.')
    assert field_config['state'] == 'available'
    assert field_config['config_version'] and any(item['field_code'] == 'observation_note' and item['writable'] for item in field_config['fields'])
step('saved_original_read_and_observation_configuration', {
    'file_id': t40_flow['file_id'], 'file_bytes': len(source_bytes), 'file_sha256': source_sha256,
    'field_config_version': field_config['config_version'], 'field_config_state': field_config['state'],
})

first_text = f'SIMULATION_ONLY T60 {run_id} observation V1'
record_payload = {
    'kind': 'observation',
    'capture_method': 'manual',
    'origin_type': 'self_report',
    'field_config_version': field_config['config_version'],
    'fields': [{'field_code': 'observation_note', 'value_type': 'text', 'value_state': 'known', 'raw_text': first_text}],
}
record_target = f'/api/patients/{patient.id}/records'
request_id = str(uuid.uuid5(uuid.UUID(run_id), 'record-create'))
with httpx.Client(base_url=args.web, timeout=30, trust_env=False, cookies=session_cookies) as web:
    intent = checked(web, 'POST', '/api/write-intents', expected=201,
        json={'operation_id': 'record_create', 'target_key': record_target, 'request_id': request_id,
            'payload': record_payload, 'schema_version': 'back-json-1'},
        headers={'X-CSRFToken': csrf}).json()
    created = checked(web, 'POST', record_target, expected=201, json=record_payload,
        headers={'X-CSRFToken': csrf, 'X-Write-Intent': intent['intent_id'],
            'X-Intent-Generation': intent['generation'], 'Idempotency-Key': request_id}).json()
    record_id = created['record_id']
    version_one_id = created['record_version_id']
    assert created['version'] == 1
step('observation_created_through_http', {'record_id': record_id, 'record_version_id': version_one_id, 'version': 1, 'request_id': request_id})

with httpx.Client(base_url=args.web, timeout=30, trust_env=False, cookies=session_cookies) as web:
    refreshed_record = checked(web, 'GET', f'/api/records/{record_id}').json()
    first_field = next(item for item in refreshed_record['fields'] if item['field_code'] == 'observation_note')
    assert refreshed_record['record_version_id'] == version_one_id and first_field['raw_text'] == first_text
    first_history = checked(web, 'GET', f'/api/records/{record_id}/history').json()
    assert first_history['current_version'] == '1' and len(first_history['versions']) == 1
step('observation_detail_refresh_and_history_read', {
    'record_id': record_id, 'version': refreshed_record['version'], 'history_versions': len(first_history['versions']),
    'value_sha256': hashlib.sha256(first_field['raw_text'].encode()).hexdigest(),
})

second_text = f'SIMULATION_ONLY T60 {run_id} observation V2'
correction = {
    'reason': 'SIMULATION_ONLY T60 demo correction',
    'expected_version': 1,
    'fields': [{
        'field_code': 'observation_note', 'concept_code': None, 'original_name': 'T60 simulated observation',
        'value_type': 'text', 'value_state': 'known', 'raw_text': second_text, 'raw_numeric': None,
        'categorical_value': None, 'qualifier': None, 'raw_unit': None, 'method': None,
        'reference_interval': None, 'report_flag': None, 'sample_time_origin': 'unknown', 'medical_time': None,
    }],
}
with httpx.Client(base_url=args.web, timeout=30, trust_env=False, cookies=session_cookies) as web:
    current = checked(web, 'GET', f'/api/records/{record_id}').json()
    if current['version'] == 1:
        corrected = checked(web, 'POST', f'/api/records/{record_id}/corrections', expected=201,
            json=correction, headers={'X-CSRFToken': csrf}).json()
    else:
        assert current['version'] == 2
        corrected = {'record_id': record_id, 'record_version_id': current['record_version_id'], 'version': 2}
    assert corrected['version'] == 2 and corrected['record_id'] == record_id
    current = checked(web, 'GET', f'/api/records/{record_id}').json()
    current_field = next(item for item in current['fields'] if item['field_code'] == 'observation_note')
    history = checked(web, 'GET', f'/api/records/{record_id}/history').json()
    original_version = checked(web, 'GET', f'/api/record-versions/{version_one_id}').json()
    original_field = next(item for item in original_version['fields'] if item['field_code'] == 'observation_note')
    assert current['version'] == 2 and current_field['raw_text'] == second_text
    assert history['current_version'] == '2' and len(history['versions']) == 2
    assert original_version['version'] == 1 and original_field['raw_text'] == first_text
step('correction_history_and_immutable_prior_version_read', {
    'record_id': record_id, 'current_version': current['version'], 'history_versions': len(history['versions']),
    'original_version_id': version_one_id,
    'current_value_sha256': hashlib.sha256(current_field['raw_text'].encode()).hexdigest(),
    'original_value_sha256': hashlib.sha256(original_field['raw_text'].encode()).hexdigest(),
})

with httpx.Client(base_url=args.web, timeout=30, trust_env=False, cookies=session_cookies) as web:
    home = checked(web, 'GET', f'/api/patients/{patient.id}/home').json()
    alerts = checked(web, 'GET', f'/api/patients/{patient.id}/alerts').json()
    alert_stats = checked(web, 'GET', f'/api/patients/{patient.id}/alerts/stats').json()
    summaries = checked(web, 'GET', f'/api/patients/{patient.id}/summaries').json()
    assert isinstance(alerts.get('alerts'), list) and isinstance(summaries, dict)
    step('current_alert_and_summary_state_read', {
        'home_alert_state': home.get('alert_state'), 'current_alert_id': home.get('current_alert_id'),
        'alert_count': len(alerts['alerts']), 'alert_stats': alert_stats,
        'summary_collection_keys': sorted(summaries.keys()),
        'summary_count': len(summaries.get('summaries', [])) if isinstance(summaries.get('summaries'), list) else None,
        'summary_list_sha256': hashlib.sha256(json.dumps(summaries, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    })
    checked(web, 'POST', '/api/session/logout', expected=200, json={}, headers={'X-CSRFToken': csrf})
    checked(web, 'GET', '/api/session', expected=401)
step('family_logout_and_session_revocation', {'logout_status': 200, 'post_logout_session_status': 401})

report['status'] = 'PARTIAL_SIMULATION_DEMO_COMPLETED'
report['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
report['record_id'] = record_id
report['record_version_ids'] = [version_one_id, corrected['record_version_id']]
save_checkpoint()
print(json.dumps({
    'task_id': report['task_id'], 'mode': report['mode'], 'status': report['status'],
    'run_id': run_id, 'steps': len(report['steps']), 'record_id': record_id,
    'record_versions': report['record_version_ids'], 'evidence': str(output.relative_to(ROOT)),
    'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'unrun': report['unrun'],
}, ensure_ascii=False, indent=2))
