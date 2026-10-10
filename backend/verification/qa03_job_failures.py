import json
import os
import time
import uuid
from pathlib import Path

import django

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.qa'
django.setup()

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from authorization.models import AccessGrant
from files.models import FileGroup, FileObject
from files.service import complete_file_group, create_file_group, declare_upload_part, store_file_object
from files.storage import digest_bytes
from identity.models import Patient, PrincipalCredential
from identity.service import register_account
from recognition.jobs import submit_recognition
from recognition.models import CandidateSet
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import Task, TaskAttempt
from tasks.service import cancel_task, input_digest

if settings.DATABASES['default']['NAME'] != 'sjia_qa03':
    raise RuntimeError('测试仅允许使用独立 sjia_qa03 数据库')
root = Path(__file__).resolve().parents[2] / '.qa-work/QA03'
actor = PrincipalCredential.objects.get(username='acceptance-family').principal
patient = Patient.objects.get(display_id='模拟患儿A')
authorization = DataUseAuthorization.objects.get(patient=patient)
image = (root.parent / 'simulated-report.png').read_bytes()
results = []


def create_task(owner, invalid_model=False, image_data=image):
    with transaction.atomic():
        group = create_file_group(patient=patient, owner=owner, source_revision=authorization.source_revision,
            use_authorization_id=authorization.id)
        part = declare_upload_part(file_group=group, client_upload_id=uuid.uuid4(), ordinal=1,
            declared_checksum=digest_bytes(image_data), declared_size_bytes=len(image_data), declared_media_type='image/png')
        store_file_object(file_group=group, upload_part=part, data=image_data, purpose=FileObject.Purpose.ORIGINAL,
            retention_policy=RetentionPolicy.objects.filter(status='active').first())
        complete_file_group(file_group=group)
        task = submit_recognition(group=group, actor=owner, version=group.version)
        if invalid_model:
            task.input_refs['model'] = f'qa03-unavailable-model-{uuid.uuid4()}'
            task.input_sha256 = input_digest(task.input_refs)
            task.save(update_fields=['input_refs', 'input_sha256'])
    return task, group


def wait_for(task, condition):
    end = time.monotonic() + 180
    while time.monotonic() < end:
        task.refresh_from_db()
        if condition(task):
            return
        time.sleep(0.5)
    raise AssertionError(f'任务等待超时：{task.id}，状态{task.status}')


def wait_finished(task):
    wait_for(task, lambda current: TaskAttempt.objects.filter(task=current, finished_at__isnull=False).exists())
    attempt = TaskAttempt.objects.get(task=task)
    if CandidateSet.objects.filter(task_id=task.id).exists():
        raise AssertionError('失效任务出现了可采用候选')
    return {'task_id': str(task.id), 'task_status': task.status, 'attempt_status': attempt.status, 'error_code': attempt.error_code}


task, _ = create_task(actor)
wait_for(task, lambda current: current.status == 'running')
time.sleep(2)
cancel_task(task.id)
result = wait_finished(task)
assert result['task_status'] == 'cancelled' and result['attempt_status'] == 'cancelled', result
results.append({'id': 'cancel-during-actual-provider', 'status': 'PASS', **result})

task, group = create_task(actor)
wait_for(task, lambda current: current.status == 'running')
time.sleep(2)
with transaction.atomic():
    group = FileGroup.objects.select_for_update().get(id=group.id)
    group.status = FileGroup.Status.SUPERSEDED
    group.save(update_fields=['status'])
result = wait_finished(task)
assert result['task_status'] == 'superseded' and result['attempt_status'] == 'superseded', result
results.append({'id': 'source-revision-during-actual-provider', 'status': 'PASS', **result})

task, _ = create_task(actor, invalid_model=True)
result = wait_finished(task)
assert result['task_status'] == 'failed' and result['error_code'] == 'ARK_HTTP_ERROR', result
results.append({'id': 'actual-provider-rejects-unavailable-model', 'status': 'PASS', **result})

owner = register_account(username=f'qa03-revocation-{uuid.uuid4()}', password='QA03-Revocation-2026')
for action in ('write_record', 'read_file'):
    AccessGrant.objects.create(patient=patient, principal=owner, action=action, field_codes=['report_item'],
        record_scope='all', purpose='family_observation', granted_by=actor, evidence_ref='用户授权独立测试账号', valid_from=timezone.now())
task, _ = create_task(owner)
wait_for(task, lambda current: current.status == 'running')
time.sleep(2)
AccessGrant.objects.filter(principal=owner, action='write_record').update(revoked_at=timezone.now())
result = wait_finished(task)
assert result['task_status'] == 'failed' and result['error_code'], result
results.append({'id': 'permission-revoked-during-actual-provider', 'status': 'PASS', **result})
task, _ = create_task(actor, image_data=(root / 'ocr-inputs/blank.png').read_bytes())
result = wait_finished(task)
assert result['task_status'] == 'failed' and result['error_code'] == 'OCR_NO_ITEMS', result
results.append({'id': 'actual-blank-image-no-candidates', 'status': 'PASS', **result})
(root / 'job-failures.json').write_text(json.dumps({'tested_at': timezone.now().isoformat(), 'database': 'sjia_qa03',
    'provider': 'actual Ark', 'results': results}, ensure_ascii=False, indent=2))
print(json.dumps({'checks': len(results), 'passed': len(results)}))
