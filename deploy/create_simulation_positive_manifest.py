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
from alerting.models import AlertEvaluation, AlertEvent, AlertRecipient
from family_content.models import EnvironmentGate
from identity.models import Patient
from notifications.models import NotificationAttempt, NotificationReceipt
from records.models import Record, RecordVersion
from summaries.models import Summary, SummaryItem
from tasks.models import Task

fixture_path = ROOT / '.simulation-work/t29-first-alert-business-fixture.json'
fixture = json.loads(fixture_path.read_text())
scope = json.loads((ROOT / '.planning-work/parallel/window1/t29-common-scope-first-alert-main-20261009-04.json').read_text())
summary_report = json.loads((ROOT / '.planning-work/parallel/window1/t29-summary-first-alert-main-summary-04-readback.json').read_text())
if fixture['mode'] != 'SIMULATION_ONLY' or scope['patient_id'] != fixture['patient_id']:
    raise RuntimeError('模拟业务资料与共同范围身份不匹配')
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    patient = Patient.objects.get(id=fixture['patient_id'], status='active')
    gate = EnvironmentGate.objects.get(environment='simulation', enabled=True)
    evaluation = AlertEvaluation.objects.get(id='43fd490e-835f-49e7-b93d-0e280634d125',
        patient=patient, status='completed', decision='met', validity='valid')
    Task.objects.get(id=evaluation.task_id, patient=patient, status='completed')
    if (evaluation.known_at != datetime.fromisoformat(scope['known_at'])
            or evaluation.assessment_at != evaluation.known_at
            or evaluation.data_epoch != patient.data_epoch
            or evaluation.verification_epoch != patient.verification_epoch
            or evaluation.publication_epoch != gate.publication_epoch):
        raise RuntimeError('当前评估范围或资格版本不匹配')
    alert = AlertEvent.objects.get(evaluation_id=evaluation.id, patient=patient,
        release_id=gate.active_release_id, validity='valid')
    recipient = AlertRecipient.objects.get(alert_id=alert.id, patient_id=patient.id,
        channel='in_app', delivery_status='delivered', read_at__isnull=False)
    attempt = NotificationAttempt.objects.get(recipient_id=recipient.id, attempt_number=1, status='accepted')
    if NotificationAttempt.objects.filter(recipient_id=recipient.id).count() != 1:
        raise RuntimeError('当前通知发送尝试数量不匹配')
    receipts = list(NotificationReceipt.objects.filter(attempt=attempt).order_by('status'))
    if {row.status for row in receipts} != {'accepted', 'delivered'} or len(receipts) != 2:
        raise RuntimeError('当前受理及送达回执不完整')
    summary_id = summary_report['summary']['summary_id']
    summary = Summary.objects.get(id=summary_id, patient=patient, validity='valid')
    Task.objects.get(id=summary.task_id, patient=patient, status='completed')
    if summary.data_epoch != patient.data_epoch or summary.verification_epoch != patient.verification_epoch:
        raise RuntimeError('当前摘要来源版本已经改变')
    sources = []
    for item in fixture['records']:
        record = Record.objects.get(id=item['record_id'], patient=patient, status='active')
        version = RecordVersion.objects.get(id=item['record_version_id'], record_id=record.id,
            patient=patient, quality_status='valid')
        if record.current_version != version.version or not SummaryItem.objects.filter(
                summary_id=summary.id, record_version_id=version.id, patient_id=patient.id).exists():
            raise RuntimeError('当前报告来源或摘要引用不匹配')
        sources.append({'record_id': str(record.id), 'record_version_id': str(version.id), 'is_current': True})
    manifest = {'schema_version': 't60-positive-demo-manifest-1', 'mode': 'SIMULATION_ONLY',
        'patient_id': str(patient.id), 'relation_id': str(recipient.relation_id),
        'task_id': str(evaluation.task_id), 'evaluation_id': str(evaluation.id),
        'alert_id': str(alert.id), 'recipient_id': str(recipient.id),
        'receipt_ids': [str(row.id) for row in receipts], 'summary_id': str(summary.id),
        'source_versions': sources, 'fixture_sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        'clinical': 'NOT_RUN', 'visual': 'NOT_RUN', 'official_rules': 'NOT_PROVIDED'}
target = ROOT / '.planning-work/parallel/window1/t60-first-alert-positive-manifest.json'
target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
if json.loads(target.read_text()) != manifest:
    raise RuntimeError('实际正向对象清单保存重读不一致')
print(json.dumps({'status': 'SAVED_AND_READ', 'manifest': str(target.relative_to(ROOT)),
    'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'summary_id': manifest['summary_id']}))
