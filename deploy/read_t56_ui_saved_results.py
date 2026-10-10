import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import connection, transaction
from alerting.models import AlertRecipient
from identity.models import Patient
from notifications.models import NotificationAttempt, NotificationReceipt
from records.models import RecordVersion
from summaries.models import Summary, SummaryItem
from tasks.models import Task

parser = argparse.ArgumentParser(description='只读核验T56模拟页面实际保存的摘要及通知')
parser.add_argument('--ui-report', required=True)
args = parser.parse_args()
source = (ROOT / args.ui_report).resolve()
if not source.is_relative_to(ROOT / '.planning-work/parallel/window2/evidence'):
    raise RuntimeError('页面报告目录不匹配')
ui = json.loads(source.read_text())
if ui['mode'] != 'SIMULATION_ONLY' or ui['status'] != 'PASS':
    raise RuntimeError('页面实际检查尚未通过')
api_summary = ui['summary']['summary']
api_task = ui['summary']['task']
reference = ui['notification_reference']
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        identity = cursor.fetchone()
        if identity != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    patient = Patient.objects.get(id=ui['patient_id'], status='active')
    summary = Summary.objects.get(id=api_summary['summary_id'], patient=patient, validity='valid')
    task = Task.objects.get(id=api_task['task_id'], patient=patient, status='completed')
    if str(summary.task_id) != str(task.id) or str(task.id) != api_summary['task_id']:
        raise RuntimeError('摘要与Task结果引用不相符')
    request = task.request_row
    if request.status != 'committed' or str(request.request_id) != ui['summary']['request']['request_id']:
        raise RuntimeError('摘要保存请求与API读取不相符')
    if summary.data_epoch != patient.data_epoch or summary.verification_epoch != patient.verification_epoch:
        raise RuntimeError('摘要当前来源版本不相符')
    items = list(SummaryItem.objects.filter(summary_id=summary.id, patient_id=patient.id).order_by('ordinal'))
    if len(items) != len(api_summary['items']) or not items:
        raise RuntimeError('摘要API与数据库项目数量不相符')
    stored_items = [(item.ordinal, str(item.field_id), str(item.record_version_id)) for item in items]
    visible_items = [(item['ordinal'], item['field_id'], item['record_version_id']) for item in api_summary['items']]
    if stored_items != visible_items:
        raise RuntimeError('摘要逐项字段、顺序与来源引用不相符')
    versions = {str(item.record_version_id) for item in items}
    if versions != set(ui['summary']['source_version_ids']):
        raise RuntimeError('摘要API与数据库来源版本不相符')
    for item in items:
        RecordVersion.objects.get(id=item.record_version_id, patient=patient)
    recipient = AlertRecipient.objects.get(id=reference['recipient_id'], patient_id=patient.id,
        alert_id=reference['alert_id'], delivery_status='delivered')
    if recipient.read_at != datetime.fromisoformat(reference['family_api_record']['read_at'].replace('Z', '+00:00')):
        raise RuntimeError('本人阅读时间不相符')
    attempts = list(NotificationAttempt.objects.filter(recipient_id=recipient.id))
    if len(attempts) != 1 or attempts[0].status != 'accepted' or str(attempts[0].id) != reference['attempt_id']:
        raise RuntimeError('唯一通知Attempt不相符')
    receipts = list(NotificationReceipt.objects.filter(attempt=attempts[0]).values('id', 'status', 'evidence_sha256'))
    if len(receipts) != 2 or {item['status'] for item in receipts} != {'accepted', 'delivered'}:
        raise RuntimeError('通知受理及送达回执不相符')
    stored_receipts = {(str(item['id']), item['status'], item['evidence_sha256']) for item in receipts}
    visible_receipts = {(item['receipt_id'], item['status'], item['evidence_sha256'])
        for item in reference['family_api_record']['attempts'][0]['receipts']}
    if stored_receipts != visible_receipts:
        raise RuntimeError('准确通知回执与API来源摘要不相符')
    result = {'task_id': 'T56', 'mode': 'SIMULATION_ONLY', 'status': 'PASS',
        'ui_report': str(source.relative_to(ROOT)), 'ui_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
        'database': {'name': identity[0], 'schema': identity[1], 'port': identity[2]},
        'read_only': True, 'saved_and_read': True, 'patient_id': str(patient.id),
        'summary_id': str(summary.id), 'summary_task_id': str(task.id), 'request_id': str(request.request_id),
        'summary_item_count': len(items), 'source_version_ids': sorted(versions),
        'recipient_id': str(recipient.id), 'attempt_id': str(attempts[0].id),
        'receipt_ids': sorted(str(item['id']) for item in receipts), 'read_at': recipient.read_at.isoformat(),
        'visual': 'NOT_RUN', 'actual_device': 'NOT_RUN', 'wechat': 'NOT_RUN', 'clinical': 'NOT_RUN'}
output = ROOT / '.planning-work/coordination/root/t56-ui-database-readback.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
if json.loads(output.read_text()) != result:
    raise RuntimeError('页面数据库核验报告保存重读不一致')
print(json.dumps(result, ensure_ascii=False))
