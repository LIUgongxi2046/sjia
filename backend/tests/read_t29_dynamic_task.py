import json
import os
import sys
from datetime import datetime
from pathlib import Path

import django
import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from requests.models import WriteIntent, WriteRequest
from tasks.models import OutboxEvent, Task


RUN_TAG = 'new-source-20261009-1'
STATE_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-dynamic-task-{RUN_TAG}-state.json'
REPORT_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / f't29-dynamic-task-{RUN_TAG}-readback.json'
API_BASE = 'http://127.0.0.1:8001'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'
    path.write_text(encoded, encoding='utf-8')
    reread = json.loads(path.read_text(encoding='utf-8'))
    require(reread == json.loads(encoded), f'Saved report readback differs: {path.name}.')


def main():
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    require(state['task_id_value'] and state['submission_attempted'],
        'Saved state has no submitted dynamic task identity.')
    with httpx.Client(base_url=API_BASE, timeout=45, trust_env=False) as client:
        bootstrap = client.get('/api/auth/bootstrap')
        require(bootstrap.status_code == 200, f'Simulation bootstrap returned HTTP {bootstrap.status_code}.')
        client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
        login = client.post('/api/v2/support/session/login', json={
            'username': 'simulation-support', 'password': os.environ['SJIA_SIMULATION_PASSWORD']})
        require(login.status_code == 200 and login.json().get('audience') == 'support',
            f'Support login returned HTTP {login.status_code}.')
        client.headers['X-CSRFToken'] = login.json()['csrf_token']
        task_response = client.get(f"/api/v2/support/alert-evaluation-tasks/{state['task_id_value']}")
        require(task_response.status_code == 200,
            f'Dynamic task read returned HTTP {task_response.status_code}: {task_response.text}')
        task_api = task_response.json()
        require(task_api['task_id'] == state['task_id_value'], 'Dynamic task API returned another task.')
        request_response = client.get(f"/api/v2/requests/{state['intent_id']}", params={
            'lookup': 'request_id', 'operation_id': 'alert_evaluation_v2_create',
            'target_key': state['target_key']})
        request_api = {'status_code': request_response.status_code, 'body': request_response.json()}

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            database_identity = cursor.fetchone()
            require(database_identity == ('sjia_simulation', 'sjia', 5433),
                'Database identity differs from approved simulation.')
        task = Task.objects.get(id=state['task_id_value'], patient_id=state['patient_id'],
            task_type='alert_evaluation', schema_version='dynamic-evaluation-task-2')
        intent = WriteIntent.objects.get(id=state['intent_id'], request_id=state['request_id'],
            operation_id='alert_evaluation_v2_create', target_key=state['target_key'])
        requests = list(WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
            target_key=intent.target_key, request_id=intent.request_id).values(
            'id', 'status', 'resource_type', 'resource_id', 'error_code'))
        events = list(OutboxEvent.objects.filter(aggregate_id=task.id,
            event_type='alert_evaluation.requested', schema_version='dynamic-evaluation-task-2').values(
            'id', 'event_type', 'schema_version', 'published_at', 'lease_generation'))
        require(len(events) == 1, 'Dynamic task must have exactly one event.')
        require(task.request_row_id is not None, 'Dynamic task has no saved write request.')
        database = {'name': connection.settings_dict['NAME'], 'schema': 'sjia', 'port': 5433,
            'task_id': str(task.id), 'task_status': task.status, 'task_input_sha256': task.input_sha256,
            'intent_status': intent.status, 'intent_write_request_id': str(intent.write_request_id)
            if intent.write_request_id else None,
            'requests': [{**row, 'id': str(row['id']), 'resource_id': str(row['resource_id'])
                if row['resource_id'] else None} for row in requests],
            'outbox_events': [{**row, 'id': str(row['id'])} for row in events]}

    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'DYNAMIC_TASK_PERSISTED_READBACK',
        'run_tag': RUN_TAG, 'known_at': state['known_at'], 'assessment_at': state['assessment_at'],
        'business_fixture_sha256': state['business_fixture_sha256'], 'options_sha256': state['options_sha256'],
        'patient_id': state['patient_id'], 'request_id': state['request_id'], 'intent_id': state['intent_id'],
        'task_api_readback': task_api, 'request_api_readback': request_api, 'database_readback': database,
        'read_only_verification': True, 'model_calls': 0, 'external_calls': 0,
        'read_at': datetime.now().astimezone().isoformat()}
    save_json(REPORT_PATH, report)
    print(json.dumps({'status': report['status'], 'task_id': task_api['task_id'],
        'task_status': task_api['status'], 'event_id': database['outbox_events'][0]['id'],
        'request_api_status': request_api['status_code'], 'saved_and_read': True,
        'report_path': str(REPORT_PATH)}, sort_keys=True))


if __name__ == '__main__':
    main()
