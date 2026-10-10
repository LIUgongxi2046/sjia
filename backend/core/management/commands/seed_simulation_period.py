import json
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from archives.models import ArchiveDocumentVersion, ArchiveFactVersion, ArchivePage, ArchiveRelationVersion
from configurations.models import ConfigVersion
from core.management.commands.seed_simulation import configuration, qualify_simulation_configuration, require_simulation
from family_content.models import EnvironmentGate
from identity.models import Patient, PrincipalCredential
from medical_time.models import MedicalTime
from records.models import Record, RecordField, RecordVersion
from records.correction import correct_record
from records.service import create_record
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRevision
from verification.service import verify_fields


class Command(BaseCommand):
    help = '保存独立模拟采样时刻和频率依据，观察范围及间隔结果由实际接口生成'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        fixture = json.loads((settings.SIMULATION_ROOT / 't44-fixture.json').read_text())
        delta = json.loads((settings.SIMULATION_ROOT / 't46-fixture.json').read_text())
        patient = Patient.objects.get(id=fixture['patient_id'])
        owner = PrincipalCredential.objects.get(username='simulation-family').principal
        executor = PrincipalCredential.objects.get(username='simulation-support').principal
        source = SourceRevision.objects.get(id=fixture['evidence_source_id'])
        gate = EnvironmentGate.objects.get(environment='simulation')
        retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
        frequency = configuration(key='simulation-sampling-frequency', kind='window', actor=executor, source=source,
            payload={'schema_version': 'sampling-frequency-1', 'purpose': 'family_observation',
                'concept_codes': ['ferritin'], 'interval_hours': '24', 'evidence_refs': [fixture['evidence_ref']]})
        qualify_simulation_configuration(frequency, actor=executor, gate=gate, source=source,
            reference=fixture['evidence_ref'], retention=retention)
        target = settings.SIMULATION_ROOT / 't47-fixture.json'
        if target.exists():
            saved = json.loads(target.read_text())
            if saved['mode'] != 'SIMULATION' or saved['patient_id'] != str(patient.id):
                raise RuntimeError('模拟采样输入身份无效')
            for identity in saved['fact_ids']:
                ArchiveFactVersion.objects.get(id=identity, patient_id=patient.id)
            self.confirm_saved_times(saved, patient, owner, executor, fixture, source)
            target.write_text(json.dumps(saved, ensure_ascii=False, indent=2))
            self.stdout.write('SIMULATION：继续使用已保存的准确采样时刻输入')
            return
        document = ArchiveDocumentVersion.objects.get(id=delta['document_version_ids'][1], patient_id=patient.id)
        page = ArchivePage.objects.get(batch_id=document.batch_id, file_id=fixture['files'][3], patient_id=patient.id)
        policy = ConfigVersion.objects.get(id=fixture['policy_id'])
        record_policy = RetentionPolicy.objects.get(policy_key='simulation-report', status='active')
        authorization = DataUseAuthorization.objects.get(patient=patient, source_revision=source,
            purpose='family_observation', environment='simulation', revoked_at=None)
        times = ['2026-10-06T00:00:00', '2026-10-06T03:00:00', '2026-10-06T06:00:00',
            '2026-10-06T09:00:00', '2026-10-01T00:00:00', '2026-10-04T00:00:00']
        facts, proofs, records = [], [], []
        for index, local in enumerate(times):
            medical_time = {'precision': 'minute' if index == 3 else 'second', 'raw_text': local,
                'local_date': local[:10], 'local_datetime': local, 'timezone_name': 'Asia/Shanghai',
                'utc_offset_minutes': None, 'instant': None, 'resolution_status': 'local_only'}
            created = create_record(patient=patient, actor=owner, kind='report', capture_method='manual',
                origin_type='report_transcription', retention_policy=record_policy, use_authorization_id=authorization.id,
                source_revision=source, file_ids=[str(page.file_id)], fields=[{'field_code': 'ferritin',
                    'concept_code': 'ferritin', 'original_name': 'SIMULATION同日独立采样铁蛋白', 'value_type': 'decimal',
                    'value_state': 'known', 'raw_numeric': str(1000 + index), 'raw_unit': 'ng/mL', 'qualifier': 'eq',
                    'method': 'SIMULATION同一检验方法', 'sample_time_origin': 'item', 'medical_time': medical_time,
                    'source_file_id': str(page.file_id), 'source_text': 'SIMULATION用户授权的采样时刻及原值输入'}],
                report_metadata={'external_ref': f'SIMULATION-period-{index}-{uuid4()}', 'report_status': 'final'})
            fact = ArchiveFactVersion.objects.get(record_version_id=created['record_version_id'], patient_id=patient.id)
            fact.document_version_id, fact.page_id, fact.documented_time_id = document.id, page.id, document.documented_time_id
            fact.context_type, fact.assertion_state = 'current', 'present'
            fact.save(update_fields=['document_version_id', 'page_id', 'documented_time_id', 'context_type', 'assertion_state'])
            field = RecordField.objects.get(id=fact.field_id, patient=patient)
            proof = verify_fields(patient=patient, record_version_id=created['record_version_id'], verifier=executor,
                policy=policy, purpose='family_observation', evidence_ref=fixture['evidence_ref'], evidence_source=source,
                field_results=[(field.id, 'consistent')])
            facts.append(str(fact.id)); proofs.append(str(proof.id)); records.append(created['record_id'])
        relations = []
        for left, right in ((0, 1), (1, 2), (2, 3), (4, 5)):
            relation = ArchiveRelationVersion.objects.create(patient_id=patient.id, relation_id=uuid4(), version=1,
                left_fact_version_id=facts[left], right_fact_version_id=facts[right], relation_type='independent_sampling',
                status='confirmed', evidence_ref=fixture['evidence_ref'], reason='SIMULATION明确提供独立采样输入',
                author_id=owner.id, created_at=timezone.now(), verification_id=proofs[right])
            relations.append(str(relation.id))
        target.write_text(json.dumps({'mode': 'SIMULATION', 'patient_id': str(patient.id), 'fact_ids': facts,
            'proof_ids': proofs, 'record_ids': records, 'relation_version_ids': relations,
            'frequency_config_id': str(frequency.id), 'clinical_approval': 'NOT_RUN',
            'outputs': 'GENERATED_BY_ACTUAL_API'}, ensure_ascii=False, indent=2))
        self.stdout.write('SIMULATION：保存六次采样输入，准确时刻、分钟精度与稀疏间隔分别保留')

    def confirm_saved_times(self, saved, patient, owner, executor, fixture, source):
        changed = False
        for index, identity in enumerate(saved['fact_ids']):
            fact = ArchiveFactVersion.objects.get(id=identity, patient_id=patient.id)
            field = RecordField.objects.get(id=fact.field_id, patient=patient)
            medical = MedicalTime.objects.get(id=field.medical_time_id, patient=patient)
            if medical.resolution_status != 'resolved' or medical.utc_offset_minutes is not None:
                continue
            previous = RecordVersion.objects.get(id=field.record_version_id, patient=patient)
            value = {name: getattr(field, name) for name in ('field_code', 'concept_code', 'original_name', 'value_type',
                'value_state', 'raw_text', 'raw_unit', 'qualifier', 'method', 'sample_time_origin')}
            value['raw_numeric'] = str(field.raw_numeric)
            value['medical_time'] = {'precision': medical.precision, 'raw_text': medical.raw_text,
                'local_date': medical.local_date.isoformat(), 'local_datetime': medical.local_datetime.isoformat(),
                'timezone_name': medical.timezone_name, 'utc_offset_minutes': None, 'instant': None, 'resolution_status': 'local_only'}
            record = Record.objects.get(id=previous.record_id, patient=patient)
            created = correct_record(record=record, expected_version=record.current_version, actor=owner,
                reason='SIMULATION采样时刻资料确认', fields=[value], use_authorization_id=previous.use_authorization_id,
                retention_policy=previous.retention_policy, source_revision=source)
            current = ArchiveFactVersion.objects.get(record_version_id=created['record_version_id'], patient_id=patient.id)
            current_field = RecordField.objects.get(id=current.field_id, patient=patient)
            proof = verify_fields(patient=patient, record_version_id=created['record_version_id'], verifier=executor,
                policy=ConfigVersion.objects.get(id=fixture['policy_id']), purpose='family_observation',
                evidence_ref=fixture['evidence_ref'], evidence_source=source, field_results=[(current_field.id, 'consistent')])
            saved['fact_ids'][index], saved['proof_ids'][index] = str(current.id), str(proof.id)
            changed = True
        if changed:
            saved['relation_version_ids'] = []
            for left, right in ((0, 1), (1, 2), (2, 3), (4, 5)):
                relation = ArchiveRelationVersion.objects.create(patient_id=patient.id, relation_id=uuid4(), version=1,
                    left_fact_version_id=saved['fact_ids'][left], right_fact_version_id=saved['fact_ids'][right],
                    relation_type='independent_sampling', status='confirmed', evidence_ref=fixture['evidence_ref'],
                    reason='SIMULATION明确提供独立采样输入', author_id=owner.id, created_at=timezone.now(),
                    verification_id=saved['proof_ids'][right])
                saved['relation_version_ids'].append(str(relation.id))
