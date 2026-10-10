import argparse
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from prepare_simulation_voice import execute_purpose, guard
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from core.management.commands.seed_simulation import account, configuration, evidence
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject, FilePage
from files.purpose import checked_bytes
from files.purpose_schemas import ConfirmedTextProcessingPlan
from files.purpose_text import process_confirmed_text, text_processing_artifact_checksum
from files.service import object_store
from files.storage import build_object_key, digest_bytes
from retention.models import RetentionPolicy

ROOT = Path(__file__).resolve().parents[1]


@transaction.atomic
def prepare(file_id):
    guard()
    fixture = json.loads((ROOT / '.simulation-work/t55-voice-fixture.json').read_text())
    target = ROOT / f'.planning-work/coordination/root/text-purpose-{file_id}.json'
    if target.exists():
        value = json.loads(target.read_text())
        original = FileObject.objects.get(id=file_id, patient_id=fixture['patient_id'])
        if checked_bytes(original).decode('utf-8') != value['confirmed_text']:
            raise RuntimeError('已保存的新文字用途资料与原件不一致')
        return value
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    owner, reviewer = account(fixture['family_username']), account('simulation-support')
    original = FileObject.objects.select_for_update().get(id=file_id,
        patient_id=fixture['patient_id'], purpose='original', status='available', media_type='text/plain')
    group = FileGroup.objects.get(id=original.file_group_id, patient=original.patient,
        owner=owner, status='available', source_revision_id=fixture['source_revision_id'])
    data = checked_bytes(original)
    text = data.decode('utf-8', errors='strict')
    if not text.startswith('模拟转写记录：') or original.id == UUID(fixture['original_file_id']):
        raise RuntimeError('仅处理指定家庭的新模拟确认文字')
    page = FilePage.objects.filter(file_id=original.id, patient=original.patient).order_by('ordinal').first()
    if page is None:
        raise RuntimeError('新文字原件没有实际来源页面')
    template = ConfigVersion.objects.get(id=fixture['processing_config_id'])
    prepared_id = uuid4()
    key = build_object_key(original.patient_id, prepared_id, 'deidentified')
    object_store.put_immutable(key, data)
    prepared = FileObject.objects.create(id=prepared_id, patient=original.patient,
        file_group_id=group.id, derived_from_id=original.id, object_key=key,
        object_version=digest_bytes(data), checksum=digest_bytes(data), size_bytes=len(data),
        media_type='text/plain', purpose='deidentified', status='available',
        retention_policy=original.retention_policy, created_at=timezone.now())
    plan = {**template.payload, 'original_file_id': str(original.id),
        'original_checksum': original.checksum, 'original_version': original.object_version,
        'file_page_id': str(page.id), 'prepared_file_id': str(prepared.id),
        'prepared_checksum': prepared.checksum}
    output, actual = process_confirmed_text(plan=ConfirmedTextProcessingPlan.model_validate(plan),
        original_bytes=data, prepared_bytes=checked_bytes(prepared), original_media_type=original.media_type,
        limits=gate.runtime_config.payload['limits'])
    if output != data or not actual['full_text_included']:
        raise RuntimeError('新确认文字用途处理必须保留完整原文')
    processing = configuration(key=f'simulation-voice-purpose-{original.id}', kind='provider',
        payload=plan, actor=reviewer, source=group.source_revision)
    ConfigApproval.objects.create(config=processing, approved_by=reviewer, approval_role='data_processing',
        approved_at=timezone.now(), evidence_ref=fixture['evidence_ref'],
        effective_scope={'patient_id': str(original.patient_id), 'original_file_id': str(original.id),
            'purpose': plan['purpose'], 'target': plan['target'], 'environment': 'simulation',
            'config_checksum': processing.checksum})
    retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
    proof_source, proof_ref = evidence(key=f'voice-purpose-validation-{processing.id}',
        payload={'mode': 'SIMULATION_NEW_TEXT', 'actual_processing': actual,
            'saved_bytes_equal': True, 'artifact_sha256': text_processing_artifact_checksum(),
            'config_checksum': processing.checksum, 'original_checksum': original.checksum,
            'clinical_approval': 'NOT_RUN'}, purposes=['configuration_validation'], retention=retention)
    ConfigValidation.objects.create(config=processing, config_checksum=processing.checksum,
        environment=gate, artifact_sha256=text_processing_artifact_checksum(),
        dataset_revision=group.source_revision, evidence_ref=proof_ref,
        evidence_sha256=proof_source.checksum, outcome='pass', executed_by=reviewer, executed_at=timezone.now())
    return {**fixture, 'original_file_id': str(original.id), 'confirmed_text': text,
        'transcript_sha256': digest_bytes(data), 'processing_config_id': str(processing.id),
        'purpose_copy_id': None, 'derived_file_id': None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file-id', required=True, type=UUID)
    args = parser.parse_args()
    value = execute_purpose(prepare(args.file_id))
    target = ROOT / f'.planning-work/coordination/root/text-purpose-{args.file_id}.json'
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    if json.loads(target.read_text()) != value:
        raise RuntimeError('新文字用途副本证据保存重读不一致')
    print(json.dumps({'status': 'PURPOSE_COPY_APPROVED', 'original_file_id': value['original_file_id'],
        'original_checksum': value['transcript_sha256'], 'purpose_copy_id': value['purpose_copy_id'],
        'purpose_copy_version': value['purpose_copy_version'], 'fixture': str(target),
        'evidence_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
        'external_calls': 0, 'audio': 'NOT_RUN', 'clinical_approval': 'NOT_RUN'}))


if __name__ == '__main__':
    main()
