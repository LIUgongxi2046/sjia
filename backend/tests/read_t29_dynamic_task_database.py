import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import django


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from requests.models import WriteIntent, WriteRequest
from tasks.models import OutboxEvent, Task


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
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label with optional hyphens.')
    report_root = ROOT / '.planning-work' / 'parallel' / 'window1'
    state_path = report_root / f't29-dynamic-task-{args.run_tag}-state.json'
    report_path = report_root / f't29-dynamic-task-{args.run_tag}-database-readback.json'
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    state = json.loads(state_path.read_text(encoding='utf-8'))
    require(state['task_id_value'] and state['submission_attempted'],
        'Saved state has no submitted dynamic task identity.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            identity = cursor.fetchone()
            require(identity == ('sjia_simulation', 'sjia', 5433),
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
    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'DYNAMIC_TASK_DATABASE_READBACK',
        'run_tag': args.run_tag, 'known_at': state['known_at'], 'assessment_at': state['assessment_at'],
        'business_fixture_sha256': state['business_fixture_sha256'], 'options_sha256': state['options_sha256'],
        'patient_id': state['patient_id'], 'request_id': state['request_id'], 'intent_id': state['intent_id'],
        'database_readback': database, 'read_only_verification': True,
        'read_at': datetime.now().astimezone().isoformat()}
    save_json(report_path, report)
    print(json.dumps({'status': report['status'], 'task_id': database['task_id'],
        'task_status': database['task_status'], 'event_id': database['outbox_events'][0]['id'],
        'published_at': database['outbox_events'][0]['published_at'],
        'lease_generation': database['outbox_events'][0]['lease_generation'],
        'saved_and_read': True, 'report_path': str(report_path)}, sort_keys=True))


if __name__ == '__main__':
    main()
