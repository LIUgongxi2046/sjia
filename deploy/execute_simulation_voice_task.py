import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.db import connection
from django.forms.models import model_to_dict
from django.utils import timezone

from core.management.commands.seed_simulation import require_simulation
from recognition.models import VoiceInput
from recognition.voice_input_jobs import execute_voice_input, record_voice_failure
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt


def main():
    parser = argparse.ArgumentParser(description='仅执行准确模拟语音任务，保存真实模型回执。')
    parser.add_argument('--task-id', required=True, type=UUID)
    parser.add_argument('--event-id', required=True, type=UUID)
    parser.add_argument('--input-sha256', required=True)
    args = parser.parse_args()
    require_simulation()
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    fixture = json.loads((ROOT / '.simulation-work/t55-voice-fixture.json').read_text())
    if fixture['mode'] != 'SIMULATION_TEXT_ONLY':
        raise RuntimeError('模拟确认文字资料身份不匹配')
    task = Task.objects.get(id=args.task_id, actor_id=fixture['owner_id'], patient_id=fixture['patient_id'],
        schema_version='voice-input-1', input_sha256=args.input_sha256)
    event = OutboxEvent.objects.get(id=args.event_id, task=task, event_type='voice_input.requested',
        schema_version='voice-input-1')
    row = VoiceInput.objects.get(id=task.input_refs['voice_input_id'], task=task,
        actor_id=fixture['owner_id'], patient_id=fixture['patient_id'], input_mode='SIMULATION_TEXT_ONLY')
    if event.aggregate_id != row.id or task.input_refs['environment'] != 'simulation':
        raise RuntimeError('模拟语音事件范围不一致')
    owner = f'simulation-single-voice-{uuid4()}'
    try:
        execute_voice_input(event, owner)
    finally:
        error = sys.exception()
        if error is not None:
            record_voice_failure(event, owner, getattr(error, 'reason_code', str(error)))
    task.refresh_from_db()
    row.refresh_from_db()
    receipt = ConsumerReceipt.objects.get(consumer='voice_input', event=event)
    attempts = list(TaskAttempt.objects.filter(task=task).order_by('attempt_number'))
    if (task.status not in ('awaiting_confirmation', 'completed')
            or row.status not in ('awaiting_confirmation', 'saved') or row.candidates is None
            or row.raw_response_file_id is None or len(attempts) != 1
            or not attempts[0].provider_request_id or attempts[0].status != 'completed'):
        raise RuntimeError('模拟语音任务未保存完整实际结果')
    report = {'mode': 'SIMULATION_TEXT_ONLY', 'status': 'PASS', 'checked_at': timezone.now().isoformat(),
        'database': 'sjia_simulation', 'task': model_to_dict(task), 'voice_input': model_to_dict(row),
        'event_id': str(event.id), 'consumer_receipt': model_to_dict(receipt),
        'attempts': [model_to_dict(item) for item in attempts], 'queue_consumption': 'EXACT_SINGLE_EVENT',
        'actual_provider_request_count': 1, 'audio_transcription': 'NOT_RUN', 'actual_invoice': 'NOT_RUN',
        'clinical_approval': 'NOT_RUN', 'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    target = ROOT / f'.planning-work/coordination/root/voice-task-{task.id}.json'
    content = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    target.write_text(content)
    if json.loads(target.read_text()) != json.loads(content):
        raise RuntimeError('实际模型报告保存重读不一致')
    print(json.dumps({'status': 'PASS', 'task_id': str(task.id), 'voice_input_id': str(row.id),
        'provider_request_id': attempts[0].provider_request_id, 'report': str(target),
        'report_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
