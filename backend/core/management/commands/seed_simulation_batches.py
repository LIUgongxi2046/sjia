import io
import json
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFont

from archives.models import PurposeCopy
from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.models import ConfigApproval, ConfigValidation
from core.management.commands.seed_simulation import require_simulation, evidence, configuration, qualify_simulation_configuration
from family_content.models import EnvironmentGate
from files.models import FileObject, FileGroup
from files.purpose import review_copy, checked_bytes, processing_page
from files.purpose_processing import process_image, processing_artifact_checksum
from files.purpose_jobs import submit_copy, execute_copy
from files.purpose_schemas import PurposeCopyInput, PurposeReviewInput, PurposeProcessingPlan
from configurations.models import ConfigVersion
from files.service import create_file_group, declare_upload_part, store_file_object, complete_file_group
from files.storage import digest_bytes
from identity.models import Patient, PrincipalCredential
from relations.models import Relation
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization
from tasks.models import OutboxEvent


def provider_configuration(actor, source, reference):
    field = configuration(key='simulation-archive-fields', kind='content', actor=actor, source=source,
        payload={'schema_version': 'field-config-1', 'kind': 'report', 'input_context': 'archive_extraction',
            'watch_field_codes': [], 'fields': [
            {'field_code': 'report_item', 'label': '模拟检验项目', 'value_type': 'decimal', 'required': False,
                'writable': True, 'allowed_values': [], 'allowed_units': ['ng/mL', 'mg/L'],
                'dictionary_category': None, 'minimum': None, 'maximum': None, 'max_length': 4000,
                'max_occurrences': 200, 'time_required': False},
            {'field_code': 'archive_text', 'label': '模拟文书原始文字', 'value_type': 'text', 'required': False,
                'writable': True, 'allowed_values': [], 'allowed_units': [], 'dictionary_category': None,
                'minimum': None, 'maximum': None, 'max_length': 16000, 'max_occurrences': 200, 'time_required': False},
            {'field_code': 'archive_time', 'label': '模拟文书时间项目', 'value_type': 'time', 'required': False,
                'writable': True, 'allowed_values': [], 'allowed_units': [], 'dictionary_category': None,
                'minimum': None, 'maximum': None, 'max_length': 4000, 'max_occurrences': 200, 'time_required': False}]})
    provider = configuration(key='simulation-archive-ocr', kind='provider', actor=actor, source=source,
        payload={'schema_version': 'provider-1', 'environment': 'simulation', 'provider': 'ark',
            'endpoint': settings.ARK_BASE_URL.rstrip('/'), 'model_version': settings.ARK_VISION_MODEL,
            'field_config_id': str(field.id), 'pricing_evidence_ref': reference,
            'input_per_million': '1', 'output_per_million': '2', 'currency': 'CNY', 'evidence_refs': [reference]})
    return field, provider


