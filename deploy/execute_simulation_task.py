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

from alerting.models import AlertEvaluation
from alerting.service import execute_dynamic_evaluation
from alerting.triggers import emit_static_completion
from assessment.models import Assessment, AssessmentOutput
from assessment.service import execute_static_assessment
from core.management.commands.seed_simulation import require_simulation
from tasks.models import ConsumerReceipt, OutboxEvent, Task, TaskAttempt


def main():
    parser = argparse.ArgumentParser(description='执行准确模拟评估任务，保存并重新读取真实结果。')
    parser.add_argument('--task-id', required=True, type=UUID)
    parser.add_argument('--event-id', required=True, type=UUID)
    parser.add_argument('--input-sha256', required=True)
    parser.add_argument('--fixture-file', default='.simulation-work/t29-business-fixture.json')
    arguments = parser.parse_args()
    require_simulation()
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')
    fixture_path = (ROOT / arguments.fixture_file).resolve()
    if not fixture_path.is_relative_to(ROOT / '.simulation-work'):
        raise RuntimeError('单项执行器输入必须位于专用模拟目录')
    fixture = json.loads(fixture_path.read_text())
    if fixture['mode'] != 'SIMULATION_ONLY':
        raise RuntimeError('模拟任务输入身份不匹配')
    task = Task.objects.get(id=arguments.task_id, patient_id=fixture['patient_id'],
        actor_id=fixture['support_id'], input_sha256=arguments.input_sha256)
    event = OutboxEvent.objects.get(id=arguments.event_id, task=task, aggregate_id=task.id,
        schema_version=task.schema_version)
    context = task.input_refs['authorization_context']
    if context['environment'] != 'simulation' or context['purpose'] != 'clinical_policy':
        raise RuntimeError('任务环境或用途不匹配')
    if task.task_type == 'assessment' and event.event_type == 'assessment.requested':
        consumer = 'assessment'
        execute_static_assessment(event, f'simulation-single-{uuid4()}')
        emit_static_completion(task.id)
    elif task.task_type == 'alert_evaluation' and event.event_type == 'alert_evaluation.requested':
        consumer = 'alert_evaluation'
        execute_dynamic_evaluation(event, f'simulation-single-{uuid4()}')
    else:
        raise RuntimeError('单项执行器不支持该准确事件类型')
    task.refresh_from_db()
    receipt = ConsumerReceipt.objects.get(consumer=consumer, event=event)
    attempts = list(TaskAttempt.objects.filter(task=task).order_by('attempt_number'))
    if task.status != 'completed' or not attempts or attempts[-1].status != 'completed':
        raise RuntimeError(f'实际任务没有完成：{task.status}')
    if consumer == 'assessment':
        result = Assessment.objects.get(task_id=task.id, patient_id=task.patient_id)
        output = model_to_dict(AssessmentOutput.objects.get(assessment=result))
    else:
        result = AlertEvaluation.objects.get(task_id=task.id, patient_id=task.patient_id)
        output = attempts[-1].usage_evidence
    if receipt.effect_resource_id != result.id:
        raise RuntimeError('消费回执与实际结果身份不匹配')
    report = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'checked_at': timezone.now().isoformat(),
        'database': 'sjia_simulation', 'patient_id': str(task.patient_id), 'task_id': str(task.id),
        'event_id': str(event.id), 'task_status': task.status, 'input_sha256': task.input_sha256,
        'consumer_receipt': model_to_dict(receipt), 'attempts': [model_to_dict(row) for row in attempts],
        'result': model_to_dict(result), 'output': output, 'clinical_approval': 'NOT_RUN',
        'external_model_calls': 0, 'queue_consumption': 'EXACT_SINGLE_EVENT',
        'business_fixture_path': str(fixture_path.relative_to(ROOT)),
        'business_fixture_sha256': hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    target = ROOT / f'.planning-work/coordination/root/task-{task.id}.json'
    content = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    target.write_text(content)
    if json.loads(target.read_text()) != json.loads(content):
        raise RuntimeError('实际结果报告保存重读不一致')
    print(json.dumps({'status': 'PASS', 'task_id': str(task.id), 'result_id': str(result.id),
        'report': str(target), 'report_sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
