import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import django
import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'
django.setup()

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from archives.models import PurposeCopy
from configurations.models import ConfigApproval, ConfigValidation
from core.management.commands.seed_simulation import configuration, evidence, require_simulation
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject
from files.purpose import checked_bytes, current_review
from files.purpose_jobs import execute_copy, submit_copy
from files.purpose_processing import process_image, processing_artifact_checksum
from files.purpose_schemas import PurposeCopyInput, PurposeProcessingPlan
from files.storage import digest_bytes
from archives.batches import batch_runtime
from identity.models import PrincipalCredential
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import OutboxEvent, Task


parser = argparse.ArgumentParser(description='处理准确的已授权模拟图片，不消费共享队列')
parser.add_argument('--file-id', required=True, type=uuid.UUID)
parser.add_argument('--fixture', required=True)
args = parser.parse_args()
require_simulation()
fixture = (ROOT / args.fixture).resolve()
assert fixture.is_relative_to(ROOT / 'docs/testing/evidence/QA04') or fixture.is_relative_to(ROOT / '.planning-work/coordination/root')
original = FileObject.objects.get(id=args.file_id, purpose='original', status='available')
assert original.checksum == digest_bytes(fixture.read_bytes())
assert original.media_type == 'image/png'
group = FileGroup.objects.get(id=original.file_group_id, status='available')
assert 'simulation' in group.source_revision.source.source_key.lower()
assert group.source_revision.license == 'user-authorized-local-simulation'
owner = PrincipalCredential.objects.get(username='simulation-family').principal
reviewer = PrincipalCredential.objects.get(username='simulation-support').principal
assert group.owner_id == owner.id and owner.id != reviewer.id
gate = EnvironmentGate.objects.get(environment='simulation', enabled=True)
retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', version=1, status='active')
original_bytes = checked_bytes(original)
source, reference = evidence(key=f'simulation-current-image-{original.id}', payload={
    'mode': 'SIMULATION_ONLY', 'authorization': '2026-10-09用户允许模拟资料实际开发',
    'fixture': str(fixture.relative_to(ROOT)), 'original_file_id': str(original.id),
    'original_sha256': original.checksum, 'processing': 'redact_regions with no regions; unchanged date',
    'review_scope': 'SIMULATION automation on approved synthetic fixture; clinical and visual NOT_RUN'},
    purposes=['configuration_approval', 'configuration_validation', 'external_recognition'], retention=retention)
plan = {'schema_version': 'purpose-processing-2', 'original_file_id': str(original.id),
    'original_checksum': original.checksum, 'original_version': original.object_version,
    'patient_id': str(original.patient_id), 'environment': 'simulation', 'purpose': 'external_recognition',
    'target': settings.ARK_BASE_URL.rstrip('/'), 'mode': 'redact_regions', 'redactions': [],
    'prepared_file_id': None, 'prepared_checksum': None, 'date_strategy': 'unchanged', 'date_transformation': None,
    'identity_coverage': [{'category': category, 'action': 'removed' if category in ('file_name', 'metadata') else 'not_present',
        'evidence_ref': reference} for category in ('body_text', 'header_footer', 'image_identity', 'barcode', 'qrcode', 'file_name', 'metadata')],
    'required_scopes': ['image_text', 'model_input', 'log'], 'clinical_preservation_evidence_ref': reference}
validated_plan = PurposeProcessingPlan.model_validate(plan)
processed, processing_result = process_image(plan=validated_plan, original_bytes=original_bytes,
    prepared_bytes=None, original_media_type=original.media_type, page_number=1,
    limits=batch_runtime().payload['limits'])
artifact = processing_artifact_checksum()
proof_source, proof_ref = evidence(key=f'simulation-current-image-processing-{original.id}', payload={
    'mode': 'SIMULATION_ONLY', 'original_sha256': digest_bytes(original_bytes),
    'output_sha256': digest_bytes(processed), 'output_bytes': len(processed),
    'artifact_sha256': artifact, 'actual_processing': processing_result,
    'clinical': 'NOT_RUN', 'visual': 'NOT_RUN'}, purposes=['configuration_validation'], retention=retention)
with transaction.atomic():
    config = configuration(key=f'simulation-current-image-{original.id}', kind='provider',
        actor=reviewer, source=source, payload=plan)
    ConfigApproval.objects.get_or_create(config=config, approval_role='data_processing', revoked_at=None,
        defaults={'approved_by': reviewer, 'approved_at': timezone.now(), 'evidence_ref': reference,
            'effective_scope': {'patient_id': str(original.patient_id), 'original_file_id': str(original.id),
                'purpose': 'external_recognition', 'target': plan['target'],
                'environment': 'simulation', 'config_checksum': config.checksum}})
    ConfigValidation.objects.get_or_create(config=config, config_checksum=config.checksum, environment=gate,
        artifact_sha256=artifact, dataset_revision_id=group.source_revision_id, revoked_at=None,
        defaults={'evidence_ref': proof_ref, 'evidence_sha256': proof_source.checksum, 'outcome': 'pass',
            'executed_by': reviewer, 'executed_at': timezone.now()})
