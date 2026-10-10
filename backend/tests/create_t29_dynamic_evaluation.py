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
from alerting.models import AlertEvent, AlertEvaluation, AlertRecipient
from tasks.models import OutboxEvent, Task


PATIENT_ID = '405514ce-9335-4705-a750-d811148bda4c'
STATE = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-dynamic-task-state.json'
REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-dynamic-task-readback.json'
OPTIONS_REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-dynamic-options-read.json'
API_BASE = 'http://127.0.0.1:8001'
TARGET = f'/support/patients/{PATIENT_ID}/alert-evaluations'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    path.write_text(encoded, encoding='utf-8')
    reread = json.loads(path.read_text(encoding='utf-8'))
    require(reread == json.loads(encoded), f'Saved report readback differs: {path.name}.')


def read_task(client, task_id):
    response = client.get(f'/api/v2/support/alert-evaluation-tasks/{task_id}')
    require(response.status_code == 200, f'Dynamic task read returned HTTP {response.status_code}: {response.text}')
    value = response.json()
    require(value['task_id'] == task_id, 'Dynamic task read returned another task identity.')
    return value


def read_evaluation(client, evaluation_id):
    response = client.get(f'/api/v2/support/alert-evaluations/{evaluation_id}')
    require(response.status_code == 200, f'Dynamic evaluation read returned HTTP {response.status_code}: {response.text}')
    value = response.json()
    require(value['evaluation_id'] == evaluation_id, 'Dynamic evaluation read returned another evaluation identity.')
    return value


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


