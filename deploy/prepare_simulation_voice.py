import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from archives.models import PurposeCopy
from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from core.management.commands.seed_simulation import account, configuration, evidence, require_simulation
from family_content.models import EnvironmentGate
from files.models import FileObject, FilePage
from files.purpose import checked_bytes, review_copy
from files.purpose_jobs import execute_copy, submit_copy
from files.purpose_schemas import ConfirmedTextProcessingPlan, PurposeCopyInput, PurposeReviewInput
from files.purpose_text import process_confirmed_text, text_processing_artifact_checksum
from files.service import complete_file_group, create_file_group, declare_upload_part, object_store, store_file_object
from files.storage import build_object_key, digest_bytes
from identity.models import Patient
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import OutboxEvent

TARGET = ROOT / '.simulation-work/t55-voice-fixture.json'
SOURCE = ROOT / '.planning-work/parallel/window3/T55-voice-simulation-text-only.json'


def guard():
    require_simulation()
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
        if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
            raise RuntimeError('模拟数据库身份不匹配')


def read_saved(value):
    if value['mode'] != 'SIMULATION_TEXT_ONLY' or value['family_username'] != 'simulation-voice':
        raise RuntimeError('模拟语音输入身份不匹配')
    owner = account(value['family_username'])
    original = FileObject.objects.get(id=value['original_file_id'], patient_id=value['patient_id'])
    if checked_bytes(original).decode('utf-8') != value['confirmed_text']:
        raise RuntimeError('确认文字原件保存重读不一致')
    if str(owner.id) != value['owner_id'] or original.checksum != value['transcript_sha256']:
        raise RuntimeError('模拟语音原件或账号身份不一致')
    return value


