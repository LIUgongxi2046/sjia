import hashlib
import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from authorization.models import AccessGrant, ServiceRoleGrant
from configurations.evidence import deployed_artifact_checksum
from configurations.models import ConfigApproval, ConfigValidation, ConfigVersion
from configurations.qualification import configuration_qualification_reason
from configurations.schemas import validate_payload
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from identity.models import Patient, PrincipalCredential
from identity.service import register_account
from notifications.material_delivery import material_configuration
from relations.models import Relation, RelationApplication
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization, SourceRegistry, SourceRevision


def require_simulation():
    if (not settings.DEBUG or settings.SJIA_ENVIRONMENT != 'simulation'
            or settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or settings.FILE_OBJECT_ROOT != settings.SIMULATION_ROOT / 'object-store'):
        raise RuntimeError('此命令仅允许在独立模拟环境执行')


def evidence(*, key, payload, purposes, retention):
    root = Path(settings.CONFIG_EVIDENCE_ROOT)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    checksum = hashlib.sha256(content).hexdigest()
    path = root / f'{key}-{checksum}.json'
    if not path.exists():
        path.write_bytes(content)
    source, _ = SourceRegistry.objects.get_or_create(source_key=f'simulation:{key}:{checksum}', defaults={
        'owner': 'SIMULATION', 'location': str(path), 'purpose': 'simulation', 'sensitivity': 'simulation',
        'update_frequency': 'manual', 'status': 'approved'})
    revision, _ = SourceRevision.objects.get_or_create(source=source, version='1', defaults={
        'checksum': checksum, 'license': 'user-authorized-local-simulation', 'allowed_use': purposes,
        'rights_evidence_ref': '2026-10-09用户授权模拟开发', 'retention_policy': retention,
        'captured_at': timezone.now(), 'status': 'approved'})
    return revision, f'urn:sjia:source-revision:{revision.id}'


def account(username):
    credential = PrincipalCredential.objects.filter(username=username).first()
    return credential.principal if credential else register_account(username=username,
        password=os.environ['SJIA_SIMULATION_PASSWORD'])


def configuration(*, key, kind, payload, actor, source):
    schema = payload['schema_version']
    validate_payload(kind, schema, payload)
    checksum = compute_checksum(payload)
    current = ConfigVersion.objects.filter(config_key=key, checksum=checksum, status='enabled').first()
    if current is not None:
        return current
    previous = ConfigVersion.objects.filter(config_key=key)
    version = (previous.order_by('-version').values_list('version', flat=True).first() or 0) + 1
    previous.filter(status='enabled').update(status='disabled')
    return ConfigVersion.objects.create(config_key=key, kind=kind, version=version, schema_version=schema,
        payload=payload, checksum=checksum, source_revision=source, status='enabled', created_by=actor,
        created_at=timezone.now())


def qualify_simulation_configuration(config, *, actor, gate, source, reference, retention):
    validate_payload(config.kind, config.schema_version, config.payload)
    ConfigApproval.objects.get_or_create(config=config, evidence_ref=reference, revoked_at=None, defaults={
        'approved_by': actor, 'approval_role': 'simulation_operator', 'approved_at': timezone.now(),
        'effective_scope': {'environment': 'simulation', 'audience': ['support', 'family'],
            'config_checksum': config.checksum, 'simulation': True}})
    executed_at = timezone.now()
    artifact = deployed_artifact_checksum(config)
    report = {'schema_version': 'config-validation-1', 'config_id': str(config.id),
        'config_checksum': config.checksum, 'environment': 'simulation', 'artifact_sha256': artifact,
        'dataset_revision_id': str(source.id), 'outcome': 'pass', 'executed_by': str(actor.id),
        'executed_at': executed_at.isoformat()}
    revision, report_ref = evidence(key=f'validation-{config.id}', payload=report,
        purposes=['configuration_validation'], retention=retention)
    ConfigValidation.objects.create(config=config, config_checksum=config.checksum, environment=gate,
        artifact_sha256=artifact, dataset_revision=source, evidence_ref=report_ref,
        evidence_sha256=revision.checksum, outcome='pass', executed_by=actor, executed_at=executed_at)
    reason = configuration_qualification_reason(config, 'simulation', audience='support')
    if reason is not None:
        raise RuntimeError(f'模拟配置未通过资格检查：{config.config_key} {reason}')


