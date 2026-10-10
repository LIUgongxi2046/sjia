import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import django
import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from tasks.models import OutboxEvent, Task


PATIENT_ID = '405514ce-9335-4705-a750-d811148bda4c'
OPTIONS_REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-static-options-read.json'
STATE = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-static-task-state.json'
REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-static-task-readback.json'
DENIAL_REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-static-task-latest-denial.json'
API_BASE = 'http://127.0.0.1:8001'
TARGET = f'/support/patients/{PATIENT_ID}/assessments'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n', encoding='utf-8')
    reread = json.loads(path.read_text(encoding='utf-8'))
    expected = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    require(reread == expected, f'Saved report readback differs: {path.name}.')


def task_payload(plan, known_at, assessment_at):
    return {
        'kind': plan['kind'],
        'assessment_at': assessment_at,
        'known_at': known_at,
        'mode': 'current',
        'rule_id': plan['rule']['config_id'],
        'window_policy_id': plan['window']['config_id'],
        'verification_policy_id': plan['verification']['config_id'],
        'observation_period_id': None,
    }


def read_scope(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    require(value.get('schema_version') == 't29-common-scope-1', 'Unsupported T29 common scope version.')
    require(value.get('mode') == 'current', 'T29 common scope must use current mode.')
    parsed_times = {}
    for key in ('known_at', 'assessment_at', 'source_cutoff_at', 'qualification_verified_at',
            'publication_evidence_saved_at'):
        parsed = datetime.fromisoformat(value[key].replace('Z', '+00:00'))
        require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            f'T29 common scope {key} must include a timezone.')
        parsed_times[key] = parsed
    require(value['known_at'] == value['assessment_at'] == value['source_cutoff_at'],
        'Static and dynamic signal evaluation requires identical known_at and assessment_at.')
    require(parsed_times['source_cutoff_at'] > parsed_times['qualification_verified_at']
        and parsed_times['source_cutoff_at'] > parsed_times['publication_evidence_saved_at'],
        'T29 source cutoff must follow current qualification and publication evidence.')
    return value


def read_simulation_fixture(path):
    fixture_path = path.resolve()
    fixture_root = (ROOT / '.simulation-work').resolve()
    require(fixture_path.is_relative_to(fixture_root),
        'T29 fixture must be saved in the simulation-work directory.')
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    require(fixture.get('mode') == 'SIMULATION_ONLY', 'T29 requires an explicitly marked simulation fixture.')
    fixture['patient_id'] = str(uuid.UUID(fixture['patient_id']))
    require(isinstance(fixture.get('family_username'), str) and fixture['family_username'].strip(),
        'T29 fixture must identify its simulation family account.')
    return fixture, hashlib.sha256(fixture_bytes).hexdigest(), fixture_path


def api_read_task(client, task_id):
    response = client.get(f'/api/v2/support/assessment-tasks/{task_id}')
    require(response.status_code == 200, f'Assessment task read returned HTTP {response.status_code}.')
    body = response.json()
    require(body.get('task_id') == task_id, 'Assessment task read returned another task identity.')
    return body