authorization = DataUseAuthorization.objects.get(patient_id=original.patient_id,
    source_revision_id=group.source_revision_id, purpose='external_recognition', environment='simulation', revoked_at=None)
copies = list(PurposeCopy.objects.filter(original_file_id=original.id,
    processing_evidence__config_id=str(config.id), environment='simulation'))
assert len(copies) <= 1
if copies:
    copy = copies[0]
    task = Task.objects.get(id=copy.processing_evidence['task_id'])
else:
    task = submit_copy(file_id=original.id, actor=owner, auth_epoch=owner.auth_epoch,
        payload=PurposeCopyInput.model_validate({'purpose': 'external_recognition', 'target': plan['target'],
            'authorization_id': str(authorization.id), 'processing_config_id': str(config.id)}))
    execute_copy(OutboxEvent.objects.get(task=task, event_type='purpose_copy.requested'), f'root-image-{task.id}')
    copy = PurposeCopy.objects.get(processing_evidence__task_id=str(task.id))
derived = FileObject.objects.get(id=copy.derived_file_id, status='available')
assert checked_bytes(derived) == processed and derived.checksum == digest_bytes(processed)
output = ROOT / f'.planning-work/coordination/root/image-{original.id}.json'
report = {'mode': 'SIMULATION_ONLY', 'original_file_id': str(original.id), 'copy_id': str(copy.id),
    'task_id': str(task.id), 'derived_file_id': str(derived.id), 'derived_sha256': derived.checksum,
    'config_id': str(config.id), 'configuration_evidence': reference, 'processing_evidence': proof_ref,
    'status': 'pending_review', 'clinical': 'NOT_RUN', 'visual': 'NOT_RUN', 'model_calls': 0}
output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
if copy.status == 'pending_review':
    with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
        bootstrap = client.get('/api/auth/bootstrap')
        assert bootstrap.status_code == 200
        login = client.post('/api/v2/support/session/login', json={'username': 'simulation-support',
            'password': os.environ['SJIA_SIMULATION_PASSWORD']}, headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
        assert login.status_code == 200, login.text
        headers = {'X-CSRFToken': login.json()['csrf_token']}
        request_id = str(uuid.uuid4())
        target = f'/support/purpose-copies/{copy.id}/reviews'
        payload = {'expected_version': str(copy.version), 'checksum': derived.checksum,
            'scope_results': [{'scope': scope, 'result': 'passed' if scope in plan['required_scopes'] else 'not_applicable'}
                for scope in ('image_text', 'confirmed_text', 'model_input', 'log', 'presentation')],
            'clinical_integrity': 'passed', 'date_shift_consistency': 'not_applicable', 'evidence_ref': reference}
        prepared = client.post('/api/v2/write-intents', json={'operation_id': 'purpose_copy_review',
            'target_key': target, 'request_id': request_id, 'schema_version': '2.0.0', 'payload': payload}, headers=headers)
        assert prepared.status_code == 201, prepared.text
        report['review_intent_id'] = prepared.json()['intent_id']
        report['review_request_id'] = request_id
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        reviewed = client.post('/api/v2' + target, json=payload, headers={**headers,
            'X-Write-Intent': prepared.json()['intent_id'], 'X-Intent-Generation': prepared.json()['generation'],
            'Idempotency-Key': request_id})
        assert reviewed.status_code == 201, reviewed.text
        reloaded = client.get(f'/api/v2/support/purpose-copies/{copy.id}')
        assert reloaded.status_code == 200, reloaded.text
        report['review_receipt'] = reviewed.json()
        report['api_copy'] = reloaded.json()
copy.refresh_from_db()
with transaction.atomic():
    review = current_review(copy, original, derived)
    assert review is not None and review.reviewer_id == reviewer.id and copy.status == 'approved'
report.update({'status': 'PASS', 'review_id': str(review.id), 'reviewed_at': review.reviewed_at.isoformat(),
    'saved_bytes_equal': True, 'exact_task_completed': Task.objects.get(id=task.id).status == 'completed'})
assert report['exact_task_completed']
output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
assert json.loads(output.read_text()) == report
print(json.dumps({'status': 'PASS', 'mode': report['mode'], 'copy_id': str(copy.id),
    'review_id': str(review.id), 'evidence': str(output)}, ensure_ascii=False))
