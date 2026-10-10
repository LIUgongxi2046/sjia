import io
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import django
import httpx
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'
django.setup()

from core.management.commands.seed_simulation import evidence, require_simulation
from authorization.models import AccessGrant
from files.models import FileGroup, FileObject
from files.service import complete_file_group, create_file_group, declare_upload_part, store_file_object
from files.storage import digest_bytes
from identity.models import Patient, PrincipalCredential
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


require_simulation()
patient = Patient.objects.get(display_id='SIMULATION模拟患儿A')
owner = PrincipalCredential.objects.get(username='simulation-family').principal
support = PrincipalCredential.objects.get(username='simulation-support').principal
receipt_policy = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', version=1, status='active')
evidence_source, reference = evidence(key='simulation-provider-disposition-input', payload={
    'mode': 'SIMULATION_ONLY', 'authorization': '2026-10-09用户要求模拟开发与真实保存重读',
    'purpose': 'single designated simulated provider copy deletion',
    'retention': 'new independent policy; preserve originals; actual deletion receipt required',
    'clinical': 'NOT_RUN', 'external_provider': 'NOT_RUN'},
    purposes=['configuration_approval', 'configuration_validation'], retention=receipt_policy)
rules = {'retention_days': None, 'disposition': 'retain', 'local_delete': {
    'schema_version': 'local-disposition-2', 'target_types': ['provider_copy'],
    'access_purpose': 'external_recognition', 'receipt_policy_id': str(receipt_policy.id)}}
receipts = []
with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
    bootstrap = client.get('/api/auth/bootstrap')
    assert bootstrap.status_code == 200
    login = client.post('/api/v2/support/session/login', json={'username': 'simulation-support',
        'password': os.environ['SJIA_SIMULATION_PASSWORD']}, headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
    assert login.status_code == 200, login.text
    headers = {'X-CSRFToken': login.json()['csrf_token']}

    def submit(operation, path, payload, expected):
        request_id = str(uuid.uuid4())
        prepared = client.post('/api/write-intents', json={'operation_id': operation, 'target_key': path,
            'request_id': request_id, 'schema_version': '1.0.0', 'payload': payload}, headers=headers)
        assert prepared.status_code == 201, prepared.text
        result = client.post(path, json=payload, headers={**headers, 'Idempotency-Key': request_id,
            'X-Write-Intent': prepared.json()['intent_id'], 'X-Intent-Generation': prepared.json()['generation']})
        assert result.status_code == expected, result.text
        reloaded = client.get('/api/requests/' + prepared.json()['intent_id'])
        assert reloaded.status_code == 200 and reloaded.json()['state'] == 'committed', reloaded.text
        receipts.append({'operation': operation, 'intent_id': prepared.json()['intent_id'],
            'receipt': result.json(), 'reloaded': reloaded.json()})
        return result.json()['resource_id']

    policy = RetentionPolicy.objects.filter(policy_key='simulation-provider-delete', version=1).first()
    if policy is None:
        policy_id = submit('retention_policy_create', '/api/support/retention-policies', {
            'policy_key': 'simulation-provider-delete', 'data_class': 'file', 'schema_version': 'retention-1',
            'rules': rules, 'evidence_ref': reference}, 201)
        policy = RetentionPolicy.objects.get(id=policy_id)
    assert policy.rules == rules and policy.evidence_ref == reference
    if policy.status == 'draft':
        submit('retention_policy_activate', f'/api/support/retention-policies/{policy.id}/activate',
            {'expected_state': 'draft', 'expected_version': str(policy.version),
                'reason': 'SIMULATION明确批准独立提供方副本处置策略'}, 200)
        policy.refresh_from_db()
    assert policy.status == 'active' and policy.approved_by_id is not None
    source = SourceRegistry.objects.filter(source_key='simulation:provider-disposition-original').first()
    if source is None:
        source_id = submit('source_register', '/api/support/sources', {
            'source_key': 'simulation:provider-disposition-original', 'owner': 'SIMULATION',
            'location': '.planning-work/coordination/root/provider-disposition.png', 'purpose': 'simulation',
            'sensitivity': 'simulation', 'update_frequency': 'manual', 'evidence_ref': reference}, 201)
        source = SourceRegistry.objects.get(id=source_id)
    image = Image.new('RGB', (1000, 500), 'white')
    font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 28)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(('SIMULATION provider disposal document',
            'SIMULATION medication A: 10.00 mg weekly', 'SIMULATION date: 2026-10-09',
            'No personal identity; generated for simulation development')):
        draw.text((30, 30 + index * 70), line, font=font, fill='black')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    data = buffer.getvalue()
    fixture = ROOT / '.planning-work/coordination/root/provider-disposition.png'
    if fixture.exists():
        assert fixture.read_bytes() == data
    else:
        fixture.write_bytes(data)
    revision = SourceRevision.objects.filter(source=source, version='1').first()
    if revision is None:
        revision_id = submit('source_revision_register', f'/api/support/sources/{source.id}/revisions', {
            'version': '1', 'checksum': digest_bytes(data), 'license': 'user-authorized-local-simulation',
            'allowed_use': ['family_observation', 'external_recognition'], 'rights_evidence_ref': reference,
            'retention_policy_id': str(policy.id)}, 201)
        revision = SourceRevision.objects.get(id=revision_id)
    assert revision.checksum == digest_bytes(data) and revision.retention_policy_id == policy.id
    if revision.status == 'pending':
        submit('source_revision_approve', f'/api/support/source-revisions/{revision.id}/approve',
            {'expected_state': 'pending', 'reason': 'SIMULATION明确批准准确合成资料来源'}, 200)
        revision.refresh_from_db()
    assert revision.status == 'approved'
    case_grant = AccessGrant.objects.filter(patient=patient, principal=support, action='read_case',
        purpose='external_recognition', record_scope='all', revoked_at=None, valid_until=None,
        evidence_ref=reference).first()
    if case_grant is None:
        response = client.post('/api/support/access-grants', json={'patient_id': str(patient.id),
            'principal_id': str(support.id), 'relation_id': None, 'action': 'read_case', 'field_codes': [],
            'record_scope': 'all', 'assigned_record_id': None, 'purpose': 'external_recognition',
            'evidence_ref': reference, 'valid_from': datetime.now(timezone.utc).isoformat(),
            'valid_until': None}, headers=headers)
        assert response.status_code == 201, response.text
        case_grant = AccessGrant.objects.get(id=response.json()['grant_id'])
    assert case_grant.field_codes == [] and case_grant.granted_by_id == support.id
    authorizations = {}
    for purpose in ('family_observation', 'external_recognition'):
        auth = DataUseAuthorization.objects.filter(patient=patient, source_revision=revision,
            purpose=purpose, environment='simulation', revoked_at=None).first()
        if auth is None:
            response = client.post(f'/api/support/patients/{patient.id}/use-authorizations', json={
                'source_revision_id': str(revision.id), 'purpose': purpose, 'environment': 'simulation',
                'evidence_ref': reference, 'deidentification_ref': reference, 'valid_until': None}, headers=headers)
            assert response.status_code == 201, response.text
            auth = DataUseAuthorization.objects.get(id=response.json()['authorization_id'])
        authorizations[purpose] = auth

