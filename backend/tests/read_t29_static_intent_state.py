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
from tasks.models import OutboxEvent, Task, TaskAttempt


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + '\n'
    path.write_text(encoded, encoding='utf-8')
    require(json.loads(path.read_text(encoding='utf-8')) == json.loads(encoded),
        f'Saved report readback differs: {path.name}.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--kind', choices=('classification', 'ms'), default='classification')
    args = parser.parse_args()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{2,47}', args.run_tag) is not None,
        'Run tag must be a unique lowercase alphanumeric label with optional hyphens.')
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    root = ROOT / '.planning-work' / 'parallel' / 'window1'
    state_path = root / f't29-static-task-{args.run_tag}-state.json'
    report_path = root / f't29-static-intent-{args.run_tag}-readback.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    item = state['tasks'][args.kind]
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            identity = cursor.fetchone()
            require(identity == ('sjia_simulation', 'sjia', 5433),
                'Database identity differs from approved simulation.')
        intent = WriteIntent.objects.get(id=item['intent_id'], request_id=item['request_id'],
            operation_id='assessment_v2_create', target_key='/support/patients/' + state['patient_id'] + '/assessments')
        requests = list(WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
            target_key=intent.target_key, request_id=intent.request_id).values(
            'id', 'status', 'resource_type', 'resource_id', 'error_code'))
        tasks = list(Task.objects.filter(request_row__actor_id=intent.actor_id,
            request_row__action=intent.operation_id, request_row__target_key=intent.target_key,
            request_row__request_id=intent.request_id).values('id', 'status', 'status_version',
                'input_sha256', 'deadline_at', 'lease_generation'))
        task_ids = [row['id'] for row in tasks]
        events = list(OutboxEvent.objects.filter(aggregate_id__in=task_ids,
            event_type='assessment.requested').values('id', 'aggregate_id', 'published_at', 'lease_generation'))
        attempts = list(TaskAttempt.objects.filter(task_id__in=task_ids).values('id', 'task_id',
            'attempt_number', 'lease_generation', 'status', 'error_code', 'started_at', 'finished_at'))
        clean = intent.status == WriteIntent.Status.PREPARED and intent.write_request_id is None \
            and not requests and not tasks and not events
        report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY',
            'status': 'STATIC_INTENT_PREPARED_NO_REQUEST' if clean else 'STATIC_INTENT_HAS_COMMITTED_OR_PENDING_ROWS',
            'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
            'run_tag': args.run_tag, 'known_at': state['known_at'], 'request_id': item['request_id'],
            'intent_id': item['intent_id'], 'intent_status': intent.status,
            'intent_write_request_id': str(intent.write_request_id) if intent.write_request_id else None,
            'matching_request_count': len(requests), 'matching_task_count': len(tasks),
            'matching_event_count': len(events), 'matching_requests': requests,
            'matching_tasks': tasks, 'matching_events': events, 'matching_attempts': attempts, 'read_only': True,
            'read_at': datetime.now().astimezone().isoformat(),
            'api_request_lookup': {'performed': False, 'http_status': None, 'error_code': None,
                'result': 'NOT_QUERIED_BY_THIS_READ_ONLY_CHECK'},
            'clean_no_request_task_event': clean, 'task_kind': args.kind}
    save_json(report_path, report)
    print(json.dumps({'status': report['status'], 'intent_status': report['intent_status'],
        'matching_request_count': report['matching_request_count'],
        'matching_task_count': report['matching_task_count'], 'matching_event_count': report['matching_event_count'],
        'saved_and_read': True, 'report_path': str(report_path)}, sort_keys=True))


if __name__ == '__main__':
    main()