@transaction.atomic
def prepare():
    guard()
    if TARGET.exists():
        return read_saved(json.loads(TARGET.read_text()))
    candidate = json.loads(SOURCE.read_text())
    if candidate['evidence_scope'] != 'SIMULATION_TEXT_ONLY' or candidate['source']['audio_source_available']:
        raise RuntimeError('输入不能声明未经运行的音频证据')
    text = candidate['source']['transcript_text']
    data = text.encode('utf-8')
    if digest_bytes(data) != candidate['source']['transcript_sha256']:
        raise RuntimeError('模拟确认文字校验和不一致')
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    owner, reviewer = account('simulation-voice'), account('simulation-support')
    retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
    source, reference = evidence(key='t55-confirmed-voice-text', payload={
        'mode': 'SIMULATION_TEXT_ONLY', 'transcript': text, 'transcript_sha256': digest_bytes(data),
        'audio_transcription': 'NOT_RUN', 'clinical_approval': 'NOT_RUN',
        'identity_inspection': '模拟文字没有姓名、账号、身份号码、条码或二维码；保留全部症状和时间。',
        'pricing': {'input_per_million': '1', 'output_per_million': '2', 'currency': 'CNY',
            'scope': 'SIMULATION_PRICING_ONLY', 'actual_invoice': 'NOT_RUN'}},
        purposes=['family_observation', 'external_recognition', 'configuration_approval',
            'configuration_validation', 'archive_pricing'], retention=retention)
    patient, _ = Patient.objects.get_or_create(display_id='SIMULATION模拟语音患儿',
        defaults={'status': 'active', 'created_at': timezone.now()})
    application = RelationApplication.objects.create(version=1, applicant=owner, patient=patient,
        evidence_ref=reference, status='approved', checked_by=reviewer, checked_at=timezone.now(),
        created_at=timezone.now())
    relation = Relation.objects.create(patient=patient, guardian=owner, application=application,
        status='active', version=1, checked_by=reviewer, checked_at=timezone.now(), valid_from=timezone.now())
    authorizations = {}
    for purpose in ('family_observation', 'external_recognition'):
        authorizations[purpose] = DataUseAuthorization.objects.create(patient=patient, source_revision=source,
            purpose=purpose, environment='simulation', evidence_ref=reference,
            approved_by=reviewer, approved_at=timezone.now())
    for actor, purpose, actions in ((owner, 'family_observation',
            ('read_raw', 'read_history', 'write_record', 'correct_record', 'read_file', 'view_request', 'manage_draft')),
            (owner, 'external_recognition', ('read_file',)),
            (reviewer, 'external_recognition', ('read_file', 'verify_field'))):
        for action in actions:
            AccessGrant.objects.create(principal=actor, patient=patient,
                relation_id=relation.id if actor == owner else None, action=action,
                purpose=purpose, record_scope='own' if action == 'correct_record' else 'all', field_codes=[], granted_by=reviewer,
                evidence_ref=reference, valid_from=timezone.now())
            if actor == owner and purpose == 'family_observation':
                AccessGrant.objects.create(principal=actor, patient=patient, relation_id=relation.id,
                    action=action, purpose=purpose, record_scope='own' if action == 'correct_record' else 'all', field_codes=['temp', 'observation_note'],
                    granted_by=reviewer, evidence_ref=reference, valid_from=timezone.now())
    ServiceRoleGrant.objects.get_or_create(principal=owner, environment=gate, action='manage_data_use',
        evidence_ref=reference, revoked_at=None, defaults={'granted_by': reviewer, 'valid_from': timezone.now()})
    field_config = ConfigVersion.objects.get(kind='content', schema_version='field-config-1',
        status='enabled', payload__kind='observation', payload__input_context='family_record')
    definitions = {item['field_code']: item for item in field_config.payload['fields']}
    if (not {'temp', 'observation_note'} <= definitions.keys()
            or definitions['temp']['value_type'] != 'decimal'
            or definitions['observation_note']['value_type'] != 'text'):
        raise RuntimeError('当前家庭观察配置不支持准确语音采用字段')
    provider = configuration(key='simulation-voice-ark', kind='provider', actor=reviewer, source=source,
        payload={'schema_version': 'provider-1', 'environment': 'simulation', 'provider': 'ark',
            'endpoint': settings.ARK_BASE_URL.rstrip('/'), 'model_version': settings.ARK_MODEL,
            'field_config_id': str(field_config.id), 'pricing_evidence_ref': reference,
            'input_per_million': '1', 'output_per_million': '2', 'currency': 'CNY', 'evidence_refs': [reference]})
    previous = gate.runtime_config
    providers = [dict(item) for item in previous.payload['providers'] if item['kind'] != 'voice']
    ocr = next(item for item in providers if item['kind'] == 'ocr' and item['enabled'])
    ocr.update(billing_scope_key='simulation-ark-account', reserved_cost='1')
    providers.append({**ocr, 'kind': 'voice', 'config_id': str(provider.id),
        'endpoint': settings.ARK_BASE_URL.rstrip('/'), 'processing_basis_ref': reference})
    runtime = configuration(key=previous.config_key, kind='provider', actor=reviewer,
        source=previous.source_revision, payload={**previous.payload, 'providers': providers})
    gate.runtime_config = runtime
    gate.save(update_fields=['runtime_config'])
    for quota in ConfigVersion.objects.filter(kind='provider', schema_version='archive-quota-1', status='enabled'):
        configuration(key=quota.config_key, kind=quota.kind, actor=reviewer, source=quota.source_revision,
            payload={**quota.payload, 'runtime_config_id': str(runtime.id)})
    file_policy = RetentionPolicy.objects.filter(policy_key='simulation-file', status='active').order_by('-version').first()
    if file_policy is None:
        raise RuntimeError('模拟原件保留策略不可用')
    group = create_file_group(patient=patient, owner=owner, source_revision=source,
        use_authorization_id=authorizations['family_observation'].id)
    part = declare_upload_part(file_group=group, client_upload_id=uuid4(), ordinal=1,
        declared_checksum=digest_bytes(data), declared_size_bytes=len(data), declared_media_type='text/plain')
    original = store_file_object(file_group=group, upload_part=part, data=data, purpose='original',
        retention_policy=file_policy, limits=runtime.payload['limits'])
    complete_file_group(file_group=group)
    prepared_id = uuid4()
    key = build_object_key(patient.id, prepared_id, 'deidentified')
    object_store.put_immutable(key, data)
    prepared = FileObject.objects.create(id=prepared_id, patient=patient, file_group_id=group.id,
        derived_from_id=original.id, object_key=key, object_version=digest_bytes(data), checksum=digest_bytes(data),
        size_bytes=len(data), media_type='text/plain', purpose='deidentified', status='available',
        retention_policy=file_policy, created_at=timezone.now())
    page = FilePage.objects.filter(file_id=original.id, patient=patient).order_by('ordinal').first()
    if page is None:
        raise RuntimeError('确认文字缺少来源页面')
    plan = {'schema_version': 'purpose-processing-text-1', 'original_file_id': str(original.id),
        'original_checksum': original.checksum, 'original_version': original.object_version,
        'patient_id': str(patient.id), 'environment': 'simulation', 'purpose': 'external_recognition',
        'target': settings.ARK_BASE_URL.rstrip('/'), 'file_page_id': str(page.id), 'mode': 'prepared_text',
        'redactions': [], 'prepared_file_id': str(prepared.id), 'prepared_checksum': prepared.checksum,
        'date_strategy': 'unchanged', 'date_transformation': None,
        'identity_coverage': [{'category': category,
            'action': 'removed' if category in ('file_name', 'metadata') else 'not_present',
            'evidence_ref': reference} for category in ('body_text', 'header_footer', 'image_identity',
                'barcode', 'qrcode', 'file_name', 'metadata')],
        'required_scopes': ['confirmed_text', 'model_input', 'log'],
        'clinical_preservation_evidence_ref': reference}
    processed, actual = process_confirmed_text(plan=ConfirmedTextProcessingPlan.model_validate(plan),
        original_bytes=checked_bytes(original), prepared_bytes=checked_bytes(prepared),
        original_media_type=original.media_type, limits=runtime.payload['limits'])
    if processed != data or actual['source_page_number'] != 1:
        raise RuntimeError('确认文字用途处理没有保存全部原文')
    processing = configuration(key=f'simulation-voice-purpose-{original.id}', kind='provider',
        actor=reviewer, source=source, payload=plan)
    ConfigApproval.objects.create(config=processing, approved_by=reviewer, approval_role='data_processing',
        approved_at=timezone.now(), evidence_ref=reference, effective_scope={'patient_id': str(patient.id),
            'original_file_id': str(original.id), 'purpose': plan['purpose'], 'target': plan['target'],
            'environment': 'simulation', 'config_checksum': processing.checksum})
    proof_source, proof_ref = evidence(key=f'voice-purpose-validation-{processing.id}',
        payload={'mode': 'SIMULATION_TEXT_ONLY', 'actual_processing': actual, 'saved_bytes_equal': True,
            'artifact_sha256': text_processing_artifact_checksum(), 'config_checksum': processing.checksum},
        purposes=['configuration_validation'], retention=retention)
    ConfigValidation.objects.create(config=processing, config_checksum=processing.checksum, environment=gate,
        artifact_sha256=text_processing_artifact_checksum(), dataset_revision=source,
        evidence_ref=proof_ref, evidence_sha256=proof_source.checksum, outcome='pass',
        executed_by=reviewer, executed_at=timezone.now())
    return read_saved({'mode': 'SIMULATION_TEXT_ONLY', 'family_username': 'simulation-voice',
        'owner_id': str(owner.id), 'patient_id': str(patient.id), 'reviewer_id': str(reviewer.id),
        'source_revision_id': str(source.id), 'evidence_ref': reference,
        'authorization_id': str(authorizations['external_recognition'].id),
        'family_authorization_id': str(authorizations['family_observation'].id),
        'confirmed_text': text, 'transcript_sha256': digest_bytes(data), 'original_file_id': str(original.id),
        'field_config_id': str(field_config.id), 'provider_config_id': str(provider.id),
        'runtime_config_id': str(runtime.id), 'processing_config_id': str(processing.id),
        'purpose_copy_id': None, 'audio_transcription': 'NOT_RUN', 'clinical_approval': 'NOT_RUN',
        'actual_invoice': 'NOT_RUN', 'external_calls': 0})