class Command(BaseCommand):
    help = '建立模拟批次原件和获准用途副本，执行实际图片处理并保存模拟配置证明'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        owner = PrincipalCredential.objects.get(username='simulation-family').principal
        reviewer = PrincipalCredential.objects.get(username='simulation-support').principal
        gate = EnvironmentGate.objects.get(environment='simulation')
        patient = Patient.objects.get(display_id='SIMULATION模拟患儿A')
        retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', version=1)
        source, reference = evidence(key='simulation-batch-development', payload={
            'mode': 'SIMULATION', 'authorization': '2026-10-09用户授权全部模拟输入',
            'measurement': {'queue_seconds': 60, 'execution_seconds': 300, 'scope': '模拟开发限制'},
            'pricing': {'input_per_million': '1', 'output_per_million': '2', 'currency': 'CNY', 'scope': '模拟计价，真实账单待验收'},
            'image_input': '程序创建的英文模拟文书；时间与内容均为模拟输入；没有姓名、身份、条码或二维码；元数据和文件名由处理程序移除'},
            purposes=['family_observation', 'external_recognition', 'configuration_approval', 'configuration_validation',
                'archive_measurement', 'archive_pricing'], retention=retention)
        field, provider = provider_configuration(reviewer, source, reference)
        runtime = gate.runtime_config
        quota = configuration(key='simulation-archive-quota', kind='provider', actor=reviewer, source=source,
            payload={'schema_version': 'archive-quota-1', 'environment': 'simulation', 'runtime_config_id': str(runtime.id),
                'provider_config_id': str(provider.id), 'model_version': settings.ARK_VISION_MODEL,
                'measurement_evidence_ref': reference, 'pricing_evidence_ref': reference, 'max_pages': 100,
                'max_bytes': 20971520, 'max_inflight_pages': 10, 'per_page_reservation': '1',
                'per_batch_budget': '100', 'daily_budget': '1000', 'currency': 'CNY', 'budget_timezone': 'Asia/Shanghai',
                'queue_wait_seconds': 60, 'execution_seconds': 300, 'lease_seconds': 360, 'heartbeat_seconds': 10,
                'visibility_timeout_seconds': 420, 'max_attempts': 3, 'deadline_seconds': 480, 'acceptable_wait_seconds': 60})
        for value in (field, provider, quota):
            qualify_simulation_configuration(value, actor=reviewer, gate=gate, source=source, reference=reference, retention=retention)
        relation = Relation.objects.get(patient=patient, guardian=owner, status='active')
        codes = ['report_item', 'archive_text', 'archive_time']
        for action in ('read_raw', 'write_record', 'read_history', 'correct_record'):
            AccessGrant.objects.get_or_create(patient=patient, principal=owner, action=action,
                purpose='family_observation', revoked_at=None, field_codes=codes, defaults={
                    'relation_id': relation.id, 'record_scope': 'all', 'granted_by': reviewer,
                    'evidence_ref': reference, 'valid_from': timezone.now()})
        for actor in (owner, reviewer):
            ServiceRoleGrant.objects.get_or_create(principal=actor, environment=gate, action='manage_data_use',
                revoked_at=None, evidence_ref=reference, defaults={'granted_by': reviewer, 'valid_from': timezone.now()})
            AccessGrant.objects.get_or_create(patient=patient, principal=actor, action='read_file', purpose='external_recognition',
                revoked_at=None, field_codes=[], defaults={'relation_id': relation.id if actor == owner else None,
                    'record_scope': 'all', 'granted_by': reviewer, 'evidence_ref': reference, 'valid_from': timezone.now()})
        authorization = {}
        for purpose in ('family_observation', 'external_recognition'):
            authorization[purpose], _ = DataUseAuthorization.objects.get_or_create(patient=patient, source_revision=source,
                purpose=purpose, environment='simulation', revoked_at=None, defaults={'approved_by': reviewer,
                    'approved_at': timezone.now(), 'evidence_ref': reference})
        target = settings.SIMULATION_ROOT / 't42-fixture.json'
        files, groups, copies = [], [], []
        if target.exists():
            fixture = json.loads(target.read_text())
            if fixture['mode'] != 'SIMULATION' or fixture['patient_id'] != str(patient.id):
                raise RuntimeError('模拟批次身份与当前环境不一致')
            for identity in fixture['copy_ids']:
                copy = PurposeCopy.objects.get(id=identity, status='approved', environment='simulation')
                config = ConfigVersion.objects.get(id=copy.processing_evidence['config_id'])
                plan = PurposeProcessingPlan.model_validate(config.payload)
                original = FileObject.objects.get(id=copy.original_file_id)
                page, number = processing_page(plan, original)
                derived = FileObject.objects.get(id=copy.derived_file_id)
                data, proof = process_image(plan=plan, original_bytes=checked_bytes(original),
                    original_media_type=original.media_type, page_number=number, limits=gate.runtime_config.payload['limits'])
                if data != checked_bytes(derived) or (proof['width'], proof['height']) != (page.width, page.height):
                    raise RuntimeError('当前模拟用途处理与已保存准确副本不一致')
                artifact = processing_artifact_checksum()
                verified, verified_ref = evidence(key=f'purpose-verification-{copy.id}',
                    payload={'mode': 'SIMULATION', 'copy_id': str(copy.id), 'config_checksum': config.checksum,
                        'artifact_sha256': artifact, 'actual_processing': proof, 'saved_bytes_equal': True},
                    purposes=['configuration_validation'], retention=retention)
                ConfigValidation.objects.get_or_create(config=config, config_checksum=config.checksum,
                    environment=gate, artifact_sha256=artifact, dataset_revision_id=FileGroup.objects.get(id=original.file_group_id).source_revision_id,
                    revoked_at=None, defaults={'evidence_ref': verified_ref, 'evidence_sha256': verified.checksum,
                        'outcome': 'pass', 'executed_by': reviewer, 'executed_at': timezone.now()})
            files, groups, copies = fixture['file_ids'], fixture['file_group_ids'], fixture['copy_ids']
            if len(files) >= 12:
                self.stdout.write('SIMULATION：模拟批次原件和用途副本继续使用保存结果')
                return
        font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 28)
        for index in range(len(files), 12):
            image = Image.new('RGB', (1300, 620), 'white')
            draw = ImageDraw.Draw(image)
            if index in (8, 10):
                lines = ['SIMULATION laboratory report',
                    f'Ferritin: {1800 if index == 8 else 1810}.00 ng/mL; sampled 2026-09-01 08:00',
                    'CRP: 24.0 mg/L; sampled 2026-09-02 10:30',
                    'Previous history: fever in August 2026.', 'No rash at current visit.',
                    'Document recorded on 2026-09-03.']
            elif index in (9, 11):
                lines = ['SIMULATION discharge record', 'Hospital: SIMULATION Clinic',
                    'Past history: joint pain in August 2026.', 'Current visit: no rash.',
                    'Discharge conclusion: fever has ended on 2026-09-03.',
                    'Plan: medication A to start in October 2026; not started.',
                    f'Actual treatment: medication {"B" if index == 9 else "C"} started on 2026-09-01.',
                    'Document recorded on 2026-09-04.']
            else:
                lines = ['SIMULATION laboratory report', f'Ferritin: {900 + index * 100}.00 ng/mL', f'CRP: {15 + index}.0 mg/L']
            for line_number, text in enumerate(lines):
                draw.text((32, 32 + line_number * 65), text, font=font, fill='black')
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            data = buffer.getvalue()
            group = create_file_group(patient=patient, owner=owner, source_revision=source,
                use_authorization_id=authorization['family_observation'].id)
            part = declare_upload_part(file_group=group, client_upload_id=uuid4(), ordinal=1,
                declared_checksum=digest_bytes(data), declared_size_bytes=len(data), declared_media_type='image/png')
            original = store_file_object(file_group=group, upload_part=part, data=data,
                purpose='original', retention_policy=RetentionPolicy.objects.get(policy_key='simulation-file', version=1))
            complete_file_group(file_group=group)
            plan = {'schema_version': 'purpose-processing-2', 'original_file_id': str(original.id),
                'original_checksum': original.checksum, 'original_version': original.object_version,
                'patient_id': str(patient.id), 'environment': 'simulation', 'purpose': 'external_recognition',
                'target': settings.ARK_BASE_URL.rstrip('/'), 'mode': 'redact_regions', 'redactions': [],
                'prepared_file_id': None, 'prepared_checksum': None, 'date_strategy': 'unchanged', 'date_transformation': None,
                'identity_coverage': [{'category': category, 'action': 'removed' if category in ('file_name', 'metadata') else 'not_present',
                    'evidence_ref': reference} for category in ('body_text', 'header_footer', 'image_identity', 'barcode', 'qrcode', 'file_name', 'metadata')],
                'required_scopes': ['image_text', 'model_input', 'log'], 'clinical_preservation_evidence_ref': reference}
            config = configuration(key=f'simulation-batch-purpose-{original.id}', kind='provider', actor=reviewer, source=source, payload=plan)
            ConfigApproval.objects.create(config=config, approved_by=reviewer, approval_role='data_processing',
                approved_at=timezone.now(), evidence_ref=reference, effective_scope={'patient_id': str(patient.id),
                    'original_file_id': str(original.id), 'purpose': 'external_recognition', 'target': plan['target'],
                    'environment': 'simulation', 'config_checksum': config.checksum})
            artifact = processing_artifact_checksum()
            ConfigValidation.objects.create(config=config, config_checksum=config.checksum, environment=gate,
                artifact_sha256=artifact, dataset_revision=source, evidence_ref=reference, evidence_sha256=source.checksum,
                outcome='pass', executed_by=reviewer, executed_at=timezone.now())
            task = submit_copy(file_id=original.id, actor=owner, auth_epoch=owner.auth_epoch,
                payload=PurposeCopyInput.model_validate({'purpose': 'external_recognition', 'target': plan['target'],
                    'authorization_id': str(authorization['external_recognition'].id), 'processing_config_id': str(config.id)}))
            execute_copy(OutboxEvent.objects.get(task=task, event_type='purpose_copy.requested'), f'simulation-batch-copy-{task.id}')
            copy = PurposeCopy.objects.get(processing_evidence__task_id=str(task.id))
            derived = FileObject.objects.get(id=copy.derived_file_id)
            review_copy(copy_id=copy.id, actor=reviewer, auth_epoch=reviewer.auth_epoch, request_id=uuid4(),
                payload=PurposeReviewInput.model_validate({'expected_version': '1', 'checksum': derived.checksum,
                    'scope_results': [{'scope': scope, 'result': 'passed' if scope in plan['required_scopes'] else 'not_applicable'}
                        for scope in ('image_text', 'confirmed_text', 'model_input', 'log', 'presentation')],
                    'clinical_integrity': 'passed', 'date_shift_consistency': 'not_applicable', 'evidence_ref': reference}))
            groups.append(str(group.id)); files.append(str(original.id)); copies.append(str(copy.id))
        target.write_text(json.dumps({'mode': 'SIMULATION', 'patient_id': str(patient.id), 'file_ids': files,
            'file_group_ids': groups, 'copy_ids': copies, 'quota_id': str(quota.id), 'provider_id': str(provider.id),
            'pricing_scope': 'SIMULATION', 'REAL_NOT_RUN': True}, ensure_ascii=False, indent=2))
        self.stdout.write('SIMULATION：十二份模拟文书原件、准确接收回执和用途副本已保存')
