import argparse
import json
import hashlib
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
from alerting.models import AlertRecipient
from notifications.models import NotificationAttempt, NotificationReceipt
from requests.models import WriteIntent
from tasks.models import ConsumerReceipt, OutboxEvent, Task


API_BASE = 'http://127.0.0.1:8001'
STATE_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / 't23-notification-state.json'
REPORT_PATH = ROOT / '.planning-work' / 'parallel' / 'window1' / 't23-notification-roundtrip.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    reread = json.loads(path.read_text(encoding='utf-8'))
    require(reread == value, f'Saved JSON readback differs: {path.name}.')


def support_client(username):
    client = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    login = client.post('/api/v2/support/session/login', json={
        'username': username, 'password': os.environ['SJIA_SIMULATION_PASSWORD']})
    require(login.status_code == 200 and login.json().get('audience') == 'support',
        f'Support login returned HTTP {login.status_code}.')
    authenticated = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    authenticated.cookies.update(client.cookies)
    authenticated.headers['X-CSRFToken'] = login.json()['csrf_token']
    client.close()
    return authenticated


def family_client(username):
    client = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    bootstrap = client.get('/api/auth/bootstrap')
    require(bootstrap.status_code == 200, f'Family bootstrap returned HTTP {bootstrap.status_code}.')
    login = client.post('/api/auth/login', json={
        'username': username, 'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
    require(login.status_code == 200 and login.json().get('audience') == 'family',
        f'Family login returned HTTP {login.status_code}.')
    authenticated = httpx.Client(base_url=API_BASE, timeout=45, trust_env=False)
    authenticated.cookies.update(client.cookies)
    authenticated.headers['X-CSRFToken'] = login.json()['csrf_token']
    client.close()
    return authenticated


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
    STATE_PATH = report_root / f't23-notification-{args.run_tag}-state.json'
    REPORT_PATH = report_root / f't23-notification-{args.run_tag}-roundtrip.json'
    recipient_id = str(uuid.UUID(args.recipient_id))
    require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
    fixture_path = Path(args.fixture_file).resolve()
    require(fixture_path.is_relative_to((ROOT / '.simulation-work').resolve()),
        'T23 fixture must be saved in the simulation-work directory.')
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    require(fixture.get('mode') == 'SIMULATION_ONLY', 'T23 requires an explicitly marked simulation fixture.')
    fixture['patient_id'] = str(uuid.UUID(fixture['patient_id']))
    require(isinstance(fixture.get('family_username'), str) and fixture['family_username'].strip(),
        'T23 fixture must identify its simulation family account.')
    support_username = fixture.get('support_username', 'simulation-support')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433), 'Database identity differs from approved simulation.')
        recipient = AlertRecipient.objects.get(id=recipient_id, channel='in_app')
        event_id = str(recipient.alert_id)
        patient_id = str(recipient.patient_id)

    require(fixture.get('mode') == 'SIMULATION_ONLY' and fixture.get('patient_id') == patient_id,
        'T23 recipient must belong to the current simulated business fixture patient.')
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()

    if STATE_PATH.exists():
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        require(state['recipient_id'] == recipient_id, 'Existing T23 state belongs to another recipient.')
        require(state.get('fixture_sha256') == fixture_sha256
            and state.get('family_username') == fixture['family_username']
            and state.get('support_username', 'simulation-support') == support_username,
            'Existing T23 state belongs to another simulation fixture or family account.')
    else:
        state = {'task_id': 'T23', 'mode': 'SIMULATION_ONLY', 'recipient_id': recipient_id,
            'alert_id': event_id, 'patient_id': patient_id, 'request_id': str(uuid.uuid4()),
            'family_username': fixture['family_username'], 'fixture_sha256': fixture_sha256,
            'fixture_path': str(fixture_path.relative_to(ROOT)), 'run_tag': args.run_tag,
            'support_username': support_username,
            'intent_id': None, 'generation': None, 'send_submitted': False,
            'delivery_acknowledged': False, 'model_calls': 0, 'external_calls': 0}
        save_json(STATE_PATH, state)

    with support_client(support_username) as support:
        record_response = support.get(f'/api/v2/support/notification-records/{recipient_id}')
        require(record_response.status_code == 200, f'Support record read returned HTTP {record_response.status_code}.')
        current_record = record_response.json()
        require(current_record['patient_id'] == patient_id and current_record['alert_id'] == event_id,
            'Notification record identity differs from the saved simulation recipient.')
        state['initial_record'] = current_record
        if state['intent_id'] is None:
            require(current_record['delivery_status'] == 'not_sent' and current_record['attempts'] == [],
                'Recipient already has an attempt; do not begin another T23 send.')
            target = f'/support/notification-records/{recipient_id}/send'
            body = {'operation_id': 'notification_v2_send', 'target_key': target,
                'request_id': state['request_id'], 'schema_version': '2.0.0',
                'payload': {'expected_snapshot': current_record['snapshot_version']}}
            intent_response = support.post('/api/v2/write-intents', json=body)
            require(intent_response.status_code == 201,
                f'Notification intent preparation returned HTTP {intent_response.status_code}: {intent_response.text}')
            prepared = intent_response.json()
            state.update({'intent_id': prepared['intent_id'], 'generation': prepared['generation'],
                'target_key': target, 'send_body': body['payload']})
            save_json(STATE_PATH, state)

        target = state['target_key']
        request_response = support.get(f"/api/v2/requests/{state['request_id']}", params={
            'lookup': 'request_id', 'operation_id': 'notification_v2_send', 'target_key': target})
        require(request_response.status_code == 200,
            f'Notification request read returned HTTP {request_response.status_code}.')
        request_value = request_response.json()
        state['request_read'] = request_value
        require(request_value['request_id'] == state['request_id'], 'Notification request read returned another request.')
        require(request_value['state'] != 'unknown', 'Unknown notification outcome must not be retried.')
        if request_value['state'] != 'committed':
            require(request_value['may_retry'] is True, 'Notification request state is not safe for submission.')
        if request_value['state'] == 'pending' and not state['send_submitted']:
            send_response = support.post(f'/api/v2{target}', json=state['send_body'], headers={
                'X-Write-Intent': state['intent_id'], 'Idempotency-Key': state['request_id'],
                'X-Intent-Generation': state['generation']})
            require(send_response.status_code == 201,
                f'Notification send returned HTTP {send_response.status_code}: {send_response.text}')
            state['send_response'] = send_response.json()
            state['send_submitted'] = True
            state['send_record'] = send_response.json()
            save_json(STATE_PATH, state)
            request_response = support.get(f"/api/v2/requests/{state['request_id']}", params={
                'lookup': 'request_id', 'operation_id': 'notification_v2_send', 'target_key': target})
            require(request_response.status_code == 200 and request_response.json()['state'] == 'committed',
                'Notification WriteIntent did not read back committed.')
            state['request_read'] = request_response.json()
            save_json(STATE_PATH, state)
        if request_value['state'] == 'committed' and not state.get('duplicate_checked', False):
            duplicate_response = support.post(f'/api/v2{target}', json=state['send_body'], headers={
                'X-Write-Intent': state['intent_id'], 'Idempotency-Key': state['request_id'],
                'X-Intent-Generation': state['generation']})
            require(duplicate_response.status_code == 201,
                f'Committed notification replay returned HTTP {duplicate_response.status_code}.')
            replay = duplicate_response.json()
            require(len(replay['attempts']) == 1 and replay['attempts'][0]['attempt_id'] ==
                request_value['receipt']['resource_id'], 'Committed replay created another notification attempt.')
            state['duplicate_checked'] = True
            state['duplicate_response'] = replay
            save_json(STATE_PATH, state)

        record_response = support.get(f'/api/v2/support/notification-records/{recipient_id}')
        require(record_response.status_code == 200, f'Support record reread returned HTTP {record_response.status_code}.')
        state['support_record'] = record_response.json()
        require(state['support_record']['alert_id'] == event_id, 'Support reread changed the alert identity.')
        require(len(state['support_record']['attempts']) <= 1, 'T23 created more than one notification attempt.')
        if state['support_record']['delivery_status'] not in ('accepted', 'delivered'):
            with transaction.atomic():
                event = OutboxEvent.objects.filter(event_type='notification.native_requested',
                    resource_refs__recipient_id=recipient_id).order_by('-created_at').first()
                if event is not None:
                    state['awaiting_main_outbox'] = {'outbox_event_id': str(event.id),
                        'task_id': str(event.task_id), 'event_type': event.event_type}
            report = {'task_id': 'T23', 'mode': 'SIMULATION_ONLY', 'status': 'AWAITING_SINGLE_OUTBOX_EXECUTION',
                'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
                'patient_id': patient_id, 'alert_id': event_id, 'recipient_id': recipient_id,
                'state': state, 'model_calls': 0, 'external_calls': 0}
            save_json(STATE_PATH, state)
            save_json(REPORT_PATH, report)
            print(json.dumps({'status': report['status'], 'awaiting_main_outbox': state.get('awaiting_main_outbox'),
                'saved_and_read': True, 'report_path': str(REPORT_PATH)}, sort_keys=True))
            return

    if not state['delivery_acknowledged']:
        with family_client(fixture['family_username']) as family:
            message_response = family.get(f'/api/v2/notification-records/{recipient_id}/message')
            require(message_response.status_code == 200,
                f'Family message read returned HTTP {message_response.status_code}: {message_response.text}')
            message = message_response.json()
            require(message['recipient_id'] == recipient_id and message['alert_id'] == event_id,
                'Family message identity differs from the authorized recipient.')
            require(message['body']['approved_text'], 'Approved notification text is empty.')
            delivered_response = family.post(f'/api/v2/notification-records/{recipient_id}/delivered',
                json={'delivery_ticket': message['delivery_ticket']})
            require(delivered_response.status_code == 200,
                f'Family delivery acknowledgement returned HTTP {delivered_response.status_code}: {delivered_response.text}')
            state['delivery_acknowledgement'] = delivered_response.json()
            state['delivery_acknowledged'] = True
            save_json(STATE_PATH, state)
            alert_read = family.post(f'/api/alerts/{event_id}/read', json={})
            require(alert_read.status_code == 200, f'Family alert read returned HTTP {alert_read.status_code}.')
            state['family_alert_read'] = alert_read.json()
            final_family = family.get(f'/api/v2/notification-records/{recipient_id}')
            require(final_family.status_code == 200, f'Family notification record reread returned HTTP {final_family.status_code}.')
            state['family_record'] = final_family.json()

    final_record = state['family_record']
    require(final_record['delivery_status'] == 'delivered' and final_record['read_at'] is not None,
        'Family delivery and read states did not persist.')
    require(len(final_record['attempts']) == 1, 'Expected one notification attempt after readback.')
    receipt_statuses = {item['status'] for item in final_record['attempts'][0]['receipts']}
    require(receipt_statuses == {'accepted', 'delivered'}, 'Accepted and delivered receipts did not persist separately.')
    with transaction.atomic():
        attempt = NotificationAttempt.objects.get(id=final_record['attempts'][0]['attempt_id'], recipient_id=recipient_id)
        receipts = list(NotificationReceipt.objects.filter(attempt=attempt).order_by('status').values('id', 'status', 'provider_event_id', 'evidence_sha256'))
        tasks = list(Task.objects.filter(id=attempt.send_permit_id, task_type='notification').values('id', 'status', 'input_sha256'))
        events = list(OutboxEvent.objects.filter(task_id=attempt.send_permit_id,
            event_type='notification.native_requested').values('id', 'event_type'))
        consumed = list(ConsumerReceipt.objects.filter(consumer='native_notifications',
            event__aggregate_id=attempt.send_permit_id).values(
            'consumer', 'event_id', 'effect_resource_id'))
        require(len(receipts) == 2 and len(tasks) == 1 and len(events) == 1,
            'Notification attempt, receipt, Task or Outbox persistence is incomplete.')
        require(consumed, 'Native notification Outbox event has no consumer receipt.')
        state['database_readback'] = {'attempt_id': str(attempt.id), 'receipts': [
            {**row, 'id': str(row['id'])} for row in receipts], 'tasks': [
            {**row, 'id': str(row['id'])} for row in tasks], 'outbox_events': [
            {**row, 'id': str(row['id'])} for row in events], 'consumer_receipts': consumed}
    report = {'task_id': 'T23', 'mode': 'SIMULATION_ONLY', 'status': 'SENT_DELIVERED_AND_READ',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'patient_id': patient_id, 'alert_id': event_id, 'recipient_id': recipient_id,
        'state': state, 'model_calls': 0, 'external_calls': 0}
    save_json(STATE_PATH, state)
    save_json(REPORT_PATH, report)
    print(json.dumps({'status': report['status'], 'receipt_statuses': sorted(receipt_statuses),
        'saved_and_read': True, 'report_path': str(REPORT_PATH)}, sort_keys=True))


if __name__ == '__main__':
    main()