def create_or_read(client, item, known_at, assessment_at):
    payload = item['payload']
    intent_id = item.get('intent_id')
    if intent_id is None:
        prepared = client.post('/api/v2/write-intents', json={
            'operation_id': 'assessment_v2_create', 'target_key': TARGET, 'request_id': item['request_id'],
            'schema_version': '2.0.0', 'payload': payload,
        })
        if prepared.status_code != 201:
            denial = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'PRECONDITIONS_MISSING',
                'patient_id': PATIENT_ID, 'known_at': known_at, 'assessment_at': assessment_at,
                'kind': item['kind'], 'request_id': item['request_id'], 'target_key': TARGET,
                'http_status': prepared.status_code, 'response_body': prepared.text,
                'write_intent_id': None, 'task_id_value': None, 'model_calls': 0, 'external_calls': 0}
            save_json(DENIAL_REPORT, denial)
            raise RuntimeError(f'{item["kind"]} intent preparation returned HTTP {prepared.status_code}: {prepared.text}')
        prepared_body = prepared.json()
        item['intent_id'] = prepared_body['intent_id']
        item['generation'] = prepared_body['generation']
        save_json(STATE, state)
        intent_id = item['intent_id']

    request = client.get(f'/api/v2/requests/{intent_id}')
    require(request.status_code == 200, f'{item["kind"]} request read returned HTTP {request.status_code}: {request.text}')
    request_body = request.json()
    if request_body['state'] == 'committed':
        task_id = request_body['receipt']['task_id']
        item['task_id'] = task_id
        item['request_state'] = request_body['state']
        item['task_read'] = api_read_task(client, task_id)
        save_json(STATE, state)
        return item
    require(request_body['state'] == 'pending' and request_body['may_retry'] is False,
        f'{item["kind"]} request is not safe to submit from saved state: {request_body["state"]}.')
    if item.get('task_id'):
        item['task_read'] = api_read_task(client, item['task_id'])
        save_json(STATE, state)
        return item
    response = client.post(f'/api/v2{TARGET}', json=payload, headers={
        'X-Write-Intent': intent_id, 'Idempotency-Key': item['request_id'],
        'X-Intent-Generation': item['generation'],
    })
    require(response.status_code == 202, f'{item["kind"]} task creation returned HTTP {response.status_code}: {response.text}')
    created = response.json()
    task_id = created['task_id']
    item['task_id'] = task_id
    item['create_response'] = {'status_code': response.status_code, 'task_id': task_id, 'status': created['status']}
    save_json(STATE, state)
    readback = api_read_task(client, task_id)
    require(readback['status'] == created['status'], 'Task status changed between create and readback.')
    item['task_read'] = readback
    request = client.get(f'/api/v2/requests/{intent_id}')
    require(request.status_code == 200 and request.json()['state'] == 'committed',
        f'{item["kind"]} request did not read back as committed.')
    item['request_state'] = request.json()['state']
    save_json(STATE, state)
    return item