def execute_purpose(value):
    guard()
    owner, reviewer = account(value['family_username']), account('simulation-support')
    config = ConfigVersion.objects.get(id=value['processing_config_id'])
    with transaction.atomic():
        copies = list(PurposeCopy.objects.filter(original_file_id=value['original_file_id'],
            purpose='external_recognition', environment='simulation'))
        if len(copies) > 1:
            raise RuntimeError('模拟文字副本需要唯一选择')
        if not copies:
            task = submit_copy(file_id=value['original_file_id'], actor=owner, auth_epoch=owner.auth_epoch,
                payload=PurposeCopyInput.model_validate({'purpose': 'external_recognition',
                    'target': config.payload['target'], 'authorization_id': value['authorization_id'],
                    'processing_config_id': str(config.id)}))
            event = OutboxEvent.objects.get(task=task, event_type='purpose_copy.requested')
            execute_copy(event, f'simulation-voice-copy-{task.id}')
            copy = PurposeCopy.objects.get(processing_evidence__task_id=str(task.id))
        else:
            copy = copies[0]
        derived = FileObject.objects.get(id=copy.derived_file_id)
        if checked_bytes(derived).decode('utf-8') != value['confirmed_text']:
            raise RuntimeError('实际用途副本保存重读与本人确认文字不一致')
        if copy.status == 'pending_review':
            review_copy(copy_id=copy.id, actor=reviewer, auth_epoch=reviewer.auth_epoch, request_id=uuid4(),
                payload=PurposeReviewInput.model_validate({'expected_version': str(copy.version),
                    'checksum': derived.checksum, 'scope_results': [{'scope': scope,
                        'result': 'passed' if scope in config.payload['required_scopes'] else 'not_applicable'}
                        for scope in ('image_text', 'confirmed_text', 'model_input', 'log', 'presentation')],
                    'clinical_integrity': 'passed', 'date_shift_consistency': 'not_applicable',
                    'evidence_ref': value['evidence_ref']}))
        copy.refresh_from_db()
        if copy.status != 'approved':
            raise RuntimeError('模拟文字用途副本未通过独立批准')
        return {**value, 'purpose_copy_id': str(copy.id), 'purpose_copy_status': copy.status,
            'purpose_copy_version': str(copy.version), 'derived_file_id': str(derived.id)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute-copy', action='store_true')
    args = parser.parse_args()
    value = prepare()
    if args.execute_copy:
        value = execute_purpose(value)
    content = json.dumps(value, ensure_ascii=False, indent=2)
    TARGET.write_text(content)
    if json.loads(TARGET.read_text()) != value:
        raise RuntimeError('模拟语音资料清单保存重读不一致')
    print(json.dumps({'status': 'PREPARED' if not args.execute_copy else 'PURPOSE_COPY_APPROVED',
        'fixture': str(TARGET), 'sha256': hashlib.sha256(TARGET.read_bytes()).hexdigest(),
        'patient_id': value['patient_id'], 'external_calls': 0, 'audio_transcription': 'NOT_RUN'}))


if __name__ == '__main__':
    main()
