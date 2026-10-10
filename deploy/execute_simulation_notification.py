import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import connection
from django.forms.models import model_to_dict
from django.utils import timezone

from alerting.models import AlertEvent, AlertRecipient
from core.management.commands.seed_simulation import require_simulation
from notifications.models import NotificationAttempt, NotificationReceipt
from notifications.native import consume_native
from tasks.models import ConsumerReceipt, OutboxEvent, Task


def main():
    parser = argparse.ArgumentParser(description='执行准确模拟通知事件并保存重读结果。')
    parser.add_argument('--event-id', required=True, type=UUID)
    parser.add_argument('--alert-id', required=True, type=UUID)
    parser.add_argument('--fixture-file', default='.simulation-work/t29-business-fixture.json')
    args = parser.parse_args()
    require_simulation()
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    fixture_path = (ROOT / args.fixture_file).resolve()
    if not fixture_path.is_relative_to(ROOT / '.simulation-work'):
        raise RuntimeError('通知执行器输入必须位于专用模拟目录')
    fixture = json.loads(fixture_path.read_text())
    if fixture['mode'] != 'SIMULATION_ONLY':
        raise RuntimeError('模拟输入身份不匹配')
    alert = AlertEvent.objects.get(id=args.alert_id, patient_id=fixture['patient_id'])
    event = OutboxEvent.objects.get(id=args.event_id)
    if event.event_type == 'alert.event_created':
        if event.aggregate_id != alert.id or event.resource_refs['alert_id'] != str(alert.id):
            raise RuntimeError('预警通知事件身份不匹配')
    elif event.event_type == 'notification.native_requested':
        task = Task.objects.get(id=event.task_id, patient_id=alert.patient_id)
        recipient = AlertRecipient.objects.get(id=event.resource_refs['recipient_id'], alert_id=alert.id)
        if task.input_refs['recipient_id'] != str(recipient.id):
            raise RuntimeError('站内通知任务身份不匹配')
    else:
        raise RuntimeError('不支持该准确通知事件')
    receipt = consume_native(event)
    receipt.refresh_from_db()
    if not ConsumerReceipt.objects.filter(id=receipt.id, event=event, consumer='native_notifications').exists():
        raise RuntimeError('通知消费回执没有保存')
    recipients = list(AlertRecipient.objects.filter(alert_id=alert.id).order_by('id'))
    if not recipients or any(row.delivery_status not in ('accepted', 'delivered') for row in recipients):
        raise RuntimeError('通知没有实际受理')
    attempts = list(NotificationAttempt.objects.filter(recipient_id__in=[row.id for row in recipients]).order_by('id'))
    receipts = list(NotificationReceipt.objects.filter(attempt__in=attempts).order_by('id'))
    requested = list(OutboxEvent.objects.filter(event_type='notification.native_requested',
        task_id__in=[row.send_permit_id for row in attempts]).order_by('id'))
    report = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'checked_at': timezone.now().isoformat(),
        'database': 'sjia_simulation', 'alert_id': str(alert.id), 'event_id': str(event.id),
        'consumer_receipt': model_to_dict(receipt), 'recipients': [model_to_dict(row) for row in recipients],
        'attempts': [model_to_dict(row) for row in attempts], 'receipts': [model_to_dict(row) for row in receipts],
        'requested_events': [model_to_dict(row) for row in requested], 'external_calls': 0,
        'queue_consumption': 'EXACT_SINGLE_EVENT', 'clinical_approval': 'NOT_RUN',
        'business_fixture_path': str(fixture_path.relative_to(ROOT)),
        'business_fixture_sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    target = ROOT / f'.planning-work/coordination/root/notification-{event.id}.json'
    content = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    target.write_text(content)
    if json.loads(target.read_text()) != json.loads(content):
        raise RuntimeError('通知报告保存重读不一致')
    print(json.dumps({'status': 'PASS', 'recipient_ids': [str(row.id) for row in recipients],
        'requested_events': [str(row.id) for row in requested], 'report': str(target),
        'report_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
