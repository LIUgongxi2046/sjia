import io
import json
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from PIL import Image, ImageDraw

from archives.models import ArchiveFactVersion
from authorization.models import AccessGrant
from core.management.commands.seed_simulation import require_simulation, evidence, configuration, qualify_simulation_configuration
from family_content.models import EnvironmentGate
from files.service import create_file_group, declare_upload_part, store_file_object, complete_file_group
from files.storage import digest_bytes
from identity.models import Patient, PrincipalCredential
from records.models import Record, RecordVersion
from records.service import create_record
from records.correction import correct_record
from retention.models import RetentionPolicy
from relations.models import Relation
from sources.models import DataUseAuthorization


class Command(BaseCommand):
    help = '保存模拟原件、采样及更正资料；全部通过实际文件与记录服务生成'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        owner = PrincipalCredential.objects.get(username='simulation-family').principal
        executor = PrincipalCredential.objects.get(username='simulation-support').principal
        patient = Patient.objects.get(display_id='SIMULATION模拟患儿A')
        gate = EnvironmentGate.objects.get(environment='simulation')
        evidence_policy = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', version=1)
        source, reference = evidence(key='t44-sampling', payload={
            'mode': 'SIMULATION', 'purpose': '重复文件、同次采样引用、独立采样和更正关系的开发验证',
            'authorization': '2026-10-09用户授权全部使用模拟数据'},
            purposes=['family_observation', 'configuration_approval', 'configuration_validation', 'clinical_policy', 'verification_evidence'], retention=evidence_policy)
        policy = configuration(key='simulation-sampling-qualification', kind='verification', actor=executor, source=source,
            payload={'schema_version': 'qualification-policy-2', 'purpose': 'family_observation',
                'allowed_origin_types': ['report_transcription'], 'verification_evidence_purpose': 'verification_evidence', 'evidence_refs': [reference]})
        qualify_simulation_configuration(policy, actor=executor, gate=gate, source=source, reference=reference, retention=evidence_policy)
        authorization, _ = DataUseAuthorization.objects.get_or_create(patient=patient, source_revision=source,
            purpose='family_observation', environment='simulation', defaults={'approved_by': executor,
                'approved_at': source.captured_at, 'evidence_ref': reference})
        family_relation = Relation.objects.get(patient=patient, guardian=owner, status='active')
        for actor, actions in ((owner, ('read_raw', 'read_history', 'write_record', 'correct_record')),
                (executor, ('read_case', 'verify_field', 'execute_assessment'))):
            for action in actions:
                AccessGrant.objects.get_or_create(patient=patient, principal=actor, action=action, field_codes=['ferritin'],
                    revoked_at=None, defaults={'relation_id': family_relation.id if actor == owner else None,
                        'record_scope': 'all', 'purpose': 'family_observation', 'granted_by': executor,
                        'evidence_ref': reference, 'valid_from': source.captured_at})
        file_policy = RetentionPolicy.objects.get(policy_key='simulation-file', version=1)
        record_policy = RetentionPolicy.objects.get(policy_key='simulation-report', version=1)
        manifest_path = settings.SIMULATION_ROOT / 't44-fixture.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest['mode'] != 'SIMULATION' or manifest['patient_id'] != str(patient.id):
                raise RuntimeError('模拟资料身份与当前环境不一致')
            for identity in manifest['facts']:
                ArchiveFactVersion.objects.get(id=identity, patient_id=patient.id)
            manifest['policy_id'] = str(policy.id)
            manifest['evidence_ref'] = reference
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            self.stdout.write(json.dumps({'mode': 'SIMULATION', 'fixture': str(manifest_path), 'reused': True}))
            return
        files, facts, records, field_inputs = [], [], [], []
        for index in range(4):
            image = Image.new('RGB', (640, 480), 'white')
            ImageDraw.Draw(image).text((25, 25), f'SIMULATION T44 SAMPLE {0 if index < 2 else index}', fill='black')
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            content = buffer.getvalue()
            group = create_file_group(patient=patient, owner=owner, source_revision=source, use_authorization_id=authorization.id)
            part = declare_upload_part(file_group=group, client_upload_id=uuid4(), ordinal=1,
                declared_checksum=digest_bytes(content), declared_size_bytes=len(content), declared_media_type='image/png')
            stored = store_file_object(file_group=group, upload_part=part, data=content, purpose='original', retention_policy=file_policy)
            complete_file_group(file_group=group)
            field = {'field_code': 'ferritin', 'concept_code': 'ferritin', 'original_name': f'SIMULATION铁蛋白 {index + 1}',
                'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '900', 'qualifier': 'eq', 'raw_unit': 'ng/mL',
                'sample_time_origin': 'item', 'source_file_id': str(stored.id), 'source_text': 'SIMULATION ferritin 900 ng/mL',
                'medical_time': {'precision': 'date', 'raw_text': f'2026-10-{7 if index < 3 else 8:02}',
                    'local_date': f'2026-10-{7 if index < 3 else 8:02}', 'timezone_name': 'Asia/Shanghai',
                    'resolution_status': 'local_only'}}
            saved = create_record(patient=patient, actor=owner, kind='report', capture_method='manual', origin_type='report_transcription',
                retention_policy=record_policy, use_authorization_id=authorization.id, fields=[field], source_revision=source,
                file_ids=[stored.id], report_metadata={'external_ref': f'SIMULATION-report-{0 if index < 3 else 1}', 'report_status': 'final'})
            fact = ArchiveFactVersion.objects.get(record_version_id=saved['record_version_id'], patient_id=patient.id)
            files.append(str(stored.id)); facts.append(str(fact.id)); records.append(saved['record_id']); field_inputs.append(field)
        corrected = correct_record(record=Record.objects.get(id=records[3]), expected_version=1, actor=owner,
            reason='SIMULATION正式更正原值', fields=[field_inputs[3] | {'source_file_id': None, 'anchor_id': str(ArchiveFactVersion.objects.get(id=facts[3]).source_anchor_id), 'raw_numeric': '950'}],
            use_authorization_id=authorization.id, retention_policy=record_policy, source_revision=source)
        corrected_fact = ArchiveFactVersion.objects.get(record_version_id=corrected['record_version_id'], patient_id=patient.id)
        foreign, _ = Patient.objects.get_or_create(display_id='SIMULATION隔离权限患儿B', defaults={'status': 'active', 'created_at': source.captured_at})
        manifest = {'mode': 'SIMULATION', 'patient_id': str(patient.id), 'foreign_patient_id': str(foreign.id), 'files': files,
            'facts': facts + [str(corrected_fact.id)], 'records': records, 'policy_id': str(policy.id), 'evidence_ref': reference,
            'evidence_source_id': str(source.id)}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        self.stdout.write(json.dumps({'mode': 'SIMULATION', 'fixture': str(manifest_path), 'files': len(files), 'facts': len(manifest['facts'])}))