class Command(BaseCommand):
    help = '建立独立模拟账号、权限、运行配置和站内通知；仅用于模拟开发'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        owner = account('simulation-family')
        executor = account('simulation-support')
        gate, _ = EnvironmentGate.objects.get_or_create(environment='simulation', defaults={
            'version': 1, 'enabled': True, 'updated_at': timezone.now()})
        if not gate.enabled:
            raise RuntimeError('模拟环境已停用，请明确恢复环境后继续')
        for action in ('register_validation', 'manage_notifications', 'manage_config', 'decide_relation',
                'manage_grant', 'verify_field', 'read_assessment', 'execute_assessment', 'import_case',
                'manage_disposition', 'manage_data_use', 'revoke_verification'):
            ServiceRoleGrant.objects.get_or_create(principal=executor, environment=gate, action=action,
                revoked_at=None, evidence_ref='SIMULATION用户授权', defaults={'granted_by': executor,
                    'valid_from': timezone.now()})
        policies = {}
        for data_class in ('configuration_evidence', 'personal_record_task', 'notification', 'observation',
                'report', 'therapy', 'file', 'draft', 'archive'):
            policies[data_class], _ = RetentionPolicy.objects.get_or_create(
                policy_key=f'simulation-{data_class}', version=1, defaults={
                    'data_class': data_class, 'rules': {'simulation': True, 'preserve_until_user_disposition': True},
                    'schema_version': 'retention-1', 'status': 'active', 'approved_by': executor,
                    'approved_at': timezone.now(), 'evidence_ref': 'SIMULATION用户授权本地保存'})
            policies[data_class] = RetentionPolicy.objects.filter(policy_key=f'simulation-{data_class}',
                data_class=data_class, status='active', approved_by__isnull=False,
                approved_at__isnull=False).order_by('-version').first()
            if policies[data_class] is None:
                raise RuntimeError(f'模拟保留策略不可用：{data_class}')
        source, reference = evidence(key='authorized-simulation', payload={
            'mode': 'SIMULATION', 'authorization': '2026-10-09用户：全部先用模拟数据',
            'validation_scope': '配置结构、校验摘要、身份权限及站内消息开发；临床验收保持待验收'},
            purposes=['configuration_approval', 'configuration_validation', 'clinical_policy',
                'material_reminder_policy', 'family_observation'], retention=policies['configuration_evidence'])
        patient, _ = Patient.objects.get_or_create(display_id='SIMULATION模拟患儿A', defaults={
            'status': 'active', 'created_at': timezone.now()})
        relation = Relation.objects.filter(patient=patient, guardian=owner, status='active').first()
        if relation is None:
            application = RelationApplication.objects.create(version=1, applicant=owner, patient=patient,
                evidence_ref=reference, status='approved', checked_by=executor, checked_at=timezone.now(),
                created_at=timezone.now())
            relation = Relation.objects.create(patient=patient, guardian=owner, application=application,
                status='active', version=1, checked_by=executor, checked_at=timezone.now(), valid_from=timezone.now())
        DataUseAuthorization.objects.get_or_create(patient=patient, source_revision=source,
            purpose='family_observation', environment='simulation', defaults={'evidence_ref': reference,
                'approved_by': executor, 'approved_at': timezone.now()})
        for principal, actions in ((owner, ('read_raw', 'write_record', 'read_history', 'read_file', 'manage_draft',
                'receive_alert', 'create_summary', 'read_summary', 'request_correction', 'request_disposition',
                'view_request', 'correct_record')), (executor, ('read_case', 'verify_field', 'execute_assessment'))):
            for action in actions:
                AccessGrant.objects.get_or_create(patient=patient, principal=principal, action=action,
                    revoked_at=None, field_codes=[], purpose='family_observation', defaults={'relation_id': relation.id if principal == owner else None,
                        'record_scope': 'all',
                        'granted_by': executor, 'evidence_ref': reference, 'valid_from': timezone.now()})
        material = configuration(key='simulation-material-channel', kind='notification', actor=executor,
            source=source, payload={'schema_version': 'material-reminder-channel-2', 'environment': 'simulation',
                'purpose': 'family_observation', 'channel': 'in_app', 'provider_config_key': 'simulation-in-app',
                'runtime_config_key': 'simulation-runtime', 'notification_text':
                    '【模拟开发】您保存的资料任务已经到达提醒时间，请打开任务核对处理情况。',
                'max_due_per_tick': 100, 'evidence_refs': [reference]})
        native = configuration(key='simulation-in-app', kind='provider', actor=executor, source=source,
            payload={'schema_version': 'notification-in-app-2', 'environment': 'simulation',
                'purpose': 'family_observation', 'channel': 'in_app', 'endpoint': 'urn:sjia:notification:in-app:2',
                'event_purposes': ['clinical_policy'],
                'runtime_config_key': 'simulation-runtime', 'executor_id': str(executor.id),
                'signing_secret_ref': 'env:SJIA_SIMULATION_NOTIFICATION_KEY', 'permit_seconds': 3600,
                'delivery_ticket_seconds': 300, 'max_daily_messages': 10000, 'max_recipients_per_event': 20,
                'timezone': 'Asia/Shanghai', 'retention_policy_id': str(policies['notification'].id),
                'evidence_refs': [reference], 'material_reminder_policy_id': str(material.id)})
        from core.management.commands.seed_simulation_batches import provider_configuration

        batch_source, batch_reference = evidence(key='simulation-batch-development', payload={
            'mode': 'SIMULATION', 'authorization': '2026-10-09用户授权全部模拟输入',
            'measurement': {'queue_seconds': 60, 'execution_seconds': 300, 'scope': '模拟开发限制'},
            'pricing': {'input_per_million': '1', 'output_per_million': '2', 'currency': 'CNY', 'scope': '模拟计价，真实账单待验收'},
            'image_input': '程序创建的英文模拟文书；时间与内容均为模拟输入；没有姓名、身份、条码或二维码；元数据和文件名由处理程序移除'},
            purposes=['family_observation', 'external_recognition', 'configuration_approval', 'configuration_validation',
                'archive_measurement', 'archive_pricing'], retention=policies['configuration_evidence'])
        _, archive_provider = provider_configuration(executor, batch_source, batch_reference)
        task_definitions = [{'task_type': name, 'queue_wait_seconds': 60, 'execution_seconds': 60,
            'lease_seconds': 30, 'heartbeat_seconds': 5, 'visibility_timeout_seconds': 120,
            'max_attempts': 3, 'retry_delays_seconds': [1, 5], 'max_concurrency': 20}
            for name in ('file_validation', 'ocr', 'assessment', 'alert_evaluation', 'summary',
                'notification', 'disposition', 'reconcile')]
        runtime = configuration(key='simulation-runtime', kind='provider', actor=executor, source=source,
            payload={'schema_version': 'backend-runtime-1', 'environment': 'simulation',
                'api': {'lock_timeout_ms': 10000, 'statement_timeout_ms': 30000, 'request_timeout_ms': 60000,
                    'upload_timeout_ms': 120000, 'max_json_bytes': 1048576, 'allowed_origins': ['https://localhost'],
                    'intent_ttl_seconds': 900, 'max_page_size': 100, 'poll_after_ms': 1000},
                'session': {'absolute_ttl_seconds': 86400, 'idle_ttl_seconds': 86400, 'same_site': 'Lax',
                    'secure': True, 'http_only': True, 'csrf_use_sessions': True, 'csrf_header': 'X-CSRFToken',
                    'cookie_name': '__Host-sjia_session', 'return_paths': ['/home', '/records', '/account', '/associations']},
                'limits': {'max_query_length': 200, 'max_text_length': 4000, 'max_decimal_digits': 20,
                    'max_record_fields': 200, 'max_upload_bytes': 20971520, 'max_pixels': 40000000,
                    'max_pages': 100, 'media_types': ['image/png', 'image/jpeg', 'image/webp', 'application/pdf', 'text/plain'],
                    'rate_limits': [{'operation_id': 'simulation', 'window_seconds': 60,
                        'max_requests': 1000, 'max_inflight': 100}]},
                'tasks': task_definitions, 'providers': [{'kind': 'notification', 'config_id': str(native.id),
                    'enabled': True, 'endpoint': 'urn:sjia:notification:in-app:2',
                    'secret_ref': 'env:SJIA_SIMULATION_NOTIFICATION_KEY', 'region': 'local',
                    'processing_basis_ref': reference, 'connect_timeout_ms': 1000, 'read_timeout_ms': 1000,
                    'total_timeout_ms': 5000, 'submit_retries': 0, 'query_attempts': 2, 'query_delays_seconds': [1, 5],
                    'circuit_window_seconds': 60, 'circuit_failure_threshold': 5, 'circuit_open_seconds': 30,
                    'daily_budget': None, 'currency': None}, {'kind': 'ocr', 'config_id': str(archive_provider.id),
                    'enabled': True, 'endpoint': settings.ARK_BASE_URL.rstrip('/'), 'secret_ref': 'env:ARK_API_KEY',
                    'region': 'simulation', 'processing_basis_ref': batch_reference, 'connect_timeout_ms': 10000,
                    'read_timeout_ms': 180000, 'total_timeout_ms': 180000, 'submit_retries': 0,
                    'query_attempts': 0, 'query_delays_seconds': [], 'circuit_window_seconds': 60,
                    'circuit_failure_threshold': 5, 'circuit_open_seconds': 30, 'daily_budget': '1000', 'currency': 'CNY',
                    'billing_scope_key': 'simulation-ark-account', 'reserved_cost': '1'}],
                'retention_policy_ids': [str(policy.id) for policy in policies.values()],
                'operations_contact_ref': 'SIMULATION本地开发负责人'})
        gate.runtime_config = runtime
        gate.save(update_fields=['runtime_config'])
        for config in (runtime, native, material):
            qualify_simulation_configuration(config, actor=executor, gate=gate, source=source,
                reference=reference, retention=policies['configuration_evidence'])
        _, reason = material_configuration(gate)
        if reason is not None:
            raise RuntimeError(f'模拟站内消息不可用：{reason}')
        call_command('seed_simulation_relations')
        call_command('seed_simulation_archive_search')
        call_command('seed_simulation_delta')
        call_command('seed_simulation_period')
        call_command('seed_simulation_batches')
        call_command('seed_simulation_fields')
        self.stdout.write(f'SIMULATION：账号 simulation-family / simulation-support；患儿 {patient.id}；配置资格检查通过')
