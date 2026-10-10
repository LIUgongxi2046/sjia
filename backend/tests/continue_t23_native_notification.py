import argparse
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path

import django
import httpx


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.db import connection, transaction
from notifications.models import NotificationAttempt, NotificationReceipt
from alerting.models import AlertRecipient
from tasks.models import ConsumerReceipt, OutboxEvent, Task


API_BASE = 'http://127.0.0.1:8001'
STATE_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / 't23-native-delivery-state.json'
REPORT_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / 't23-native-delivery-readback.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    path.write_text(encoded, encoding='utf-8')
    require(json.loads(path.read_text(encoding='utf-8')) == json.loads(encoded),
        f'Saved JSON readback differs: {path.name}.')


def support_client(username):
    client = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    bootstrap = client.get('/api/auth/bootstrap')
    require(bootstrap.status_code == 200, f'Support bootstrap returned HTTP {bootstrap.status_code}.')
    client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
    login = client.post('/api/v2/support/session/login', json={
        'username': username, 'password': os.environ['SJIA_SIMULATION_PASSWORD']})
    require(login.status_code == 200 and login.json().get('audience') == 'support',
        f'Support login returned HTTP {login.status_code}.')
    client.headers['X-CSRFToken'] = login.json()['csrf_token']
    return client


