import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'

import django

django.setup()

from django.conf import settings
from django.db import transaction
from identity.models import Patient, Principal
from retention.external_deletion import (ExternalDeletionRejected, ExternalDeletionRequest,
    read_simulated_external_deletion_task, register_simulated_external_deletion_task)
from retention.models import DispositionItem, DispositionRequest, RetentionPolicy


if settings.DATABASES['default']['NAME'] != 'sjia_simulation':
    raise RuntimeError('T40 业务绑定检查只允许 sjia_simulation')

checks = []
assertions = unittest.TestCase()
now = datetime.now(timezone.utc)
object_id = uuid4()
object_version = f'sim-{uuid4()}'
request = ExternalDeletionRequest(schema_version='provider-delete-request-1', request_id=uuid4(),
    provider_id='SIMULATION_PROVIDER', object_id=object_id, object_version=object_version,
    checksum='a' * 64, size_bytes=0, requested_at=now)

with transaction.atomic():
    actor = Principal.objects.create(account_status='active', created_at=now, updated_at=now)
    patient = Patient.objects.create(display_id=f'T40-SIM-{uuid4()}', status='active', created_at=now)
    policy = RetentionPolicy.objects.create(policy_key=f't40-sim-{uuid4()}', version=1,
        data_class='simulation-provider-copy', rules={'simulation': True}, schema_version='simulation-1',
        status='active')
    target = {'target_type': 'external_copy', 'target_id': str(object_id), 'target_version': object_version}
    disposition = DispositionRequest.objects.create(patient=patient, requested_by=actor, policy=policy,
        evidence_ref='SIMULATION:T40 task binding', scope={'targets': [target]}, status='approved',
        approved_by=actor, approved_at=now, created_at=now)
    item = DispositionItem.objects.create(request=disposition, target_type=target['target_type'],
        target_id=target['target_id'], target_version=target['target_version'], status='pending')
    business_ref = {'disposition_id': disposition.id, 'item_id': item.id, **target}
    task = register_simulated_external_deletion_task(actor=actor, request=request, disposition_ref=business_ref)
    saved = read_simulated_external_deletion_task(task.id)
    assert saved['task_id'] == str(task.id)
    assert saved['provider_state'] == 'not_requested'
    assert saved['disposition_ref'] == {**business_ref,
        'disposition_id': str(disposition.id), 'item_id': str(item.id), 'auth_epoch': None}
    assert task.patient_id == patient.id
    item.refresh_from_db()
    assert item.status == 'pending'
    checks.append('保存并重读的单项模拟任务绑定准确申请、逐项对象及患者身份')

    item.status = 'awaiting_provider'
    item.save(update_fields=['status'])
    disposition.status = 'running'
    disposition.save(update_fields=['status'])
    awaiting_read = read_simulated_external_deletion_task(task.id)
    assert awaiting_read['disposition_ref'] == saved['disposition_ref']
    checks.append('逐项进入等待提供方及申请执行中后仍能重读原始任务绑定')

    wrong_ref = {**business_ref, 'target_version': f'{object_version}-other'}
    with assertions.assertRaises(ExternalDeletionRejected) as raised:
        register_simulated_external_deletion_task(actor=actor, request=request, disposition_ref=wrong_ref)
    assert raised.exception.reason_code == 'EXTERNAL_DELETE_DISPOSITION_TARGET_MISMATCH'
    checks.append('申请对象版本与提供方请求版本不一致时拒绝登记')
    transaction.set_rollback(True)

report = {'mode': 'SIMULATION', 'database': settings.DATABASES['default']['NAME'],
    'status': 'PASS', 'checks': checks, 'provider_request_state': 'not_requested',
    'business_item_after_transition': 'awaiting_provider', 'temporary_database_rows_rolled_back': True,
    'provider_called': False, 'external_provider': 'NOT_RUN'}
output = ROOT / '.planning-work/main-window/t40-task-binding.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
output.chmod(0o600)
print(json.dumps(report, ensure_ascii=False, indent=2))