def main():
    global PATIENT_ID, TARGET, STATE, REPORT, OPTIONS_REPORT
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--scope-file')
    parser.add_argument('--fixture-file', default=str(ROOT / '.simulation-work' / 't29-business-fixture.json'))
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label with optional hyphens.')
    report_root = ROOT / '.planning-work' / 'parallel' / 'window1'
    STATE = report_root / f't29-dynamic-task-{args.run_tag}-state.json'
    REPORT = report_root / f't29-dynamic-task-{args.run_tag}-readback.json'
    OPTIONS_REPORT = report_root / f't29-dynamic-options-{args.run_tag}.json'
    fixture, fixture_sha256, fixture_path = read_simulation_fixture(Path(args.fixture_file))
    PATIENT_ID = fixture['patient_id']
    TARGET = f'/support/patients/{PATIENT_ID}/alert-evaluations'
    scope_path = Path(args.scope_file).resolve() if args.scope_file else None
    if scope_path is not None:
        require(scope_path.is_relative_to(report_root.resolve()),
            'T29 common scope must be saved in the ignored window1 work directory.')
    common_scope = read_scope(scope_path) if scope_path else None
    scope_sha256 = hashlib.sha256(scope_path.read_bytes()).hexdigest() if scope_path else None
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding='utf-8'))
        require(state['patient_id'] == PATIENT_ID
            and state.get('business_fixture_sha256') == fixture_sha256,
            'Existing dynamic task state belongs to another simulation fixture.')
    else:
        state = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'patient_id': PATIENT_ID,
            'request_id': str(uuid.uuid4()), 'intent_id': None, 'generation': None,
            'task_id_value': None, 'model_calls': 0, 'external_calls': 0,
            'business_fixture_path': str(fixture_path.relative_to(ROOT)),
            'business_fixture_sha256': fixture_sha256, 'family_username': fixture['family_username']}
        save_json(STATE, state)
    if common_scope:
        require(state.get('scope_sha256') in (None, scope_sha256),
            'Saved dynamic state is bound to another T29 common scope.')
        require(state.get('known_at') in (None, common_scope['known_at'])
            and state.get('assessment_at') in (None, common_scope['assessment_at']),
            'Saved dynamic state timestamps differ from the T29 common scope.')

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

        if state.get('payload') is None:
            known_at = common_scope['known_at'] if common_scope else datetime.now(timezone.utc).isoformat()
            assessment_at = common_scope['assessment_at'] if common_scope else known_at
            options = client.get(f'/api/v2/support/patients/{PATIENT_ID}/alert-evaluation-options',
                params={'mode': 'current', 'known_at': known_at})
            require(options.status_code == 200, f'Dynamic options returned HTTP {options.status_code}: {options.text}')
            plans = [plan for plan in options.json()['plans'] if plan['kind'] == 'dynamic']
            require(len(plans) == 1, 'Expected one qualified dynamic plan for the simulation patient.')
            plan = plans[0]
            options_report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'run_tag': args.run_tag,
                'patient_id': PATIENT_ID, 'known_at': known_at, 'status_code': options.status_code,
                'body': options.json(), 'business_fixture_sha256': fixture_sha256,
                'business_fixture_path': str(fixture_path.relative_to(ROOT))}
            save_json(OPTIONS_REPORT, options_report)
            authorizations = plan['use_authorization_ids']
            require(len(authorizations) == 1, 'Expected one qualified use authorization; choose only an unambiguous option.')
            payload = {'mode': 'current', 'assessment_at': assessment_at, 'known_at': known_at,
                'policy_id': plan['rule']['config_id'], 'input_rule_id': plan['input_rule']['config_id'],
                'verification_policy_id': plan['verification']['config_id'],
                'use_authorization_id': authorizations[0], 'window_policy_id': plan['window']['config_id'],
                'observation_period_id': None, 'clinical_selections': []}
            state.update({'known_at': known_at, 'assessment_at': assessment_at, 'run_tag': args.run_tag,
                'options_path': str(OPTIONS_REPORT.relative_to(ROOT)), 'options_status_code': options.status_code,
                'options_sha256': hashlib.sha256(OPTIONS_REPORT.read_bytes()).hexdigest(), 'options_plan': plan,
                'business_fixture_sha256': options_report['business_fixture_sha256'],
                'payload': payload, 'target_key': TARGET})
            if common_scope:
                state.update({'scope_sha256': scope_sha256, 'scope_path': str(scope_path.relative_to(ROOT))})
            save_json(STATE, state)

        if args.prepare_only:
            require(state.get('payload') is not None and OPTIONS_REPORT.exists()
                and state.get('intent_id') is None and state.get('submission_attempted', False) is False,
                'Prepare-only mode requires current saved options and no write intent.')
            print(json.dumps({'status': 'DYNAMIC_OPTIONS_PREPARED_NO_WRITE',
                'run_tag': args.run_tag, 'known_at': state['known_at'],
                'options_sha256': state['options_sha256'], 'request_id_reserved': state['request_id'],
                'write_intent_created': False, 'task_created': False, 'saved_state_path': str(STATE)}, sort_keys=True))
            return

        if state['intent_id'] is None:
            intent = client.post('/api/v2/write-intents', json={'operation_id': 'alert_evaluation_v2_create',
                'target_key': TARGET, 'request_id': state['request_id'], 'schema_version': '2.0.0', 'payload': state['payload']})
            require(intent.status_code == 201, f'Dynamic intent preparation returned HTTP {intent.status_code}: {intent.text}')
            prepared = intent.json()
            state.update({'intent_id': prepared['intent_id'], 'generation': prepared['generation']})
            save_json(STATE, state)

        lookup = client.get(f"/api/v2/requests/{state['request_id']}", params={
            'lookup': 'request_id', 'operation_id': 'alert_evaluation_v2_create', 'target_key': TARGET})
        if lookup.status_code == 404:
            require(lookup.json().get('code') == 'DYNAMIC_REQUEST_NOT_FOUND',
                f'Dynamic request lookup returned HTTP 404: {lookup.text}')
            preflight_path = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-dynamic-intent-{args.run_tag}-readback.json'
            preflight = json.loads(preflight_path.read_text(encoding='utf-8'))
            require(preflight['status'] == 'DYNAMIC_INTENT_PREPARED_NO_REQUEST'
                and preflight['intent_id'] == state['intent_id'] and preflight['request_id'] == state['request_id']
                and preflight['matching_request_count'] == 0 and preflight['matching_task_count'] == 0
                and preflight['matching_event_count'] == 0,
                'Saved database preflight does not prove a prepared intent without a request or task.')
            request_state = {'request_id': state['request_id'], 'state': 'not_created', 'may_retry': True}
        else:
            require(lookup.status_code == 200,
                f'Dynamic request lookup returned HTTP {lookup.status_code}: {lookup.text}')
            request_state = lookup.json()
            require(request_state['request_id'] == state['request_id'],
                'Dynamic request lookup returned another request.')
            require(request_state['state'] != 'unknown', 'Unknown dynamic request result cannot be retried.')
        if request_state['state'] == 'committed':
            state['task_id_value'] = request_state['receipt']['resource_id']
        else:
            first_submission_proven = False
            if request_state['state'] == 'pending' and request_state['may_retry'] is False:
                preflight_path = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-dynamic-intent-{args.run_tag}-readback.json'
                preflight = json.loads(preflight_path.read_text(encoding='utf-8'))
                api_snapshot = preflight['api_request_lookup']
                api_body = api_snapshot['body']
                require(not state.get('submission_attempted', False)
                    and preflight['status'] == 'DYNAMIC_INTENT_PREPARED_NO_REQUEST'
                    and preflight['intent_status'] == 'prepared'
                    and preflight['intent_write_request_id'] is None
                    and preflight['matching_request_count'] == 0
                    and preflight['matching_task_count'] == 0
                    and preflight['matching_event_count'] == 0
                    and preflight['clean_no_request_task_event'] is True
                    and preflight['intent_id'] == state['intent_id']
                    and preflight['request_id'] == state['request_id']
                    and preflight['known_at'] == state['known_at']
                    and api_snapshot['http_status'] == 200
                    and api_body['state'] == 'pending' and api_body['may_retry'] is False
                    and api_body['intent_id'] == state['intent_id']
                    and api_body['request_id'] == state['request_id']
                    and api_body['operation_id'] == 'alert_evaluation_v2_create'
                    and api_body['target_key'] == TARGET
                    and api_body['generation'] == state['generation']
                    and api_body['dynamic_input'] == state['payload'],
                    'First dynamic submission requires an exact prepared intent and zero matching database effects.')
                first_submission_proven = True
            require(request_state['state'] in ('not_created', 'pending')
                and (request_state['may_retry'] is True or first_submission_proven),
                f'Dynamic request state is not safe for submission: {request_state["state"]}.')
            require(not state.get('submission_attempted', False),
                'A dynamic submission was already attempted; do not submit again.')
            state['submission_attempted'] = True
            save_json(STATE, state)
            response = client.post(f'/api/v2{TARGET}', json=state['payload'], headers={
                'X-Write-Intent': state['intent_id'], 'Idempotency-Key': state['request_id'],
                'X-Intent-Generation': state['generation']})
            require(response.status_code == 202, f'Dynamic task create returned HTTP {response.status_code}: {response.text}')
            task_view = response.json()
            state['task_id_value'] = task_view['task_id']
            state['create_response'] = task_view
            save_json(STATE, state)
            lookup = client.get(f"/api/v2/requests/{state['request_id']}", params={
                'lookup': 'request_id', 'operation_id': 'alert_evaluation_v2_create', 'target_key': TARGET})
            require(lookup.status_code == 200 and lookup.json()['state'] == 'committed',
                'Dynamic request did not read back as committed.')
            request_state = lookup.json()

        task_view = read_task(client, state['task_id_value'])
        evaluation_view = read_evaluation(client, task_view['result_ref']) if task_view.get('result_ref') else None
        state.update({'request_read': request_state, 'task_read': task_view,
            'evaluation_read': evaluation_view})
        save_json(STATE, state)

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433), 'Database identity differs from approved simulation.')
        task = Task.objects.get(id=state['task_id_value'], patient_id=PATIENT_ID,
            task_type='alert_evaluation', schema_version='dynamic-evaluation-task-2')
        events = list(OutboxEvent.objects.filter(task=task, event_type='alert_evaluation.requested',
            schema_version='dynamic-evaluation-task-2').values('id', 'published_at', 'lease_generation'))
        require(len(events) == 1, 'Dynamic task must have one exact evaluation event.')
        event = {**events[0], 'id': str(events[0]['id'])}
        evaluation_rows = list(AlertEvaluation.objects.filter(task_id=task.id, patient_id=PATIENT_ID).values(
            'id', 'status', 'decision', 'validity', 'context_sequence', 'known_at', 'assessment_at', 'input_digest'))
        alert_events = list(AlertEvent.objects.filter(evaluation_id__in=[row['id'] for row in evaluation_rows],
            patient_id=PATIENT_ID).values('id', 'evaluation_id', 'validity', 'release_id', 'created_at'))
        alert_event_ids = [row['id'] for row in alert_events]
        alert_outbox = list(OutboxEvent.objects.filter(event_type='alert.event_created',
            schema_version='dynamic-family-event-2', aggregate_id__in=alert_event_ids).values(
                'id', 'aggregate_id', 'published_at', 'lease_generation', 'resource_refs'))
        recipients = list(AlertRecipient.objects.filter(patient_id=PATIENT_ID,
            alert_id__in=alert_event_ids).values('id', 'alert_id', 'relation_id', 'channel',
                'delivery_status', 'read_at'))
        inventory = {'task_id': str(task.id), 'task_status': task.status, 'task_input_sha256': task.input_sha256,
            'request_id': str(task.request_row.request_id), 'outbox_event': event,
            'task_api_readback': state['task_read'], 'evaluation_api_readback': state['evaluation_read'],
            'evaluations': [{**row, 'id': str(row['id']), 'known_at': row['known_at'].isoformat(),
                'assessment_at': row['assessment_at'].isoformat()} for row in evaluation_rows],
            'alert_events': [{**row, 'id': str(row['id']), 'evaluation_id': str(row['evaluation_id']),
                'release_id': str(row['release_id']), 'created_at': row['created_at'].isoformat()} for row in alert_events],
            'alert_event_outbox': [{**row, 'id': str(row['id']), 'aggregate_id': str(row['aggregate_id'])}
                for row in alert_outbox],
            'alert_recipients': [{**row, 'id': str(row['id']), 'alert_id': str(row['alert_id']),
                'relation_id': str(row['relation_id']), 'read_at': row['read_at'].isoformat() if row['read_at'] else None}
                for row in recipients]}

    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'DYNAMIC_TASK_READBACK_COMPLETE',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433}, 'api_base': API_BASE,
        'patient_id': PATIENT_ID, 'known_at': state['known_at'], 'assessment_at': state['assessment_at'],
        'options_sha256': state['options_sha256'], 'run_tag': args.run_tag,
        'business_fixture_sha256': state['business_fixture_sha256'], 'request_id': state['request_id'],
        'business_fixture_path': str(fixture_path.relative_to(ROOT)),
        'scope_sha256': state.get('scope_sha256'),
        'intent_id': state['intent_id'], 'task': inventory, 'model_calls': 0,
        'external_calls': 0, 'task_execution': 'READ_FROM_SAVED_TASK_STATE'}
    save_json(REPORT, report)
    print(json.dumps({'status': report['status'], 'task_id': inventory['task_id'],
        'task_status': inventory['task_status'], 'outbox_event_id': event['id'],
        'saved_and_read': True, 'report_path': str(REPORT)}, sort_keys=True))


if __name__ == '__main__':
    main()