def family_client(username):
    client = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    bootstrap = client.get('/api/auth/bootstrap')
    require(bootstrap.status_code == 200, f'Family bootstrap returned HTTP {bootstrap.status_code}.')
    login = client.post('/api/auth/login', json={
        'username': username, 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
    require(login.status_code == 200 and login.json().get('audience') == 'family',
        f'Family login returned HTTP {login.status_code}.')
    client.headers['X-CSRFToken'] = login.json()['csrf_token']
    return client


def api_read(client, recipient_id, support):
    prefix = '/api/v2/support/notification-records' if support else '/api/v2/notification-records'
    response = client.get(f'{prefix}/{recipient_id}')
    require(response.status_code == 200,
        f'Notification record read returned HTTP {response.status_code}: {response.text}')
    value = response.json()
    require(value['recipient_id'] == recipient_id, 'Notification API returned another recipient.')
    return value


def main():
    global STATE_PATH, REPORT_PATH
    parser = argparse.ArgumentParser()
    parser.add_argument('--recipient-id', required=True)
    parser.add_argument('--run-tag', required=True)
    parser.add_argument('--fixture-file', default=str(ROOT / '.simulation-work' / 't29-business-fixture.json'))
    args = parser.parse_args()
    require(len(args.run_tag) <= 48 and args.run_tag.replace('-', '').isalnum()
        and args.run_tag == args.run_tag.lower(), 'Run tag must be a lowercase alphanumeric label.')
    report_root = ROOT / '.planning-work' / 'parallel' / 'window1'
    STATE_PATH = report_root / f't23-native-delivery-{args.run_tag}-state.json'
    REPORT_PATH = report_root / f't23-native-delivery-{args.run_tag}-readback.json'
    recipient_id = str(uuid.UUID(args.recipient_id))
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    fixture_path = Path(args.fixture_file).resolve()
    require(fixture_path.is_relative_to((ROOT / '.simulation-work').resolve()),
        'T23 fixture must be saved in the simulation-work directory.')
    fixture = json.loads(fixture_path.read_text(encoding='utf-8'))
    require(fixture.get('mode') == 'SIMULATION_ONLY', 'T23 requires a marked simulation fixture.')
    fixture['patient_id'] = str(uuid.UUID(fixture['patient_id']))
    require(isinstance(fixture.get('family_username'), str) and fixture['family_username'].strip(),
        'T23 fixture must identify its simulation family account.')
    support_username = fixture.get('support_username', 'simulation-support')
    fixture_sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433),
                'Database identity differs from approved simulation.')
        recipient = AlertRecipient.objects.get(id=recipient_id, channel='in_app')
        recipient_identity = {'patient_id': str(recipient.patient_id), 'alert_id': str(recipient.alert_id),
            'relation_id': str(recipient.relation_id)}
    require(fixture['patient_id'] == recipient_identity['patient_id'],
        'Recipient does not belong to the marked simulation fixture patient.')
    require(fixture.get('family_username'), 'Simulation fixture has no family account identifier.')

    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        require(state['recipient_id'] == recipient_id and state['fixture_sha256'] == fixture_sha256
            and state['family_username'] == fixture['family_username']
            and state.get('support_username', 'simulation-support') == support_username,
            'Saved T23 continuation belongs to another recipient or fixture.')
    else:
        state = {'task_id': 'T23', 'mode': 'SIMULATION_ONLY', 'recipient_id': recipient_id,
            **recipient_identity, 'fixture_sha256': fixture_sha256,
            'fixture_path': str(fixture_path.relative_to(ROOT)),
            'family_username': fixture['family_username'], 'support_username': support_username,
            'delivery_attempted': False,
            'read_attempted': False, 'external_calls': 0, 'model_calls': 0}
        save_json(STATE_PATH, state)

    support = support_client(support_username)
    support_record = api_read(support, recipient_id, True)
    support.close()
    require(support_record['patient_id'] == recipient_identity['patient_id']
        and support_record['alert_id'] == recipient_identity['alert_id']
        and support_record['delivery_status'] in ('accepted', 'delivered')
        and len(support_record['attempts']) == 1,
        'Main-window native notification must have one persisted accepted attempt before delivery.')
    attempt = support_record['attempts'][0]
    require(any(item['status'] == 'accepted' for item in attempt['receipts']),
        'Accepted signed receipt is not available in the support API record.')
    state['support_initial_record'] = support_record
    state['attempt_id'] = attempt['attempt_id']
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        recipient_row = AlertRecipient.objects.get(id=recipient_id, patient_id=recipient_identity['patient_id'])
        attempt_row = NotificationAttempt.objects.get(id=state['attempt_id'], recipient_id=recipient_row.id)
        receipt_rows = list(NotificationReceipt.objects.filter(attempt=attempt_row).order_by('status').values(
            'id', 'status', 'provider_event_id', 'evidence_sha256', 'occurred_at'))
        notification_task = Task.objects.get(id=attempt_row.send_permit_id, task_type='notification')
        notification_events = list(OutboxEvent.objects.filter(task_id=notification_task.id,
            event_type='notification.native_requested').values('id', 'published_at', 'lease_generation', 'resource_refs'))
        notification_consumed = list(ConsumerReceipt.objects.filter(consumer='native_notifications',
            event__aggregate_id=notification_task.id).values('event_id', 'effect_resource_id'))
        alert_consumed = list(ConsumerReceipt.objects.filter(consumer='native_notifications',
            event__aggregate_id=recipient_identity['alert_id'], event__event_type='alert.event_created').values(
                'event_id', 'effect_resource_id'))
        require(attempt_row.status == 'accepted' and notification_task.status == 'completed'
            and len(receipt_rows) == 1 and receipt_rows[0]['status'] == 'accepted'
            and len(notification_events) == 1 and len(notification_consumed) == 1 and len(alert_consumed) == 1,
            'Exact simulated notification attempt, accepted receipt, Outbox or consumer receipt is inconsistent.')
        database_readback = {'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
            'recipient_id': str(recipient_row.id), 'attempt': {'id': str(attempt_row.id),
                'status': attempt_row.status, 'attempt_number': attempt_row.attempt_number,
                'send_permit_id': str(attempt_row.send_permit_id)},
            'task': {'id': str(notification_task.id), 'status': notification_task.status,
                'input_sha256': notification_task.input_sha256},
            'outbox_events': [{**row, 'id': str(row['id'])} for row in notification_events],
            'receipts': [{**row, 'id': str(row['id']), 'occurred_at': row['occurred_at'].isoformat()}
                for row in receipt_rows],
            'notification_consumer_receipts': [{**row, 'event_id': str(row['event_id']),
                'effect_resource_id': str(row['effect_resource_id'])} for row in notification_consumed],
            'alert_event_consumer_receipts': [{**row, 'event_id': str(row['event_id']),
                'effect_resource_id': str(row['effect_resource_id'])} for row in alert_consumed]}
    save_json(STATE_PATH, state)

    family = family_client(fixture['family_username'])
    family_record = api_read(family, recipient_id, False)
    require(family_record['alert_id'] == recipient_identity['alert_id'],
        'Family notification read returned another alert.')
    state['family_initial_record'] = family_record
    save_json(STATE_PATH, state)

    if family_record['delivery_status'] != 'delivered':
        require(not state['delivery_attempted'],
            'Delivery acknowledgement outcome is unknown; re-read only and do not send it again.')
        message_response = family.get(f'/api/v2/notification-records/{recipient_id}/message')
        if message_response.status_code != 200:
            state['family_message_read'] = {'http_status': message_response.status_code,
                'response': message_response.json(), 'delivery_status': family_record['delivery_status'],
                'current_validity': family_record['current_validity'],
                'content_state': family_record['content_state']}
            save_json(STATE_PATH, state)
            report = {'task_id': 'T23', 'mode': 'SIMULATION_ONLY',
                'status': 'FAMILY_CONTENT_UNAVAILABLE_CURRENT_CONFIGURATION',
                'api_base': API_BASE, 'patient_id': recipient_identity['patient_id'],
                'alert_id': recipient_identity['alert_id'], 'recipient_id': recipient_id,
                'attempt_id': state['attempt_id'], 'fixture_sha256': fixture_sha256,
                'support_api_record': support_record, 'family_api_record': family_record,
                'database_readback': database_readback,
                'family_message_response': state['family_message_read'],
                'delivery': 'NOT_RUN', 'read': 'NOT_RUN', 'external_calls': 0, 'model_calls': 0}
            save_json(REPORT_PATH, report)
            family.close()
            print(json.dumps({'status': report['status'], 'recipient_id': recipient_id,
                'http_status': message_response.status_code,
                'error_code': message_response.json().get('code'), 'delivery': 'NOT_RUN',
                'read': 'NOT_RUN', 'saved_and_read': True, 'report_path': str(REPORT_PATH)},
                ensure_ascii=False, sort_keys=True))
            return
        message = message_response.json()
        require(message['recipient_id'] == recipient_id and message['alert_id'] == recipient_identity['alert_id']
            and message['body']['approved_text'], 'Family message identity or approved content is invalid.')
        state['delivery_attempted'] = True
        save_json(STATE_PATH, state)
        delivery = family.post(f'/api/v2/notification-records/{recipient_id}/delivered',
            json={'delivery_ticket': message['delivery_ticket']})
        require(delivery.status_code == 200,
            f'Family delivery acknowledgement returned HTTP {delivery.status_code}: {delivery.text}')
        state['delivery_response'] = delivery.json()
        save_json(STATE_PATH, state)

    current = api_read(family, recipient_id, False)
    if current['read_at'] is None:
        require(not state['read_attempted'], 'Family read outcome is unknown; do not submit the read action again.')
        state['read_attempted'] = True
        save_json(STATE_PATH, state)
        alert_read = family.post(f'/api/alerts/{recipient_identity["alert_id"]}/read', json={})
        require(alert_read.status_code == 200,
            f'Family alert read returned HTTP {alert_read.status_code}: {alert_read.text}')
        state['family_alert_read'] = alert_read.json()
        save_json(STATE_PATH, state)
    final_family = api_read(family, recipient_id, False)
    state['family_final_record'] = final_family
    save_json(STATE_PATH, state)
    family.close()

    require(final_family['delivery_status'] == 'delivered' and final_family['read_at'] is not None
        and len(final_family['attempts']) == 1, 'Family delivery or read state did not persist.')
    receipt_statuses = {item['status'] for item in final_family['attempts'][0]['receipts']}
    require(receipt_statuses == {'accepted', 'delivered'},
        'Accepted and delivered receipt statuses are not both present.')

    with transaction.atomic():
        recipient = AlertRecipient.objects.get(id=recipient_id, patient_id=recipient_identity['patient_id'])
        attempt_row = NotificationAttempt.objects.get(id=state['attempt_id'], recipient_id=recipient.id)
        receipts = list(NotificationReceipt.objects.filter(attempt=attempt_row).order_by('status').values(
            'id', 'status', 'provider_event_id', 'evidence_sha256', 'occurred_at'))
        task = Task.objects.get(id=attempt_row.send_permit_id, task_type='notification')
        events = list(OutboxEvent.objects.filter(task_id=task.id,
            event_type='notification.native_requested').values('id', 'published_at', 'lease_generation', 'resource_refs'))
        consumed = list(ConsumerReceipt.objects.filter(consumer='native_notifications',
            event__aggregate_id=task.id).values('event_id', 'effect_resource_id'))
        alert_consumed = list(ConsumerReceipt.objects.filter(consumer='native_notifications',
            event__aggregate_id=recipient.alert_id, event__event_type='alert.event_created').values(
            'event_id', 'effect_resource_id'))
        require(recipient.delivery_status == 'delivered' and recipient.read_at is not None
            and len(receipts) == 2 and {row['status'] for row in receipts} == {'accepted', 'delivered'}
            and task.status == 'completed' and len(events) == 1 and consumed and alert_consumed,
            'Persisted notification Task, Outbox, ConsumerReceipt or signed receipt state is incomplete.')
        database_readback = {'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
            'recipient': {'id': str(recipient.id), 'patient_id': str(recipient.patient_id),
                'alert_id': str(recipient.alert_id), 'relation_id': str(recipient.relation_id),
                'delivery_status': recipient.delivery_status, 'read_at': recipient.read_at.isoformat()},
            'attempt': {'id': str(attempt_row.id), 'status': attempt_row.status,
                'attempt_number': attempt_row.attempt_number, 'send_permit_id': str(attempt_row.send_permit_id)},
            'task': {'id': str(task.id), 'status': task.status, 'input_sha256': task.input_sha256},
            'outbox_events': [{**row, 'id': str(row['id'])} for row in events],
            'receipts': [{**row, 'id': str(row['id']), 'occurred_at': row['occurred_at'].isoformat()}
                for row in receipts],
            'notification_consumer_receipts': [{**row, 'event_id': str(row['event_id']),
                'effect_resource_id': str(row['effect_resource_id'])} for row in consumed],
            'alert_event_consumer_receipts': [{**row, 'event_id': str(row['event_id']),
                'effect_resource_id': str(row['effect_resource_id'])} for row in alert_consumed]}

    report = {'task_id': 'T23', 'mode': 'SIMULATION_ONLY', 'status': 'NATIVE_SEND_DELIVERED_AND_READ',
        'api_base': API_BASE, 'patient_id': recipient_identity['patient_id'], 'alert_id': recipient_identity['alert_id'],
        'recipient_id': recipient_id, 'attempt_id': state['attempt_id'], 'fixture_sha256': fixture_sha256,
        'support_api_record': support_record, 'family_api_record': final_family,
        'database_readback': database_readback, 'external_calls': 0, 'model_calls': 0}
    save_json(STATE_PATH, state)
    save_json(REPORT_PATH, report)
    print(json.dumps({'status': report['status'], 'recipient_id': recipient_id,
        'attempt_id': state['attempt_id'], 'receipt_statuses': sorted(receipt_statuses),
        'family_read_at': final_family['read_at'], 'saved_and_read': True,
        'report_path': str(REPORT_PATH)}, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