def main():
    global state, PATIENT_ID, TARGET, OPTIONS_REPORT, STATE, REPORT, DENIAL_REPORT
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--kind', required=True, choices=('classification', 'ms'))
    parser.add_argument('--scope-file')
    parser.add_argument('--fixture-file', default=str(ROOT / '.simulation-work' / 't29-business-fixture.json'))
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label with optional hyphens.')
    report_root = ROOT / '.planning-work' / 'parallel' / 'window1'
    OPTIONS_REPORT = report_root / f't29-static-options-{args.run_tag}.json'
    STATE = report_root / f't29-static-task-{args.run_tag}-state.json'
    REPORT = report_root / f't29-static-task-{args.run_tag}-readback.json'
    DENIAL_REPORT = report_root / f't29-static-task-{args.run_tag}-denial.json'
    fixture, fixture_sha256, fixture_path = read_simulation_fixture(Path(args.fixture_file))
    PATIENT_ID = fixture['patient_id']
    TARGET = f'/support/patients/{PATIENT_ID}/assessments'
    scope_path = Path(args.scope_file).resolve() if args.scope_file else None
    if scope_path is not None:
        require(scope_path.is_relative_to(report_root.resolve()),
            'T29 common scope must be saved in the ignored window1 work directory.')
    common_scope = read_scope(scope_path) if scope_path else None
    scope_sha256 = hashlib.sha256(scope_path.read_bytes()).hexdigest() if scope_path else None
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding='utf-8'))
        require(state.get('patient_id') == PATIENT_ID
            and state.get('business_fixture_sha256') == fixture_sha256,
            'Saved static task state belongs to another simulation fixture.')
    else:
        state = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'patient_id': PATIENT_ID,
            'tasks': {args.kind: {'kind': args.kind, 'request_id': str(uuid.uuid4())}},
            'business_fixture_path': str(fixture_path.relative_to(ROOT)),
            'business_fixture_sha256': fixture_sha256,
            'family_username': fixture['family_username'],
            'run_tag': args.run_tag, 'modeled_calls': 0, 'external_calls': 0}
    require(set(state['tasks']) == {args.kind}, 'Saved static run is bound to another task kind.')
    if common_scope:
        require(state.get('scope_sha256') in (None, scope_sha256),
            'Saved static state is bound to another T29 common scope.')
        require(state.get('known_at') in (None, common_scope['known_at'])
            and state.get('assessment_at') in (None, common_scope['assessment_at']),
            'Saved static state timestamps differ from the T29 common scope.')

    with httpx.Client(base_url=API_BASE, timeout=45, trust_env=False) as client:
        bootstrap = client.get('/api/auth/bootstrap')
        require(bootstrap.status_code == 200, f'Simulation API bootstrap returned HTTP {bootstrap.status_code}.')
        client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
        login = client.post('/api/v2/support/session/login', json={
            'username': fixture.get('support_username', 'simulation-support'),
            'password': os.environ['SJIA_SIMULATION_PASSWORD']})
        require(login.status_code == 200 and login.json().get('audience') == 'support',
            f'Support login returned HTTP {login.status_code}.')
        client.headers['X-CSRFToken'] = login.json()['csrf_token']
        if any('intent_id' in item or 'task_id' in item for item in state['tasks'].values()):
            options_bytes = OPTIONS_REPORT.read_bytes()
            options_report = json.loads(options_bytes)
            require(state['options_sha256'] == hashlib.sha256(options_bytes).hexdigest(),
                'Static task state is bound to another options report.')
        else:
            known_at = common_scope['known_at'] if common_scope else datetime.now(timezone.utc).isoformat()
            assessment_at = common_scope['assessment_at'] if common_scope else known_at
            response = client.get(f'/api/v2/support/patients/{PATIENT_ID}/assessment-options',
                params={'mode': 'current', 'known_at': known_at})
            require(response.status_code == 200, f'Static options returned HTTP {response.status_code}: {response.text}')
            options_report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'patient_id': PATIENT_ID,
                'known_at': known_at, 'status_code': response.status_code, 'body': response.json(),
                'external_calls': 0, 'model_calls': 0}
            save_json(OPTIONS_REPORT, options_report)
            options_bytes = OPTIONS_REPORT.read_bytes()
            plans_by_kind = {plan['kind']: plan for plan in options_report['body']['plans']}
            require(set(plans_by_kind) == {'classification', 'ms'}, 'Expected one current plan for each static assessment kind.')
            state.update({'options_path': str(OPTIONS_REPORT.relative_to(ROOT)),
                'options_sha256': hashlib.sha256(options_bytes).hexdigest(), 'known_at': known_at,
                'assessment_at': assessment_at})
            if common_scope:
                state.update({'scope_sha256': scope_sha256, 'scope_path': str(scope_path.relative_to(ROOT))})
            for kind in (args.kind,):
                state['tasks'][kind]['payload'] = task_payload(plans_by_kind[kind], known_at, assessment_at)
            save_json(STATE, state)
        for kind in (args.kind,):
            create_or_read(client, state['tasks'][kind], state['known_at'], state['assessment_at'])

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433), 'Database identity differs from approved simulation.')
        inventory = {}
        for kind, item in state['tasks'].items():
            task = Task.objects.get(id=item['task_id'], patient_id=PATIENT_ID, task_type='assessment',
                schema_version='static-assessment-task-2')
            events = list(OutboxEvent.objects.filter(task=task, event_type='assessment.requested',
                schema_version='static-assessment-task-2').values('id', 'published_at', 'lease_generation'))
            require(len(events) == 1, f'{kind} task must have one exact assessment event.')
            inventory[kind] = {'task_id': str(task.id), 'task_status': task.status,
                'task_input_sha256': task.input_sha256, 'request_id': str(task.request_row.request_id),
                'outbox_event': {**events[0], 'id': str(events[0]['id'])},
                'task_api_readback': item['task_read']}
    report = {
        'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'STATIC_TASKS_CREATED_AND_READ',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'api_base': API_BASE, 'patient_id': PATIENT_ID, 'known_at': state['known_at'],
        'assessment_at': state['assessment_at'], 'options_sha256': state['options_sha256'],
        'run_tag': args.run_tag, 'task_kind': args.kind,
        'scope_sha256': state.get('scope_sha256'),
            'business_fixture_sha256': state['business_fixture_sha256'],
        'business_fixture_path': str(fixture_path.relative_to(ROOT)),
        'tasks': inventory, 'modeled_calls': 0, 'external_calls': 0,
        'task_execution': 'PENDING_MAIN_WINDOW_SINGLE_EVENT_EXECUTION',
    }
    save_json(REPORT, report)
    print(json.dumps({'status': report['status'], 'tasks': {
        kind: {'task_id': row['task_id'], 'status': row['task_status'], 'outbox_event_id': row['outbox_event']['id']}
        for kind, row in inventory.items()}, 'saved_and_read': True, 'report_path': str(REPORT)}, sort_keys=True))


if __name__ == '__main__':
    main()
