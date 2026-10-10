import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

import django
import httpx
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'
django.setup()

from archives.models import PurposeCopy, PurposeCopyReview
from configurations.models import ConfigApproval, ConfigValidation
from core.management.commands.seed_simulation import configuration, require_simulation
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject
from files.purpose import checked_bytes
from files.purpose_jobs import execute_copy
from files.purpose_processing import processing_artifact_checksum
from files.storage import digest_bytes
from identity.models import Patient, PrincipalCredential
from retention.jobs import execute_simulated_provider_disposition
from retention.models import DispositionItem, DispositionRequest, RetentionPolicy, SimulatedProviderCopy
from sources.models import DataUseAuthorization
from tasks.models import OutboxEvent, Task


require_simulation()
if settings.DATABASES['default']['NAME'] != 'sjia_simulation':
    raise RuntimeError('T40正向检查仅允许sjia_simulation')
if settings.SJIA_ENVIRONMENT != 'simulation' or settings.FILE_OBJECT_ROOT != settings.SIMULATION_ROOT / 'object-store':
    raise RuntimeError('T40正向检查的模拟环境身份不符')

output = ROOT / '.planning-work/parallel/window1/t40-positive-provider-disposition.json'
checks = []
patient = Patient.objects.get(display_id='SIMULATION模拟患儿A', status='active')
family_principal = PrincipalCredential.objects.get(username='simulation-family').principal
creator_principal = PrincipalCredential.objects.get(username='simulation-support').principal
reviewer_principal = PrincipalCredential.objects.get(username='simulation-reviewer').principal
policy = RetentionPolicy.objects.get(policy_key='simulation-file', version=3, status='active')
gate = EnvironmentGate.objects.get(environment='simulation', enabled=True)
if gate.runtime_config_id is None or str(policy.id) not in gate.runtime_config.payload['retention_policy_ids']:
    raise RuntimeError('当前模拟运行配置没有引用T40策略')
external_auths = list(DataUseAuthorization.objects.filter(patient=patient, purpose='external_recognition',
    environment='simulation', revoked_at__isnull=True).select_related('source_revision').order_by('-approved_at'))
family_auths = list(DataUseAuthorization.objects.filter(patient=patient, purpose='family_observation',
    environment='simulation', revoked_at__isnull=True).select_related('source_revision').order_by('-approved_at'))
authorization_pair = next(((family, external) for family in family_auths for external in external_auths
    if family.source_revision_id == external.source_revision_id), None)
if authorization_pair is None:
    raise RuntimeError('模拟家庭上传或外部识别用途授权不可用')
family_auth, external_auth = authorization_pair


def check(name, condition):
    if not condition:
        raise RuntimeError(name)
    checks.append(name)


def session(username, support=False):
    client = httpx.Client(base_url='http://127.0.0.1:8001', timeout=90, trust_env=False)
    bootstrap = client.get('/api/auth/bootstrap')
    if bootstrap.status_code != 200:
        raise RuntimeError(f'API Bootstrap失败：{bootstrap.status_code}')
    client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
    login_path = '/api/v2/support/session/login' if support else '/api/auth/login'
    login = client.post(login_path, json={'username': username, 'password': os.environ['SJIA_SIMULATION_PASSWORD']})
    if login.status_code != 200:
        raise RuntimeError(f'模拟会话登录失败：{login.status_code} {login.text[:400]}')
    client.headers['X-CSRFToken'] = login.json()['csrf_token']
    return client


def api(client, method, path, body=None, expected=200, headers=None, content=None):
    response = client.request(method, path, json=body, headers=headers, content=content)
    if response.status_code != expected:
        raise RuntimeError(f'{method} {path}返回{response.status_code}：{response.text[:700]}')
    if 'json' in response.headers.get('content-type', ''):
        return response.json()
    return response


def write(client, operation, target, payload, expected=200):
    request_id = str(uuid4())
    intent = api(client, 'POST', '/api/v2/write-intents', {'operation_id': operation,
        'target_key': target, 'request_id': request_id, 'schema_version': '2.0.0', 'payload': payload}, 201)
    headers = {'X-Write-Intent': intent['intent_id'], 'Idempotency-Key': request_id,
        'X-Intent-Generation': intent['generation']}
    receipt = api(client, 'POST', '/api/v2' + target, payload, expected, headers)
    persisted = api(client, 'GET', '/api/v2/requests/' + intent['intent_id'])
    if persisted['state'] != 'committed' or persisted['receipt'] != receipt:
        raise RuntimeError(f'{operation}请求未能准确重读')
    return receipt


family = session('simulation-family')
creator = session('simulation-support', support=True)
reviewer = session('simulation-reviewer', support=True)
upload_options = api(family, 'GET', f'/api/v2/patients/{patient.id}/archive-upload-options')
check('家庭上传接口读取当前模拟来源与运行配置', str(family_auth.id) in
    [item['authorization_id'] for item in upload_options['authorizations']]
    and upload_options['runtime_config_id'] == str(gate.runtime_config_id))

