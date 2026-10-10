import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import connection, transaction
from recognition.models import VoiceInput
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt

with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    task = Task.objects.values().get(id='273b3ae6-b04b-4851-aeb5-dd5e0cacbac7',
        input_sha256='32e174f9eb0d141c0d9f7e6c06588cbdd74a0963a35ef2edd9ad8b23f66baec2')
    report = {'mode': 'SIMULATION_ONLY', 'status': 'READ_ONLY', 'task': task,
        'voice_input': VoiceInput.objects.values().get(task_id=task['id']),
        'event': OutboxEvent.objects.values().get(id='b379a3d2-f9c3-4032-93cb-2038ad446c1d', task_id=task['id']),
        'attempts': list(TaskAttempt.objects.filter(task_id=task['id']).values()),
        'receipts': list(ConsumerReceipt.objects.filter(event_id='b379a3d2-f9c3-4032-93cb-2038ad446c1d').values())}
encoded = json.dumps(report, default=str, ensure_ascii=False, indent=2)
target = ROOT / '.planning-work/coordination/root/correction-voice-task-readback.json'
target.write_text(encoded)
if json.loads(target.read_text()) != json.loads(encoded):
    raise RuntimeError('只读任务报告保存重读不一致')
print(json.dumps({'status': task['status'], 'lease_until': task['lease_until'],
    'deadline_at': task['deadline_at'], 'voice_status': report['voice_input']['status'],
    'attempts': report['attempts'], 'receipts': len(report['receipts'])}, default=str))