group_id = uuid.uuid5(uuid.NAMESPACE_URL, f'simulation-provider-disposition:{revision.id}:{policy.id}:{digest_bytes(data)}')
originals = list(FileObject.objects.filter(file_group_id=group_id, purpose='original'))
assert len(originals) <= 1
if originals:
    original = originals[0]
else:
    group = create_file_group(patient=patient, owner=owner, source_revision=revision,
        use_authorization_id=authorizations['family_observation'].id, group_id=group_id)
    part = declare_upload_part(file_group=group, client_upload_id=uuid.uuid4(), ordinal=1,
        declared_checksum=digest_bytes(data), declared_size_bytes=len(data), declared_media_type='image/png')
    original = store_file_object(file_group=group, upload_part=part, data=data, purpose='original', retention_policy=policy)
    complete_file_group(file_group=group)
original.refresh_from_db()
assert original.checksum == digest_bytes(data) and original.retention_policy_id == policy.id
result = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'policy_id': str(policy.id), 'policy_version': str(policy.version),
    'source_revision_id': str(revision.id), 'original_file_id': str(original.id), 'original_sha256': original.checksum,
    'fixture': str(fixture.relative_to(ROOT)), 'source_receipts': receipts, 'model_calls': 0,
    'case_grant_id': str(case_grant.id),
    'external_provider': 'NOT_RUN', 'clinical': 'NOT_RUN', 'visual': 'NOT_RUN'}
output = ROOT / '.planning-work/coordination/root/provider-disposition-input.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
assert json.loads(output.read_text()) == result
print(json.dumps({'mode': result['mode'], 'status': 'PASS', 'policy_id': str(policy.id),
    'original_file_id': str(original.id), 'evidence': str(output)}, ensure_ascii=False))
