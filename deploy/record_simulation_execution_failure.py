import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.forms.models import model_to_dict
from django.utils import timezone

from core.management.commands.seed_simulation import require_simulation
from tasks.models import OutboxEvent, Task, TaskAttempt
from tasks.service import TaskRejected
from tasks.worker import RecognitionWorker

require_simulation()
parser = argparse.ArgumentParser(description='保存实际单项执行超时的准确模拟任务失败状态。')
parser.add_argument('--task-id', required=True)
parser.add_argument('--event-id', required=True)
parser.add_argument('--input-sha256', required=True)
parser.add_argument('--execution-session', required=True)
arguments = parser.parse_args()
task = Task.objects.get(id=arguments.task_id,
    patient_id='405514ce-9335-4705-a750-d811148bda4c',
    input_sha256=arguments.input_sha256)
event = OutboxEvent.objects.get(id=arguments.event_id, task=task,
    event_type='assessment.requested')
attempt = TaskAttempt.objects.get(task=task, lease_generation=task.lease_generation)
if task.status != 'running' or attempt.status != 'running' or not task.lease_owner:
    raise RuntimeError('准确执行失败对象已经改变')
error = TaskRejected('TASK_EXECUTION_DEADLINE_EXCEEDED')
RecognitionWorker().on_failure(error, task.lease_owner, (), {'event_id': str(event.id)}, None)
task.refresh_from_db()
attempt.refresh_from_db()
if task.status != 'failed' or attempt.status != 'failed' or attempt.error_code != error.reason_code:
    raise RuntimeError('成熟任务框架失败状态保存重读不一致')
report = {'mode': 'SIMULATION_ONLY', 'checked_at': timezone.now().isoformat(),
    'failure_source': f'实际执行会话{arguments.execution_session}退出1及lease heartbeat异常',
    'task': model_to_dict(task), 'attempt': model_to_dict(attempt), 'clinical_approval': 'NOT_RUN'}
target = ROOT / f'.planning-work/coordination/root/task-{task.id}-failure.json'
content = json.dumps(report, ensure_ascii=False, indent=2, default=str)
target.write_text(content)
if json.loads(target.read_text()) != json.loads(content):
    raise RuntimeError('任务失败记录保存重读不一致')
print(json.dumps({'task_id': str(task.id), 'status': task.status, 'error_code': attempt.error_code,
    'report': str(target)}))
