import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'

import django

django.setup()

from django.conf import settings
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker
from core.management.commands.seed_simulation import require_simulation
from files.deletion import digest
from identity.models import PrincipalCredential
from retention.external_deletion import ExternalDeletionRejected, ExternalDeletionRequest, execute_simulated_external_deletion, execute_simulated_external_deletion_task, prepare_simulated_external_deletion, query_simulated_external_deletion, read_simulated_external_deletion_task, register_simulated_external_deletion_task, register_simulated_external_object, submit_simulated_external_deletion


require_simulation()
schema = json.loads((ROOT / 'backend-design/simulation-provider-deletion.schema.json').read_text())
Draft202012Validator.check_schema(schema)
request_schema = Draft202012Validator(schema['$defs']['DeletionRequest'], format_checker=FormatChecker())
receipt_schema = Draft202012Validator(schema['$defs']['DeletionReceipt'], format_checker=FormatChecker())
object_id, request_id = uuid4(), uuid4()
version = f'sim-{uuid4()}'
data = f'SIMULATION external provider copy {object_id}'.encode('utf-8')
registered = register_simulated_external_object(object_id=object_id, object_version=version, data=data)
request = ExternalDeletionRequest(schema_version='provider-delete-request-1', request_id=request_id,
    provider_id='SIMULATION_PROVIDER', object_id=object_id, object_version=version,
    checksum=registered['checksum'], size_bytes=registered['size_bytes'], requested_at=timezone.now())
request_schema.validate(request.model_dump(mode='json'))

pending = prepare_simulated_external_deletion(request)
assert pending == {'state': 'unknown', 'receipt': None}
assert query_simulated_external_deletion(request) == pending

actor = PrincipalCredential.objects.get(username='simulation-support').principal
task = register_simulated_external_deletion_task(actor=actor, request=request)
assert task.status == 'accepted'
assert read_simulated_external_deletion_task(task.id)['provider_state'] == 'unknown'
task_result = execute_simulated_external_deletion_task(task.id, lease_owner=f't40-sim-{task.id}')
result = {'state': task_result['provider_state'], 'receipt': task_result['receipt']}
assert result['state'] == 'deleted'
assert task_result['task_status'] == 'completed'
assert task_result['attempt']['status'] == 'completed'
assert task_result == read_simulated_external_deletion_task(task.id)
receipt_schema.validate(result['receipt'])
assert result == query_simulated_external_deletion(request)
assert result == submit_simulated_external_deletion(request)
assert execute_simulated_external_deletion_task(task.id, lease_owner=f't40-sim-{task.id}') == task_result

target = settings.SIMULATION_ROOT / 'external-provider-store' / 'objects' / str(object_id) / f'{version}.bin'
assert not target.exists()
assert hashlib.sha256(data).hexdigest() == registered['checksum']
assert result['receipt']['evidence_sha256'] == digest(json.dumps({key: value for key, value in result['receipt'].items()
    if key != 'evidence_sha256'}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))

conflict = request.model_copy(update={'object_version': f'{version}-changed'})
try:
    submit_simulated_external_deletion(conflict)
except ExternalDeletionRejected as error:
    assert error.reason_code == 'EXTERNAL_DELETE_REQUEST_MISMATCH'
else:
    raise AssertionError('同一请求键接受了不同对象版本')

not_requested = request.model_copy(update={'request_id': uuid4()})
assert query_simulated_external_deletion(not_requested) == {'state': 'not_requested', 'receipt': None}

report = {'mode': 'SIMULATION', 'status': 'PASS', 'database': settings.DATABASES['default']['NAME'],
    'provider_id': 'SIMULATION_PROVIDER', 'checks': ['准确请求键首次查询为unknown',
        '模拟提供方实际删除独立对象并保存回执', '单项Disposition Task与TaskAttempt实际执行并保存结果',
    'Task、TaskAttempt和Provider回执重新读取一致', '重复执行已完成Task复用同一回执', '回执Schema与摘要通过重新读取核验',
        '重复提交返回同一回执且没有再次删除', '同键异对象版本请求拒绝',
        '无请求记录保持not_requested'],
    'request_id': str(request.request_id), 'object_id': str(request.object_id),
    'object_version': request.object_version, 'object_checksum': request.checksum,
    'task_id': str(task.id), 'task_status': task_result['task_status'],
    'attempt_status': task_result['attempt']['status'], 'receipt': result['receipt'],
    'external_provider': 'NOT_RUN', 'production_protocol': 'NOT_RUN'}
output = ROOT / '.planning-work/main-window/t40-external-deletion.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
output.chmod(0o600)
print(json.dumps(report, ensure_ascii=False, indent=2))
