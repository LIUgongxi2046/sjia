import json
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from archives.batch_creation import save_received_batch
from archives.grouping import save_document
from archives.models import ArchiveDocument, ArchiveFactVersion, ArchivePage, ArchiveRelationVersion
from authorization.models import AccessGrant
from configurations.models import ConfigVersion
from core.management.commands.seed_simulation import require_simulation, configuration, qualify_simulation_configuration
from family_content.models import EnvironmentGate
from files.models import FilePage
from identity.models import Patient, PrincipalCredential
from records.models import RecordField
from records.service import create_record
from relations.models import Relation
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRevision
from verification.service import verify_fields


class Command(BaseCommand):
    help = '保存独立模拟摘要输入、明确语境与准确来源，结果由实际摘要接口生成'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        fixture = json.loads((settings.SIMULATION_ROOT / 't44-fixture.json').read_text())
        patient = Patient.objects.get(id=fixture['patient_id'])
        owner = PrincipalCredential.objects.get(username='simulation-family').principal
        executor = PrincipalCredential.objects.get(username='simulation-support').principal
        source = SourceRevision.objects.get(id=fixture['evidence_source_id'])
        gate = EnvironmentGate.objects.get(environment='simulation')
        retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
        visibility = configuration(key='simulation-delta-visibility', kind='visibility', actor=executor, source=source,
            payload={'schema_version': 'visibility-1', 'environment': 'simulation', 'purpose': 'family_observation',
                'field_codes': ['ferritin', 'episode_context', 'therapy_name', 'therapy_status'], 'clinical_fields': [],
                'evidence_refs': [fixture['evidence_ref']]})
        qualify_simulation_configuration(visibility, actor=executor, gate=gate, source=source,
            reference=fixture['evidence_ref'], retention=retention)
        target = settings.SIMULATION_ROOT / 't46-fixture.json'
        if target.exists():
            saved = json.loads(target.read_text())
            if saved['mode'] != 'SIMULATION' or saved['patient_id'] != str(patient.id):
                raise RuntimeError('模拟摘要输入身份无效')
            for identity in saved['document_ids']:
                ArchiveDocument.objects.get(id=identity, patient_id=patient.id)
            self.stdout.write('SIMULATION：继续使用已经保存的摘要输入及准确来源')
            return
        relation = Relation.objects.get(patient=patient, guardian=owner, status='active')
        for actor, actions in ((owner, ('read_raw', 'read_history', 'write_record', 'correct_record')),
                (executor, ('read_case', 'verify_field'))):
            for action in actions:
                AccessGrant.objects.get_or_create(principal=actor, patient=patient, action=action,
                    field_codes=['therapy_name', 'therapy_status'], revoked_at=None,
                    defaults={'relation_id': relation.id if actor == owner else None, 'record_scope': 'all',
                        'purpose': 'family_observation', 'granted_by': executor, 'evidence_ref': fixture['evidence_ref'],
                        'valid_from': timezone.now()})
        quota = ConfigVersion.objects.get(config_key='simulation-directory-input-quota', status='draft')
        policy = ConfigVersion.objects.get(id=fixture['policy_id'])
        record_policy = RetentionPolicy.objects.get(policy_key='simulation-report', status='active')
        authorization = DataUseAuthorization.objects.get(patient=patient, source_revision=source,
            purpose='family_observation', environment='simulation', revoked_at=None)
        batches, documents, versions, facts, proofs, records = [], [], [], [], [], []
        for index, file_ids in enumerate(([fixture['files'][2]], [fixture['files'][3], fixture['files'][0]])):
            batch = save_received_batch(actor=owner, patient=patient, request_id=uuid4(),
                pages=[FilePage.objects.get(file_id=identity, patient=patient, ordinal=1) for identity in file_ids], quota=quota)
            batch.status = 'awaiting_confirmation'
            batch.save(update_fields=['status'])
            batches.append(str(batch.id))
            for offset, file_id in enumerate(file_ids):
                page = ArchivePage.objects.get(batch_id=batch.id, patient_id=patient.id, file_id=file_id)
                known_time = {'precision': 'date', 'raw_text': f'2026-10-{7 + index:02}',
                    'local_date': f'2026-10-{7 + index:02}', 'local_datetime': None, 'timezone_name': 'Asia/Shanghai',
                    'utc_offset_minutes': None, 'instant': None, 'resolution_status': 'local_only'}
                document, version = save_document(patient=patient, actor=owner, payload={'batch_id': str(batch.id),
                    'page_ids': [str(page.id)], 'document_type': 'unknown' if offset else 'laboratory',
                    'organization': None if offset else 'SIMULATION摘要医院', 'documented_time': None if offset else known_time,
                    'episode_ids': [], 'confirmed_text': 'SIMULATION摘要原文；全部内容为用户授权的模拟输入。'})
                documents.append(str(document.id)); versions.append(str(version.id))
                values = [{'field_code': 'ferritin', 'concept_code': 'ferritin', 'original_name': 'SIMULATION铁蛋白',
                    'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '950' if index else '900',
                    'raw_unit': 'ng/mL', 'qualifier': 'eq', 'method': 'SIMULATION同一检验方法',
                    'sample_time_origin': 'unknown' if offset else 'item', 'medical_time': None if offset else known_time}]
                if not offset:
                    values.extend([{'field_code': 'therapy_name', 'original_name': '治疗原文', 'value_type': 'text',
                        'value_state': 'known', 'raw_text': 'SIMULATION治疗名称原文，剂量尚未提供', 'sample_time_origin': 'therapy', 'medical_time': known_time},
                        {'field_code': 'therapy_status', 'original_name': '治疗状态原文', 'value_type': 'categorical', 'value_state': 'known',
                            'categorical_value': 'stopped' if index else 'ongoing', 'sample_time_origin': 'therapy', 'medical_time': known_time}])
                inputs = [value | {'source_file_id': str(file_id), 'source_text': 'SIMULATION摘要输入原文'} for value in values]
                created = create_record(patient=patient, actor=owner, kind='report', capture_method='manual', origin_type='report_transcription',
                    retention_policy=record_policy, use_authorization_id=authorization.id, source_revision=source,
                    file_ids=[file_id], fields=inputs, report_metadata={'external_ref': f'SIMULATION-delta-{batch.id}', 'report_status': 'final'})
                records.append(created['record_id'])
                saved_fields = list(RecordField.objects.filter(record_version_id=created['record_version_id']).order_by('ordinal'))
                saved_facts = list(ArchiveFactVersion.objects.filter(record_version_id=created['record_version_id']).order_by('field_id'))
                # 仅初始化新建模拟输入，来源与语境直接来自本命令明确提供的资料。
                for fact in saved_facts:
                    fact.document_version_id, fact.page_id, fact.documented_time_id = version.id, page.id, version.documented_time_id
                    fact.context_type, fact.assertion_state = ('unknown', 'unknown') if offset else ('current', 'present')
                    fact.save(update_fields=['document_version_id', 'page_id', 'documented_time_id', 'context_type', 'assertion_state'])
                facts.append([str(fact.id) for fact in saved_facts])
                if not offset:
                    proof = verify_fields(patient=patient, record_version_id=created['record_version_id'], verifier=executor, policy=policy,
                        purpose='family_observation', evidence_ref=fixture['evidence_ref'], evidence_source=source,
                        field_results=[(field.id, 'consistent') for field in saved_fields])
                    proofs.append(str(proof.id))
        first = ArchiveFactVersion.objects.get(id__in=facts[0], field_id__in=RecordField.objects.filter(field_code='ferritin').values('id'))
        second = ArchiveFactVersion.objects.get(id__in=facts[1], field_id__in=RecordField.objects.filter(field_code='ferritin').values('id'))
        sampling = ArchiveRelationVersion.objects.create(patient_id=patient.id, relation_id=uuid4(), version=1,
            left_fact_version_id=first.id, right_fact_version_id=second.id, relation_type='independent_sampling', status='confirmed',
            evidence_ref=fixture['evidence_ref'], reason='SIMULATION明确提供两次独立采样输入', author_id=owner.id,
            created_at=timezone.now(), verification_id=proofs[1])
        target.write_text(json.dumps({'mode': 'SIMULATION', 'patient_id': str(patient.id), 'baseline_batch_id': batches[0],
            'current_batch_id': batches[1], 'document_ids': documents, 'document_version_ids': versions, 'fact_ids': facts,
            'proof_ids': proofs, 'record_ids': records, 'sampling_relation_id': str(sampling.relation_id),
            'sampling_version_id': str(sampling.id), 'clinical_approval': 'NOT_RUN', 'outputs': 'GENERATED_BY_ACTUAL_API'}, ensure_ascii=False, indent=2))
        self.stdout.write('SIMULATION：保存两个批次、三个文书及独立采样、治疗原文、未知时间输入')
