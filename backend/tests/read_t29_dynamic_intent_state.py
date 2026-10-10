import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import django
import httpx


ROOT = Path(__file__).resolve().parents[2]
API_BASE = 'http://127.0.0.1:8001'
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from requests.models import WriteIntent, WriteRequest
from tasks.models import OutboxEvent, Task


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label with optional hyphens.')
    report_root = ROOT / '.planning-work' / 'parallel' / 'window1'
    state_path = report_root / f't29-dynamic-task-{args.run_tag}-state.json'
    report_path = report_root / f't29-dynamic-intent-{args.run_tag}-readback.json'
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    state = json.loads(state_path.read_text(encoding='utf-8'))
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            identity = cursor.fetchone()
            require(identity == ('sjia_simulation', 'sjia', 5433),
                'Database identity differs from approved simulation.')
        intent = WriteIntent.objects.get(id=state['intent_id'], request_id=state['request_id'],
            operation_id='alert_evaluation_v2_create', target_key=state['target_key'])
        requests = list(WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
            target_key=intent.target_key, request_id=intent.request_id).values(
            'id', 'status', 'resource_type', 'resource_id', 'error_code'))
        tasks = list(Task.objects.filter(request_row__actor_id=intent.actor_id,
            request_row__action=intent.operation_id, request_row__target_key=intent.target_key,
            request_row__request_id=intent.request_id).values('id', 'status', 'input_sha256'))
        events = list(OutboxEvent.objects.filter(aggregate_id__in=[row['id'] for row in tasks],
            event_type='alert_evaluation.requested').values('id', 'aggregate_id', 'published_at', 'lease_generation'))
        clean = intent.status == WriteIntent.Status.PREPARED and intent.write_request_id is None \
            and not requests and not tasks and not events
        database_state = {'intent_status': intent.status,
            'intent_write_request_id': str(intent.write_request_id) if intent.write_request_id else None,
            'matching_request_count': len(requests), 'matching_task_count': len(tasks),
            'matching_event_count': len(events),
            'matching_requests': [{**row, 'id': str(row['id']),
                'resource_id': str(row['resource_id']) if row['resource_id'] else None} for row in requests],
            'matching_tasks': [{**row, 'id': str(row['id'])} for row in tasks],
            'matching_events': [{**row, 'id': str(row['id']), 'aggregate_id': str(row['aggregate_id'])}
                for row in events], 'clean_no_request_task_event': clean}

    with httpx.Client(base_url=API_BASE, timeout=30, trust_env=False) as client:
        bootstrap = client.get('/api/auth/bootstrap')
        require(bootstrap.status_code == 200, f'Simulation API bootstrap returned HTTP {bootstrap.status_code}.')
        client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
        login = client.post('/api/v2/support/session/login', json={
            'username': 'simulation-support', 'password': os.environ['SJIA_SIMULATION_PASSWORD']})
        require(login.status_code == 200 and login.json().get('audience') == 'support',
            f'Support login returned HTTP {login.status_code}.')
        client.headers['X-CSRFToken'] = login.json()['csrf_token']
        lookup = client.get(f"/api/v2/requests/{state['request_id']}", params={
            'lookup': 'request_id', 'operation_id': intent.operation_id, 'target_key': intent.target_key})
        require(lookup.status_code in (200, 404),
            f'Dynamic request lookup returned HTTP {lookup.status_code}: {lookup.text}')
        api_state = {'http_status': lookup.status_code, 'body': lookup.json()}
        if lookup.status_code == 200:
            require(api_state['body'].get('request_id') == state['request_id'],
                'Dynamic request lookup returned another request identity.')

    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY',
            'status': 'DYNAMIC_INTENT_PREPARED_NO_REQUEST' if clean else 'DYNAMIC_INTENT_HAS_COMMITTED_OR_PENDING_ROWS',
            'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
            'api_base': API_BASE, 'run_tag': args.run_tag, 'known_at': state['known_at'], 'request_id': state['request_id'],
            'intent_id': state['intent_id'], 'intent_status': intent.status,
            **database_state, 'api_request_lookup': api_state,
            'read_only': True, 'read_at': datetime.now().astimezone().isoformat()}
    if report['intent_status'] == WriteIntent.Status.COMMITTED:
        require(len(requests) == 1 and len(tasks) == 1 and len(events) == 1,
            'Committed dynamic intent must have one matching request, task, and event.')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    reread = json.loads(report_path.read_text(encoding='utf-8'))
    require(reread == report, 'Saved dynamic intent readback differs.')
    print(json.dumps({'status': report['status'], 'intent_status': report['intent_status'],
        'matching_request_count': report['matching_request_count'],
        'matching_task_count': report['matching_task_count'], 'matching_event_count': report['matching_event_count'],
        'api_http_status': report['api_request_lookup']['http_status'],
        'api_request_state': report['api_request_lookup']['body'].get('state'),
        'api_may_retry': report['api_request_lookup']['body'].get('may_retry'),
        'saved_and_read': True, 'report_path': str(report_path)}, sort_keys=True))


if __name__ == '__main__':
    main()
