import argparse
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

from django.db import transaction
from django.utils import timezone

from archives.batch_creation import save_received_batch
from archives.grouping import save_document
from archives.models import ArchiveDocumentVersion, ArchiveFactVersion, ArchivePage, ArchiveRelationVersion
from authorization.models import AccessGrant
from case_details.models import ClinicalEpisode
from configurations.models import ConfigVersion
from core.management.commands.seed_simulation import require_simulation
from family_content.models import EnvironmentGate
from files.models import FilePage
from files.purpose import checked_bytes
from files.service import complete_file_group, create_file_group, declare_upload_part, store_file_object
from files.storage import digest_bytes
from identity.models import Patient, Principal
from records.models import RecordField, RecordVersion
from records.service import create_record, read_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRevision
from verification.service import verify_fields


def medical_time(instant):
    local = instant.removesuffix('+08:00')
    return {'precision': 'second', 'raw_text': instant, 'local_date': local[:10], 'local_datetime': local,
        'timezone_name': 'Asia/Shanghai', 'utc_offset_minutes': None, 'instant': None,
        'resolution_status': 'local_only'}


@transaction.atomic
def prepare(target):
    require_simulation()
    target = (ROOT / target).resolve()
    if not target.is_relative_to(ROOT / '.simulation-work'):
        raise RuntimeError('模拟文书资料必须位于专用目录')
    saved = json.loads(target.read_text())
    if saved['mode'] != 'SIMULATION_ONLY':
        raise RuntimeError('模拟资料身份不匹配')
    if saved.get('confirmed_document_fixture'):
        for identity in saved['document_ids']:
            if not ArchiveDocumentVersion.objects.filter(document_id=identity, grouping_status='confirmed').exists():
                raise RuntimeError('模拟文书确认状态保存重读不一致')
        return {'mode': 'SIMULATION_ONLY', 'reused': True, 'document_ids': saved['document_ids']}
    candidate = json.loads((ROOT / '.planning-work/parallel/window1/t29-simulation-fixture.json').read_text())
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    patient = Patient.objects.select_for_update().get(id=saved['patient_id'])
    owner = Principal.objects.get(id=saved['owner_id'])
    actor = Principal.objects.get(id=saved['support_id'])
    source = SourceRevision.objects.get(id=saved['source_revision_id'], status='approved')
    authorization = DataUseAuthorization.objects.get(id=saved['authorization_ids']['clinical_policy'], patient=patient)
    policy = ConfigVersion.objects.get(id=saved['config_ids']['qualification'], status='enabled')
    retention = RetentionPolicy.objects.get(policy_key='simulation-report', status='active')
    file_policy = RetentionPolicy.objects.filter(policy_key='simulation-file', status='active').order_by('-version').first()
    if file_policy is None:
        raise RuntimeError('模拟原件保存策略不可用')
    quota = ConfigVersion.objects.get(config_key='simulation-directory-input-quota', status='draft')
    old = {key: saved[key] for key in ('records', 'file_ids', 'document_ids', 'fact_ids', 'verification_ids',
        'sampling_relation_version_id')}
    records, files, documents, facts, proofs, episode_records = [], [], [], [], [], []
    for ordinal, points in enumerate([[candidate['comparison_points'][0]], candidate['fact_candidates']], 1):
        episode_text = f'SIMULATION ONLY 独立模拟就诊{ordinal}：于{points[0]["medical_time"]}在SIMULATION模拟输入机构住院采样。'
        content = ('SIMULATION ONLY\n' + episode_text + '\n' + '\n'.join(
            f'{item["source_text"]}; sample time {item["medical_time"]}' for item in points)).encode('utf-8')
        group = create_file_group(patient=patient, owner=owner, source_revision=source, use_authorization_id=authorization.id)
        part = declare_upload_part(file_group=group, client_upload_id=uuid4(), ordinal=1,
            declared_checksum=digest_bytes(content), declared_size_bytes=len(content), declared_media_type='text/plain')
        original = store_file_object(file_group=group, upload_part=part, data=content, purpose='original',
            retention_policy=file_policy, limits=gate.runtime_config.payload['limits'])
        complete_file_group(file_group=group)
        if checked_bytes(original) != content:
            raise RuntimeError('模拟新原件保存重读不一致')
        page = FilePage.objects.get(file_id=original.id, patient=patient)
        episode_record = create_record(patient=patient, actor=owner, kind='observation', capture_method='manual',
            origin_type='self_report', retention_policy=retention, use_authorization_id=authorization.id,
            source_revision=source, file_ids=[original.id], fields=[{'field_code': 'observation_note',
                'original_name': 'SIMULATION独立就诊原文', 'value_type': 'text', 'value_state': 'known',
                'raw_text': episode_text, 'medical_time': medical_time(points[0]['medical_time']),
                'source_file_id': str(original.id), 'source_text': episode_text}])
        episode_field = RecordField.objects.get(record_version_id=episode_record['record_version_id'], field_code='observation_note')
        episode = ClinicalEpisode.objects.create(patient_id=patient.id,
            record_version_id=episode_field.record_version_id, source_field_id=episode_field.id,
            episode_type='hospitalization', label=f'SIMULATION独立模拟就诊{ordinal}',
            start_time_id=episode_field.medical_time_id, organization_text='SIMULATION模拟输入机构')
        batch = save_received_batch(actor=owner, patient=patient, request_id=uuid4(), pages=[page], quota=quota)
        batch.status = 'awaiting_confirmation'
        batch.save(update_fields=['status'])
        archive_page = ArchivePage.objects.get(batch_id=batch.id, file_id=original.id, patient_id=patient.id)
        document, document_version = save_document(patient=patient, actor=owner,
            payload={'batch_id': str(batch.id), 'page_ids': [str(archive_page.id)], 'document_type': 'laboratory',
                'organization': 'SIMULATION模拟输入机构', 'documented_time': medical_time(points[0]['medical_time']),
                'episode_ids': [str(episode.id)], 'confirmed_text': content.decode('utf-8')})
        if document_version.grouping_status != 'confirmed':
            raise RuntimeError('现有业务文书确认服务未完成就诊归属')
        fields = []
        for item in points:
            numeric = item['unit'] is not None
            fields.append({'field_code': item['variable_code'], 'concept_code': item.get('concept_code', item['variable_code']),
                'original_name': 'SIMULATION ' + item['variable_code'], 'value_type': 'decimal' if numeric else 'categorical',
                'value_state': 'known', 'raw_numeric': item['value'] if numeric else None,
                'categorical_value': None if numeric else item['value'], 'raw_unit': item['unit'],
                'qualifier': 'eq' if numeric else None, 'method': item.get('method'), 'sample_time_origin': 'item',
                'medical_time': medical_time(item['medical_time']), 'source_file_id': str(original.id),
                'source_text': item['source_text']})
        result = create_record(patient=patient, actor=owner, kind='report', capture_method='manual',
            origin_type='report_transcription', retention_policy=retention, use_authorization_id=authorization.id,
            source_revision=source, file_ids=[original.id], fields=fields,
            report_metadata={'external_ref': 'SIMULATION-T29-CONFIRMED-' + str(original.id), 'report_status': 'final'})
        saved_fields = list(RecordField.objects.filter(record_version_id=result['record_version_id']).order_by('ordinal'))
        saved_facts = []
        for field, item in zip(saved_fields, points, strict=True):
            fact = ArchiveFactVersion.objects.get(record_version_id=result['record_version_id'], field_id=field.id)
            fact.document_version_id, fact.page_id, fact.episode_id = document_version.id, archive_page.id, episode.id
            fact.documented_time_id, fact.context_type = document_version.documented_time_id, item['context_type']
            fact.assertion_state = 'absent' if item['value'] == 'absent' else 'present'
            fact.save(update_fields=['document_version_id', 'page_id', 'episode_id', 'documented_time_id', 'context_type', 'assertion_state'])
            saved_facts.append(str(fact.id))
        proof = verify_fields(patient=patient, record_version_id=result['record_version_id'], verifier=actor,
            policy=policy, purpose='clinical_policy', evidence_ref=saved['evidence_ref'], evidence_source=source,
            field_results=[(field.id, 'consistent') for field in saved_fields])
        reread = read_record(result['record_id'], patient)
        if reread['quality_status'] != 'valid' or reread['record_version_id'] != result['record_version_id']:
            raise RuntimeError('模拟完整输入保存重读未通过')
        records.append(result); files.append(str(original.id)); documents.append(str(document.id))
        facts.append(saved_facts); proofs.append(str(proof.id)); episode_records.append(episode_record)
    baseline = ArchiveFactVersion.objects.get(id=facts[0][0])
    current_field = RecordField.objects.get(record_version_id=records[1]['record_version_id'], field_code='ferritin')
    current = ArchiveFactVersion.objects.get(record_version_id=records[1]['record_version_id'], field_id=current_field.id)
    sampling = ArchiveRelationVersion.objects.create(patient_id=patient.id, relation_id=uuid4(), version=1,
        left_fact_version_id=baseline.id, right_fact_version_id=current.id, relation_type='independent_sampling',
        status='confirmed', evidence_ref=saved['evidence_ref'], reason='SIMULATION原文明确两次独立就诊采样',
        author_id=actor.id, created_at=timezone.now(), verification_id=proofs[1])
    saved.update(confirmed_document_fixture=True, historical_fixture=old, records=records, file_ids=files,
        document_ids=documents, fact_ids=facts, verification_ids=proofs, episode_records=episode_records,
        sampling_relation_version_id=str(sampling.id), known_at=timezone.now().isoformat())
    transaction.on_commit(lambda: target.write_text(json.dumps(saved, ensure_ascii=False, indent=2)))
    return {'mode': 'SIMULATION_ONLY', 'document_ids': documents, 'records': records,
        'verified_fields': sum(len(value) for value in facts), 'sampling_relation_version_id': str(sampling.id),
        'historical_originals_preserved': True, 'clinical_approval': 'NOT_RUN'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture-file', default='.simulation-work/t29-business-fixture.json')
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.fixture_file), ensure_ascii=False, indent=2))
