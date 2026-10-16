import hashlib
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from alerting.models import AlertEvaluation, AlertEvaluationInput, AlertEvent, AlertRecipient
from authorization.models import AccessGrant
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.service import compute_checksum
from contacts.models import ContactVersion
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from identity.models import Patient, PrincipalCredential
from identity.service import register_account
from records.models import RecordField, RecordVersion
from records.service import create_record
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision
from tasks.models import Task
from verification.service import verify_fields


FIELD_CODES = ['temp', 'rash', 'joint_discomfort', 'observation_note', 'report_item', 'medication_name', 'therapy_note', 'therapy_status', 'therapy_start', 'therapy_end', 'therapy_source']
ACTIONS = ['read_raw', 'write_record', 'read_history', 'read_file', 'manage_draft', 'receive_alert', 'create_summary', 'read_summary', 'request_correction', 'request_disposition', 'view_request', 'correct_record']


class Command(BaseCommand):
    help = '建立用户授权的本地模拟验收账号、资料、发布与提示'

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG or settings.DATABASES['default']['NAME'] not in ('sjia_dev', 'sjia_qa03'):
            raise RuntimeError('此命令仅允许在指定的本地开发或验收数据库执行')
        username = os.environ.get('SJIA_ACCEPTANCE_USERNAME')
        password = os.environ.get('SJIA_ACCEPTANCE_PASSWORD')
        if not username or not password:
            raise CommandError('SJIA_ACCEPTANCE_USERNAME and SJIA_ACCEPTANCE_PASSWORD are required')
        now = timezone.now()
        credential = PrincipalCredential.objects.filter(username=username).first()
        actor = credential.principal if credential else register_account(username=username, password=password)
        gate, _ = EnvironmentGate.objects.get_or_create(environment='test', defaults={'version': 1, 'enabled': True, 'updated_at': now})
        if not gate.enabled:
            raise RuntimeError('本地测试环境没有启用')
        source, _ = SourceRegistry.objects.get_or_create(source_key='qa02-simulation', defaults={'owner': 'local-acceptance', 'location': 'local', 'purpose': 'family_observation', 'sensitivity': 'simulation', 'update_frequency': 'manual', 'status': 'approved'})
        revision, _ = SourceRevision.objects.get_or_create(source=source, version='2026-10-04', defaults={'checksum': hashlib.sha256(b'QA02 simulated input').hexdigest(), 'license': 'user-authorized-local-simulation', 'allowed_use': ['family_observation'], 'status': 'approved', 'captured_at': now})
        retention, _ = RetentionPolicy.objects.get_or_create(policy_key='qa02-local', version=1, defaults={'data_class': 'observation', 'rules': {'local_simulation': True}, 'schema_version': 'retention-1', 'status': 'active'})
        patients = []
        for display_id in ['模拟患儿A', '模拟患儿B']:
            patient, _ = Patient.objects.get_or_create(display_id=display_id, defaults={'status': 'active', 'created_at': now})
            patients.append(patient)
            DataUseAuthorization.objects.get_or_create(patient=patient, source_revision=revision, purpose='family_observation', environment='test', defaults={'evidence_ref': '2026-10-04用户授权本地模拟数据', 'approved_by': actor, 'approved_at': now})
            relation = Relation.objects.filter(patient=patient, guardian=actor, status='active').first()
            if relation is None:
                application = RelationApplication.objects.create(version=1, applicant=actor, patient=patient, evidence_ref='local-simulation', status='approved', checked_by=actor, checked_at=now, created_at=now)
                relation = Relation.objects.create(patient=patient, guardian=actor, application=application, status='active', version=1, checked_by=actor, checked_at=now, valid_from=now)
            for action in ACTIONS:
                AccessGrant.objects.get_or_create(patient=patient, principal=actor, action=action, evidence_ref='qa02-simulation', defaults={'relation_id': relation.id, 'field_codes': FIELD_CODES, 'record_scope': 'all', 'purpose': 'family_observation', 'granted_by': actor, 'valid_from': now})

        def config(kind, key, payload, schema=None):
            existing = ConfigVersion.objects.filter(config_key=key, checksum=compute_checksum(payload), status='enabled').first()
            if existing:
                return existing
            if kind in ('summary', 'visibility'):
                ConfigVersion.objects.filter(kind=kind, status='enabled').update(status='disabled')
            version = (ConfigVersion.objects.filter(config_key=key).order_by('-version').values_list('version', flat=True).first() or 0) + 1
            item = ConfigVersion.objects.create(config_key=key, kind=kind, version=version, schema_version=schema or f'{kind}-1', payload=payload, checksum=compute_checksum(payload), source_revision=revision, status='enabled', created_by=actor, created_at=now)
            ConfigApproval.objects.create(config=item, approved_by=actor, approval_role='local_simulation', approved_at=now, evidence_ref='user-authorized-simulation', effective_scope={'environment': 'test', 'simulation': True})
            ConfigValidation.objects.create(config=item, config_checksum=item.checksum, environment=gate, artifact_sha256=item.checksum, dataset_revision=revision, evidence_ref='local-simulation-definition', evidence_sha256=revision.checksum, outcome='pass', executed_by=actor, executed_at=now)
            return item

        observation = config('content', 'qa02-observation', {'schema_version': 'field-config-1', 'kind': 'observation', 'fields': [
            {'field_code': 'rash', 'label': '皮疹观察', 'value_type': 'categorical', 'writable': True, 'allowed_values': ['yes', 'no']},
            {'field_code': 'joint_discomfort', 'label': '关节不适观察', 'value_type': 'categorical', 'writable': True, 'allowed_values': ['yes', 'no']},
        ], 'watch_field_codes': []}, 'field-config-1')
        # field-config 按版本选取同类当前定义，保留其他类型配置。
        ConfigVersion.objects.filter(kind='content', schema_version='field-config-1', payload__kind='observation').exclude(id=observation.id).update(status='disabled')
        summary = config('summary', 'qa02-summary', {'schema_version': 'summary-1', 'field_codes': FIELD_CODES})
        config('content', 'qa03-report', {'schema_version': 'field-config-1', 'kind': 'report',
            'fields': [{'field_code': 'report_item', 'label': '检验项目', 'value_type': 'text', 'writable': True}],
            'watch_field_codes': []}, 'field-config-1')
        visibility = config('visibility', 'qa02-visibility', {'schema_version': 'visibility-1', 'field_codes': FIELD_CODES, 'visible_score': False})
        content = config('content', 'qa02-alert-content', {'schema_version': 'content-1', 'approved_text': '这是本地模拟提示。请核对体温、症状及报告原文；出现需要关注的变化时，联系负责诊疗的医疗人员。本提示用于预警，不构成诊断。'})
        components = {kind: config(kind, f'qa02-{kind}', {'schema_version': f'{kind}-1', 'simulation': True}) for kind in ['rule', 'window', 'verification', 'mapping', 'notification', 'validation']}
        components.update({'visibility': visibility, 'content': content})
        contact = ContactVersion.objects.filter(contact_name='模拟联系渠道', status='active').first()
        if contact is None:
            contact = ContactVersion.objects.create(contact_id=uuid.uuid4(), version=1, contact_name='模拟联系渠道', purpose='local_simulation', channel='in_app', channel_value='本地站内提示', owner=actor, source_revision=revision, approval_config=components['validation'], verified_at=now, updated_at=now, valid_until=now + timedelta(days=365), status='active')
        release = ReleaseBundle.objects.filter(environment='test', status='enabled').first()
        if release is None or not ReleaseComponent.objects.filter(release=release, config=content).exists():
            ReleaseBundle.objects.filter(environment='test', status='enabled').update(status='disabled', disabled_at=now)
            version = (ReleaseBundle.objects.filter(environment='test').order_by('-version').values_list('version', flat=True).first() or 0) + 1
            release = ReleaseBundle.objects.create(environment='test', version=version, publication_epoch=gate.publication_epoch + 1, status='enabled', contact_version=contact, validation_config=components['validation'], activated_at=now)
            for kind, item in components.items():
                if kind != 'validation':
                    ReleaseComponent.objects.create(release=release, component_kind=kind, config=item)
            gate.active_release = release
            gate.publication_epoch += 1
            gate.version += 1
            gate.save(update_fields=['active_release', 'publication_epoch', 'version'])
        patient = patients[0]
        version = RecordVersion.objects.filter(patient=patient, source_organization='本地模拟资料').first()
        if version is None:
            use_auth = DataUseAuthorization.objects.get(patient=patient, source_revision=revision)
            record = create_record(patient=patient, actor=actor, kind='observation', capture_method='manual', origin_type='self_report', retention_policy=retention, use_authorization_id=use_auth.id, source_revision=revision, source_organization='本地模拟资料', fields=[{'field_code': 'temp', 'concept_code': 'temp', 'value_type': 'decimal', 'value_state': 'known', 'raw_numeric': '38.2', 'raw_unit': 'C', 'qualifier': 'eq', 'method': '模拟测量', 'medical_time': {'precision': 'date', 'local_date': '2026-10-04', 'resolution_status': 'local_only'}, 'sample_time_origin': 'observation'}])
            version = RecordVersion.objects.get(id=record['record_version_id'])
        field = RecordField.objects.get(record_version_id=version.id, field_code='temp')
        verification = verify_fields(patient=patient, record_version_id=version.id, verifier=actor, policy=components['verification'], purpose='local_simulation', evidence_ref='本地模拟核对', evidence_source=revision, field_results=[(field.id, 'consistent')])
        patient.refresh_from_db()
        sequence = (AlertEvaluation.objects.filter(patient=patient).order_by('-context_sequence').values_list('context_sequence', flat=True).first() or 0) + 1
        task = Task.objects.create(actor=actor, patient=patient, task_type='alert_evaluation', schema_version='task-1', input_refs={'simulation': True}, input_sha256=compute_checksum({'simulation': True}), status='completed', status_version=1, created_at=now, deadline_at=now)
        use_auth = DataUseAuthorization.objects.get(patient=patient, source_revision=revision)
        evaluation = AlertEvaluation.objects.create(patient=patient, task_id=task.id, environment='test', policy=components['rule'], window_policy=components['window'], assessment_at=now, known_at=now, context_sequence=sequence, mode='current', status='completed', decision='met', data_epoch=patient.data_epoch, verification_epoch=patient.verification_epoch, publication_epoch=gate.publication_epoch, authorization_version=1, lease_generation=1, use_authorization_id=use_auth.id, input_digest=compute_checksum({'simulation': True}), missing_items=[], computed_at=now, validity='valid')
        AlertEvaluationInput.objects.create(evaluation_id=evaluation.id, patient_id=patient.id, signal_id='simulation', position=1, variable_code='temp', input_role='current', record_version_id=version.id, field_id=field.id, verification_id=verification.id, medical_time_id=field.medical_time_id, source_revision=revision)
        event = AlertEvent.objects.create(patient=patient, evaluation_id=evaluation.id, release=release, trigger_key=f'qa02-{sequence}', trigger_policy=components['rule'], input_snapshot={'simulation': True}, validity='valid', created_at=now)
        relation = Relation.objects.get(patient=patient, guardian=actor, status='active')
        AlertRecipient.objects.create(patient_id=patient.id, alert_id=event.id, relation_id=relation.id, channel='in_app', delivery_status='delivered')
        self.stdout.write(f'本地模拟验收资料已建立：账号 {username}，患儿 {len(patients)}，摘要模板 {summary.version}，提示 {event.id}')
