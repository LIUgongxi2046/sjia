import json
from pathlib import Path
from uuid import uuid5

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from authorization.models import AccessGrant
from core.management.commands.seed_simulation import configuration, evidence, qualify_simulation_configuration, require_simulation
from dictionaries.service import materialize_configuration
from family_content.models import EnvironmentGate
from identity.models import Patient, PrincipalCredential
from relations.models import Relation
from retention.models import RetentionPolicy
from sources.models import DataUseAuthorization


def field(code, label, value_type, values=(), units=(), category=None, occurrences=1):
    return {'field_code': code, 'label': label, 'value_type': value_type, 'required': False, 'writable': True,
        'allowed_values': list(values), 'allowed_units': list(units), 'dictionary_category': category,
        'minimum': None, 'maximum': None, 'max_length': 10000, 'max_occurrences': occurrences, 'time_required': False}


class Command(BaseCommand):
    help = '建立用户授权的独立模拟字段配置与项目字典'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        owner = PrincipalCredential.objects.get(username='simulation-family').principal
        reviewer = PrincipalCredential.objects.get(username='simulation-support').principal
        patient = Patient.objects.get(display_id='SIMULATION模拟患儿A')
        relation = Relation.objects.get(guardian=owner, patient=patient, status='active')
        gate = EnvironmentGate.objects.select_for_update().get(environment='simulation')
        retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', version=1)
        source, reference = evidence(key='t04-family-field-input', payload={'mode': 'SIMULATION',
            'authorization': '2026-10-09用户授权全部模拟开发', 'scope': '模拟患儿A本人记录的观察与治疗字段',
            'clinical_approval': 'NOT_RUN'}, purposes=['configuration_approval', 'configuration_validation',
                'family_observation', 'clinical_policy'], retention=retention)
        definitions = {'observation': [field('temp', '体温', 'decimal', units=['C', 'F']),
            field('rash', '皮疹', 'categorical', values=['yes', 'no']),
            field('joint_discomfort', '关节疼痛或肿胀', 'categorical', values=['yes', 'no']),
            field('observation_note', '补充说明', 'text')],
            'report': [field('report_item', '原检验项目', 'decimal', category='laboratory', occurrences=100)],
            'therapy': [field('medication_name', '原药名', 'text'), field('therapy_note', '资料说明', 'text'),
                field('therapy_status', '实际使用状态', 'categorical', values=['ongoing', 'ended', 'not_started']),
                field('therapy_start', '开始日期', 'time'), field('therapy_end', '结束日期', 'time'), field('therapy_source', '资料来源', 'text'),
                field('dose', '单次剂量', 'decimal'), field('dose_unit', '剂量单位', 'text'),
                field('concentration_text', '原始浓度', 'text'), field('route_text', '原始给药途径', 'text'),
                field('frequency_text', '原始使用频次', 'text'), field('interval_value', '间隔数值', 'decimal'),
                field('interval_unit', '间隔单位', 'categorical', values=['hour', 'day', 'week', 'month'])]}
        configs = {}
        for kind, fields in definitions.items():
            config = configuration(key='simulation-' + kind + '-fields', kind='content', actor=reviewer, source=source,
                payload={'schema_version': 'field-config-1', 'kind': kind, 'input_context': 'family_record',
                    'fields': fields, 'watch_field_codes': []})
            qualify_simulation_configuration(config, actor=reviewer, gate=gate, source=source, reference=reference, retention=retention)
            configs[kind] = str(config.id)
            codes = [item['field_code'] for item in fields]
            for action in ('read_raw', 'write_record', 'read_history', 'correct_record'):
                AccessGrant.objects.get_or_create(principal=owner, patient=patient, relation_id=relation.id, action=action,
                    field_codes=codes, record_scope='own', purpose='family_observation', revoked_at=None,
                    evidence_ref=reference, defaults={'granted_by': reviewer, 'valid_from': timezone.now()})
        DataUseAuthorization.objects.get_or_create(patient=patient, source_revision=source, purpose='family_observation',
            environment='simulation', revoked_at=None, defaults={'approved_by': reviewer, 'approved_at': timezone.now(), 'evidence_ref': reference})
        dictionary = configuration(key='simulation-family-dictionary', kind='dictionary', actor=reviewer, source=source,
            payload={'schema_version': 'dictionary-1', 'entries': [
                {'id': str(uuid5(source.id, 'SIM-PLT-A')), 'concept_code': 'simulation_platelet', 'category': 'laboratory',
                    'label': '模拟血小板', 'definition_ref': reference,
                    'aliases': [{'original_alias': 'SIM PLT', 'source_revision_id': str(source.id)}]},
                {'id': str(uuid5(source.id, 'SIM-PLT-B')), 'concept_code': 'simulation_platelet_other', 'category': 'laboratory',
                    'label': '模拟血小板其他方法', 'definition_ref': reference,
                    'aliases': [{'original_alias': 'SIM PLT', 'source_revision_id': str(source.id)}]}], 'evidence_refs': [reference]})
        qualify_simulation_configuration(dictionary, actor=reviewer, gate=gate, source=source, reference=reference, retention=retention)
        materialize_configuration(dictionary)
        configs['dictionary'] = str(dictionary.id)
        result = {'mode': 'SIMULATION', 'patient_id': str(patient.id), 'source_revision_id': str(source.id),
            'reference': reference, 'configs': configs, 'retention_policy_id': str(retention.id),
            'source_id': str(source.source_id), 'scope': 'OWN_RECORDS_EXPLICIT_FIELDS', 'clinical_approval': 'NOT_RUN'}
        root = Path(__file__).resolve().parents[4]
        (root / '.planning-work/t04-field-fixture.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        self.stdout.write('SIMULATION：家庭字段与字典通过当前资格检查')