image_path = ROOT / '.planning-work/parallel/window1/t40-simulation-source.png'
if not image_path.exists():
    image = Image.new('RGB', (480, 320), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((32, 32), 'SIMULATION SOURCE DOCUMENT', fill='black')
    draw.text((32, 90), 'Synthetic development material; no clinical use.', fill='black')
    image.save(image_path, format='PNG')
raw = image_path.read_bytes()
group = api(family, 'POST', f'/api/patients/{patient.id}/uploads', {
    'authorization_id': str(family_auth.id), 'client_group_id': str(uuid4())}, 201)
upload_id = uuid4()
file_response = api(family, 'PUT', f'/api/uploads/{group["group_id"]}/files/{upload_id}?checksum={digest_bytes(raw)}'
    f'&size={len(raw)}&ordinal=1&media_type=image/png', expected=201, content=raw)
file_id = file_response['file_id']
api(family, 'POST', f'/api/uploads/{group["group_id"]}/complete', {}, 200)
original = FileObject.objects.get(id=file_id, patient=patient, status='available')
check('家庭API上传的原件绑定新模拟策略且字节回读一致', original.retention_policy_id == policy.id
    and checked_bytes(original) == raw)

with transaction.atomic():
    source = FileGroup.objects.select_related('source_revision').get(id=original.file_group_id).source_revision
    reference = f'urn:sjia:source-revision:{source.id}'
    plan = {'schema_version': 'purpose-processing-2', 'original_file_id': str(original.id),
        'original_checksum': original.checksum, 'original_version': original.object_version,
        'patient_id': str(patient.id), 'environment': 'simulation', 'purpose': 'external_recognition',
        'target': settings.ARK_BASE_URL.rstrip('/'), 'mode': 'redact_regions', 'redactions': [],
        'prepared_file_id': None, 'prepared_checksum': None, 'date_strategy': 'unchanged',
        'date_transformation': None, 'identity_coverage': [
            {'category': category, 'action': 'removed' if category in ('file_name', 'metadata') else 'not_present',
                'evidence_ref': reference} for category in ('body_text', 'header_footer', 'image_identity',
                    'barcode', 'qrcode', 'file_name', 'metadata')],
        'required_scopes': ['image_text', 'model_input', 'log'], 'clinical_preservation_evidence_ref': reference}
    config = configuration(key=f't40-positive-purpose-{original.id}', kind='provider', payload=plan,
        actor=creator_principal, source=source)
    ConfigApproval.objects.get_or_create(config=config, approved_by=creator_principal,
        approval_role='data_processing', evidence_ref=reference, defaults={
            'approved_at': timezone.now(), 'effective_scope': {'patient_id': str(patient.id),
                'original_file_id': str(original.id), 'purpose': 'external_recognition', 'target': plan['target'],
                'environment': 'simulation', 'config_checksum': config.checksum}})
    artifact = processing_artifact_checksum()
    ConfigValidation.objects.get_or_create(config=config, config_checksum=config.checksum, environment=gate,
        artifact_sha256=artifact, dataset_revision=source, revoked_at__isnull=True, defaults={
            'evidence_ref': reference, 'evidence_sha256': source.checksum, 'outcome': 'pass',
            'executed_by': creator_principal, 'executed_at': timezone.now()})

copy_payload = {'purpose': 'external_recognition', 'target': plan['target'],
    'authorization_id': str(external_auth.id), 'processing_config_id': str(config.id)}
copy_receipt = write(creator, 'purpose_copy_create', f'/support/files/{original.id}/purpose-copies', copy_payload, 202)
copy_task = Task.objects.get(id=copy_receipt['task_id'], patient=patient)
copy_event = OutboxEvent.objects.get(task=copy_task, event_type='purpose_copy.requested')
execute_copy(copy_event, f't40-positive-copy-{copy_task.id}')
copy_task.refresh_from_db()
copy = PurposeCopy.objects.get(processing_evidence__task_id=str(copy_task.id), status='pending_review')
derived = FileObject.objects.get(id=copy.derived_file_id, status='available')
check('指定副本任务实际处理并保存可重新读取的输出', copy_task.status == 'completed'
    and hashlib.sha256(checked_bytes(derived)).hexdigest() == derived.checksum)

review_payload = {'expected_version': str(copy.version), 'checksum': derived.checksum,
    'scope_results': [{'scope': scope, 'result': 'passed' if scope in copy.processing_evidence['required_scopes']
        else 'not_applicable'} for scope in ('image_text', 'confirmed_text', 'model_input', 'log', 'presentation')],
    'clinical_integrity': 'passed', 'date_shift_consistency': 'not_applicable',
    'evidence_ref': 'SIMULATION独立复核员核对模拟处理结果'}
write(reviewer, 'purpose_copy_review', f'/support/purpose-copies/{copy.id}/reviews', review_payload, 201)
copy.refresh_from_db()
review_id = copy.processing_evidence['latest_review_id']
review_record = PurposeCopyReview.objects.get(id=review_id, copy_id=copy.id)
check('独立模拟复核账号完成当前用途复核', copy.status == 'approved'
    and review_record.reviewer_id == reviewer_principal.id and review_record.reviewer_id != copy.created_by)
provider_payload = {'expected_version': str(copy.version), 'review_id': review_id}
provider_receipt = write(creator, 'simulation_provider_copy_create',
    f'/support/purpose-copies/{copy.id}/simulation-provider-copy', provider_payload, 201)
provider_copy = SimulatedProviderCopy.objects.get(id=provider_receipt['resource_id'], status='available')

options = api(family, 'GET', f'/api/v2/patients/{patient.id}/disposition-options')
choice = next((item for item in options['targets'] if item['target_type'] == 'provider_copy'
    and item['target_id'] == str(provider_copy.object_id)), None)
check('家庭处置接口显示当前批准策略下的准确模拟外发对象', choice is not None
    and choice['policy_id'] == str(policy.id) and choice['target_version'] == provider_copy.object_version)

disposition_payload = {'policy_id': str(policy.id), 'evidence_ref': 'SIMULATION用户授权删除准确模拟外发副本',
    'targets': [{'target_type': 'provider_copy', 'target_id': str(provider_copy.object_id),
        'target_version': provider_copy.object_version}]}
disposition_receipt = write(family, 'disposition_create', f'/patients/{patient.id}/dispositions',
    disposition_payload, 201)
disposition_id = disposition_receipt['resource_id']
support_view = api(creator, 'GET', f'/api/v2/support/dispositions/{disposition_id}')
approval_payload = {'policy_version': support_view['policy_version'],
    'expected_scope_sha256': support_view['scope_sha256'],
    'evidence_ref': 'SIMULATION支持复核准确申请范围、对象版本和保留策略'}
write(creator, 'disposition_approve', f'/support/dispositions/{disposition_id}/approve', approval_payload, 200)
support_view = api(creator, 'GET', f'/api/v2/support/dispositions/{disposition_id}')
item = next(row for row in support_view['items'] if row['target_id'] == str(provider_copy.object_id))
execute_payload = {'policy_version': support_view['policy_version'],
    'expected_scope_sha256': support_view['scope_sha256'], 'expected_target_version': item['target_version']}
task_receipt = write(creator, 'disposition_execute',
    f'/support/dispositions/{disposition_id}/items/{item["item_id"]}/execute', execute_payload, 202)
task = Task.objects.get(id=task_receipt['task_id'], patient=patient)
event = OutboxEvent.objects.get(task=task, event_type='disposition.provider_requested')
execute_simulated_provider_disposition(event, f't40-positive-provider-{task.id}')
task.refresh_from_db()
first_task_read = api(creator, 'GET', f'/api/v2/tasks/{task.id}')
second_task_read = api(creator, 'GET', f'/api/v2/tasks/{task.id}')
family_result = api(family, 'GET', f'/api/v2/dispositions/{disposition_id}')
support_result = api(creator, 'GET', f'/api/v2/support/dispositions/{disposition_id}')
provider_copy.refresh_from_db()
check('单项模拟删除任务、尝试、回执及API结果均已保存', task.status == 'completed'
    and first_task_read == second_task_read and first_task_read['status'] == 'completed'
    and provider_copy.status == 'deleted' and bool(provider_copy.receipt_sha256))
check('家庭和支持接口重读准确逐项删除结果', family_result['status'] == 'completed'
    and support_result['status'] == 'completed'
    and len(family_result['items']) == 1 and family_result['items'][0]['status'] == 'deleted')

report = {'mode': 'SIMULATION', 'database': settings.DATABASES['default']['NAME'],
    'status': 'PASS', 'checks': checks, 'policy_id': str(policy.id), 'runtime_config_id': str(gate.runtime_config_id),
    'patient_id': str(patient.id), 'file_id': str(original.id), 'purpose_copy_id': str(copy.id),
    'provider_copy_id': str(provider_copy.id), 'disposition_id': disposition_id,
    'task_id': str(task.id), 'request_id': str(task.request_row.request_id),
    'object_version': provider_copy.object_version, 'receipt_sha256': provider_copy.receipt_sha256,
    'provider': 'SIMULATION_ONLY', 'external_provider': 'NOT_RUN', 'clinical': 'NOT_RUN',
    'visual': 'NOT_RUN', 'saved_at': timezone.now().isoformat()}
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
output.chmod(0o600)
saved = json.loads(output.read_text())
if saved != report:
    raise RuntimeError('T40正向运行报告保存后重新读取不一致')
print(json.dumps({'status': report['status'], 'checks': len(checks), 'task_id': report['task_id'],
    'disposition_id': disposition_id, 'provider_copy_id': str(provider_copy.id)}, ensure_ascii=False))
