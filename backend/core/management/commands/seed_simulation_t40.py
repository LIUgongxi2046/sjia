from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from authorization.models import AccessGrant, ServiceRoleGrant
from core.management.commands.seed_simulation import account, configuration, evidence, qualify_simulation_configuration, require_simulation
from family_content.models import EnvironmentGate
from identity.models import Patient, PrincipalCredential
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization


class Command(BaseCommand):
    help = '建立仅供 SIMULATION 使用的外部副本处置策略与独立复核身份'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
        support = PrincipalCredential.objects.select_for_update().get(username='simulation-support').principal
        reviewer = account('simulation-reviewer')
        patient = Patient.objects.get(display_id='SIMULATION模拟患儿A', status='active')
        receipt_policy = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', version=1,
            status='active')
        revision, reference = evidence(key='t40-provider-deletion-simulation', payload={
            'mode': 'SIMULATION', 'authorization': '用户授权全部使用模拟资料',
            'purpose': '仅验证本地模拟提供方副本的逐项处置流程',
            'external_provider': 'NOT_RUN', 'real_data': 'NOT_RUN'},
            purposes=['configuration_approval', 'configuration_validation'], retention=receipt_policy)
        policy, _ = RetentionPolicy.objects.get_or_create(policy_key='simulation-file', version=3, defaults={
            'data_class': 'file', 'rules': {'simulation': True, 'preserve_until_user_disposition': True,
                'local_delete': {'schema_version': 'local-disposition-2',
                    'target_types': ['file_object', 'purpose_copy', 'provider_copy'],
                    'access_purpose': 'external_recognition', 'receipt_policy_id': str(receipt_policy.id)}},
            'schema_version': 'retention-1', 'status': 'active', 'approved_by': support,
            'approved_at': timezone.now(), 'evidence_ref': reference})
        policy.refresh_from_db()
        expected_rule = {'schema_version': 'local-disposition-2',
            'target_types': ['file_object', 'purpose_copy', 'provider_copy'],
            'access_purpose': 'external_recognition', 'receipt_policy_id': str(receipt_policy.id)}
        if policy.data_class != 'file' or policy.status != 'active' or policy.rules.get('local_delete') != expected_rule:
            raise RuntimeError('T40 SIMULATION 保留策略与批准用途不符')

        runtime = gate.runtime_config
        runtime_payload = dict(runtime.payload)
        other_policy_ids = [str(policy_id) for policy_id in RetentionPolicy.objects.filter(
            policy_key__startswith='simulation-', version=1, status='active').exclude(data_class='file')
            .values_list('id', flat=True)]
        runtime_payload['retention_policy_ids'] = [*other_policy_ids, str(policy.id)]
        runtime_source, runtime_reference = evidence(key='t40-provider-deletion-runtime', payload={
            'mode': 'SIMULATION', 'authorization': '仅更新本地模拟文件保留策略引用',
            'runtime_config_id': str(runtime.id), 'retention_policy_id': str(policy.id)},
            purposes=['configuration_approval', 'configuration_validation'], retention=receipt_policy)
        runtime = configuration(key='simulation-runtime', kind='provider', payload=runtime_payload,
            actor=support, source=runtime_source)
        gate.runtime_config = runtime
        gate.save(update_fields=['runtime_config'])
        qualify_simulation_configuration(runtime, actor=support, gate=gate, source=runtime_source,
            reference=runtime_reference, retention=receipt_policy)
        RetentionPolicy.objects.filter(policy_key='simulation-file', version=2, status='active').update(status='disabled')

        for action in ('manage_data_use', 'verify_field'):
            ServiceRoleGrant.objects.get_or_create(principal=reviewer, environment=gate, action=action,
                revoked_at=None, evidence_ref=reference, defaults={'granted_by': support,
                    'valid_from': timezone.now()})
        use = DataUseAuthorization.objects.filter(patient=patient, purpose='external_recognition',
            environment='simulation', revoked_at__isnull=True, source_revision__source__source_key__startswith=
            'simulation:simulation-batch-development:').order_by('-approved_at').first()
        if use is None:
            raise RuntimeError('T40 SIMULATION 外部识别用途授权尚未初始化')
        AccessGrant.objects.get_or_create(patient=patient, principal=reviewer, action='read_file',
            purpose='external_recognition', revoked_at=None, field_codes=[], defaults={
                'relation_id': None, 'record_scope': 'all', 'granted_by': support,
                'evidence_ref': reference, 'valid_from': timezone.now()})
        self.stdout.write(f'SIMULATION T40策略={policy.id} 运行配置={runtime.id} 复核账号=simulation-reviewer 授权={use.id}')
