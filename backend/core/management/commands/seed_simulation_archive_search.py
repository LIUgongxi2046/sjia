import json
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from archives.batch_creation import save_received_batch
from archives.grouping import save_document
from archives.models import ArchiveDocument, ArchivePage
from authorization.models import AccessGrant
from case_details.models import ClinicalEpisode
from configurations.models import ConfigVersion
from configurations.schemas import validate_payload
from configurations.service import compute_checksum
from core.management.commands.seed_simulation import require_simulation
from files.models import FileObject, FilePage
from identity.models import Patient, PrincipalCredential
from records.models import RecordField
from records.service import create_record
from relations.models import Relation
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRevision


class Command(BaseCommand):
    help = '保存独立模拟文书、医院、就诊、确认文本与实际原件引用，仅用于资料检索开发'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        fixture = json.loads((settings.SIMULATION_ROOT / 't44-fixture.json').read_text())
        patient = Patient.objects.get(id=fixture['patient_id'])
        target = settings.SIMULATION_ROOT / 't45-fixture.json'
        if target.exists():
            saved = json.loads(target.read_text())
            if saved['mode'] != 'SIMULATION' or saved['patient_id'] != str(patient.id):
                raise RuntimeError('模拟目录资料身份与当前环境不一致')
            for identity in saved['document_ids']:
                ArchiveDocument.objects.get(id=identity, patient_id=patient.id)
            self.stdout.write('SIMULATION：已保存目录资料继续使用准确当前版本')
            return
        owner = PrincipalCredential.objects.get(username='simulation-family').principal
        executor = PrincipalCredential.objects.get(username='simulation-support').principal
        relation = Relation.objects.get(patient=patient, guardian=owner, status='active')
        source = SourceRevision.objects.get(id=fixture['evidence_source_id'])
        authorization = DataUseAuthorization.objects.get(patient=patient, source_revision=source,
            purpose='family_observation', environment='simulation', revoked_at=None)
        runtime = ConfigVersion.objects.get(config_key='simulation-runtime', status='enabled')
        quota_payload = {'schema_version': 'archive-quota-1', 'environment': 'simulation',
            'runtime_config_id': str(runtime.id), 'provider_config_id': str(runtime.id), 'model_version': 'SIMULATION-input-only',
            'measurement_evidence_ref': fixture['evidence_ref'], 'pricing_evidence_ref': fixture['evidence_ref'],
            'max_pages': 100, 'max_bytes': 20971520, 'max_inflight_pages': 10, 'per_page_reservation': '1',
            'per_batch_budget': '100', 'daily_budget': '1000', 'currency': 'CNY', 'budget_timezone': 'Asia/Shanghai',
            'queue_wait_seconds': 60, 'execution_seconds': 60, 'lease_seconds': 30, 'heartbeat_seconds': 5,
            'visibility_timeout_seconds': 120, 'max_attempts': 3, 'deadline_seconds': 180, 'acceptable_wait_seconds': 60}
        validate_payload('provider', 'archive-quota-1', quota_payload)
        quota = ConfigVersion.objects.create(config_key='simulation-directory-input-quota', kind='provider', version=1,
            schema_version='archive-quota-1', payload=quota_payload, checksum=compute_checksum(quota_payload),
            source_revision=source, status='draft', created_by=executor, created_at=timezone.now())
        pages = [FilePage.objects.get(file_id=identity, patient=patient, ordinal=1) for identity in fixture['files']]
        batch = save_received_batch(actor=owner, patient=patient, request_id=uuid4(), pages=pages, quota=quota)
        batch.status, batch.budget_status = 'awaiting_confirmation', 'unknown'
        batch.save(update_fields=['status', 'budget_status'])
        for action in ('read_raw', 'read_history', 'write_record', 'correct_record'):
            AccessGrant.objects.get_or_create(principal=owner, patient=patient, action=action,
                field_codes=['episode_context'], revoked_at=None, defaults={'relation_id': relation.id,
                    'record_scope': 'all', 'purpose': 'family_observation', 'granted_by': executor,
                    'evidence_ref': fixture['evidence_ref'], 'valid_from': timezone.now()})
        policy = RetentionPolicy.objects.get(policy_key='simulation-report', status='active')
        documents, episode_ids = [], []
        for index, source_id in enumerate(fixture['files']):
            source_file = FileObject.objects.get(id=source_id, patient=patient)
            archive_page = ArchivePage.objects.get(batch_id=batch.id, file_id=source_id, patient_id=patient.id)
            episodes = []
            if index != 2:
                kind = 'outpatient' if index == 1 else 'hospitalization'
                label = f'SIMULATION{ "门诊" if kind == "outpatient" else "住院" }就诊 {index + 1}'
                created = create_record(patient=patient, actor=owner, kind='professional', capture_method='manual',
                    origin_type='report_transcription', retention_policy=policy, use_authorization_id=authorization.id,
                    source_revision=source, file_ids=[source_file.id], source_organization=f'SIMULATION医院{index % 2 + 1}',
                    fields=[{'field_code': 'episode_context', 'original_name': '就诊原文', 'value_type': 'text',
                        'value_state': 'known', 'raw_text': label, 'sample_time_origin': 'unknown',
                        'source_file_id': str(source_file.id), 'source_text': label}])
                field = RecordField.objects.get(record_version_id=created['record_version_id'], field_code='episode_context')
                episode = ClinicalEpisode.objects.create(patient_id=patient.id, record_version_id=created['record_version_id'],
                    source_field_id=field.id, episode_type=kind, label=label, organization_text=f'SIMULATION医院{index % 2 + 1}')
                episodes.append(str(episode.id)); episode_ids.extend(episodes)
            payload = {'batch_id': str(batch.id), 'page_ids': [str(archive_page.id)],
                'document_type': ('inpatient', 'outpatient', 'unknown', 'laboratory')[index],
                'organization': None if index == 2 else f'SIMULATION医院{index % 2 + 1}',
                'documented_time': None if index == 2 else {'precision': 'date', 'raw_text': f'2026-10-0{index + 5}',
                    'local_date': f'2026-10-0{index + 5}', 'local_datetime': None, 'timezone_name': 'Asia/Shanghai',
                    'utc_offset_minutes': None, 'instant': None, 'resolution_status': 'local_only'},
                'episode_ids': episodes, 'confirmed_text': f'SIMULATION目录原文 {index + 1}；铁蛋白复查；原件和页序分别保存。'}
            document, _ = save_document(patient=patient, actor=owner, payload=payload)
            documents.append(str(document.id))
        target.write_text(json.dumps({'mode': 'SIMULATION', 'patient_id': str(patient.id), 'batch_id': str(batch.id),
            'document_ids': documents, 'episode_ids': episode_ids, 'quota_consumption': 'NOT_RUN',
            'ocr_processing': 'NOT_RUN'}, ensure_ascii=False, indent=2))
        self.stdout.write(f'SIMULATION：目录输入保存 {len(documents)} 份文书与 {len(episode_ids)} 次就诊；原件继续使用实际保存对象')
