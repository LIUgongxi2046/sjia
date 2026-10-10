import hashlib
import json
import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction
from core.management.commands.seed_simulation import require_simulation
from family_content.models import EnvironmentGate
from recognition.provider_budget import check_provider_budget
from recognition.voice_input_service import VoiceInputRejected
from tasks.models import Task, TaskAttempt

require_simulation()
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
    if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
        raise RuntimeError('费用预留检查仅允许指定模拟数据库')
ids = ['84285aee-3018-4138-b78e-b02bb4240212', 'd08d966b-8660-4987-9dce-a864bc55e389']
before = list(TaskAttempt.objects.filter(task_id__in=ids).order_by('id').values())
with transaction.atomic():
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    runtime = gate.runtime_config
    entry = next(row for row in runtime.payload['providers'] if row['kind'] == 'voice' and row['enabled'])
    policy = next(row for row in runtime.payload['tasks'] if row['task_type'] == 'ocr')
    value = check_provider_budget(runtime=runtime, billing_scope_key=entry['billing_scope_key'],
        currency=entry['currency'], daily_budget=entry['daily_budget'], reserved_cost=entry['reserved_cost'],
        budget_timezone=settings.TIME_ZONE, max_concurrency=policy['max_concurrency'], reject=VoiceInputRejected)
    if value['budget_state'] != 'unknown' or {row['task_id'] for row in value['unknown_reservations']} != set(ids):
        raise RuntimeError('未知调用的预算预留没有完整保留')
    expected = sum((Decimal(Task.objects.get(id=task_id).input_refs['reserved_cost']) for task_id in ids), Decimal('0'))
    if Decimal(value['unknown_reserved_cost']) != expected or any(row['actual_cost'] is not None for row in value['unknown_reservations']):
        raise RuntimeError('预留额度与真实未知费用没有分别保存')
    if Decimal(value['existing_cost_or_reservation']) < expected or value['inflight_count'] < len(ids):
        raise RuntimeError('跨配置版本计费账号未包含未知调用预留及并发')
    assertion = unittest.TestCase()
    parameters = {'runtime': runtime, 'billing_scope_key': entry['billing_scope_key'],
        'currency': entry['currency'], 'daily_budget': entry['daily_budget'],
        'budget_timezone': settings.TIME_ZONE, 'max_concurrency': policy['max_concurrency'],
        'reject': VoiceInputRejected}
    with assertion.assertRaises(VoiceInputRejected) as exhausted:
        check_provider_budget(**parameters, reserved_cost=entry['daily_budget'])
    assertion.assertEqual(exhausted.exception.reason_code, 'PROVIDER_DAILY_BUDGET_EXCEEDED')
    with assertion.assertRaises(VoiceInputRejected) as concurrency:
        check_provider_budget(**parameters, reserved_cost=entry['reserved_cost'],
            additional_tasks=policy['max_concurrency'])
    assertion.assertEqual(concurrency.exception.reason_code, 'PROVIDER_CONCURRENCY_LIMIT')
after = list(TaskAttempt.objects.filter(task_id__in=ids).order_by('id').values())
if before != after:
    raise RuntimeError('预算检查修改了原有未知调用证明')
report = {'status': 'PASS', 'mode': 'SIMULATION_ONLY', 'budget_evidence': value,
    'unknown_attempts_unchanged': True, 'new_model_calls': 0, 'visual': 'NOT_RUN'}
report['over_budget_denied'] = 'PASS'
report['over_concurrency_denied'] = 'PASS'
target = ROOT / '.planning-work/coordination/root/provider-budget-reservations.json'
data = json.dumps(report, ensure_ascii=False, indent=2).encode()
target.write_bytes(data)
if target.read_bytes() != data:
    raise RuntimeError('预算预留检查报告保存重读不一致')
print(json.dumps({**report, 'report_sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
