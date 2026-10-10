import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from configurations.models import ConfigValidation, ConfigVersion
from configurations.schemas import validate_payload
from configurations.service import compute_checksum
from contacts.models import ContactVersion
from core.management.commands.seed_simulation import qualify_simulation_configuration, require_simulation
from family_content.models import EnvironmentGate
from family_content.qualification import release_qualification_reason
from family_content.service import activate_contact, add_release_component, approve_release, create_contact_version, create_release_bundle, enable_release
from identity.models import Principal
from retention.models import RetentionPolicy
from sources.models import SourceRevision


INPUT = settings.SIMULATION_ROOT / 't29-business-fixture.json'
TARGET = settings.SIMULATION_ROOT / 't29-publication-fixture.json'


@transaction.atomic
def prepare():
    require_simulation()
    saved = json.loads(INPUT.read_text())
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    actor = Principal.objects.get(id=saved['support_id'])
    source = SourceRevision.objects.get(id=saved['source_revision_id'])
    retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
    reference = saved['evidence_ref']
    if TARGET.exists():
        result = json.loads(TARGET.read_text())
        if gate.active_release_id is None or str(gate.active_release_id) != result['release_id']:
            raise RuntimeError('模拟发布清单与当前批准版本不匹配')
        reason = release_qualification_reason(gate.active_release)
        if reason:
            raise RuntimeError(reason)
        return result | {'reused': True}
    if gate.active_release_id is not None:
        raise RuntimeError('现有批准发布需要单独处理，不能由初始化替换')

    def create_config(key, kind, schema, payload, identity=None):
        validate_payload(kind, schema, payload)
        previous = ConfigVersion.objects.filter(config_key=key)
        version = (previous.order_by('-version').values_list('version', flat=True).first() or 0) + 1
        previous.filter(status='enabled').update(status='disabled')
        config = ConfigVersion.objects.create(id=identity or uuid4(), config_key=key, kind=kind, version=version,
            schema_version=schema, payload=payload, checksum=compute_checksum(payload), source_revision=source,
            status='enabled', created_by=actor, created_at=timezone.now())
        qualify_simulation_configuration(config, actor=actor, gate=gate, source=source, reference=reference, retention=retention)
        return config

    configs = {name: ConfigVersion.objects.get(id=identifier) for name, identifier in saved['config_ids'].items()}
    for config in configs.values():
        qualify_simulation_configuration(config, actor=actor, gate=gate, source=source, reference=reference, retention=retention)
    contact_payload = {'contact_id': str(uuid4()), 'contact_name': 'SIMULATION模拟站内联系',
        'purpose': 'clinical_policy', 'channel': 'in_app', 'channel_value': 'SIMULATION本地开发负责人',
        'owner_id': str(actor.id), 'environment': 'simulation', 'source_revision_id': str(source.id),
        'valid_until': (timezone.now() + timedelta(days=2)).isoformat()}
    approval = create_config('t29-simulation-contact-approval', 'content', 'contact-1', contact_payload)
    contact = create_contact_version(contact_id=contact_payload['contact_id'], contact_name=contact_payload['contact_name'],
        purpose=contact_payload['purpose'], channel=contact_payload['channel'], channel_value=contact_payload['channel_value'],
        owner=actor, source_revision=source, approval_config=approval, valid_until=timezone.datetime.fromisoformat(contact_payload['valid_until']))
    activate_contact(contact=contact, expected_gate_version=gate.version)
    gate.refresh_from_db()
    visibility_payload = dict(configs['visibility'].payload)
    field_codes = ['sjia', 'fever', 'ferritin', 'platelet_count', 'ast', 'triglycerides', 'fibrinogen',
        'cns', 'hemorrhagic_manifestations', 'active_arthritis', 'ldh', 'current_therapy', 'inpatient_status',
        'late_result_flag', 'observation_note']
    visibility_payload.update(field_codes=field_codes, clinical_fields=['level', 'classification', 'ms'])
    visibility = create_config('t29-simulation-visibility', 'visibility', 'visibility-1', visibility_payload)
    content_id, trigger_id = uuid4(), uuid4()
    trigger_payload = json.loads(json.dumps(configs['trigger'].payload))
    trigger_payload['publication'].update(content_config_id=str(content_id), visibility_config_id=str(visibility.id),
        contact_version_id=str(contact.id))
    trigger = create_config('t29-simulation-trigger', 'rule', 'alert-trigger-1', trigger_payload, trigger_id)
    content_payload = dict(configs['content'].payload)
    content_payload.update(rule_id=str(trigger.id))
    content = create_config('t29-simulation-approved-content', 'content', 'content-1', content_payload, content_id)
    summary_payload = dict(configs['summary'].payload)
    summary_payload.update(field_codes=field_codes)
    summary = create_config('t29-simulation-summary', 'summary', 'summary-1', summary_payload)
    validation = ConfigValidation.objects.filter(config=trigger, revoked_at=None, outcome='pass').order_by('-executed_at').first()
    if validation is None:
        raise RuntimeError('模拟发布缺少实际配置验证结果')
    validation_config = create_config('t29-simulation-publication-validation', 'validation', 'validation-1', {
        'schema_version': 'validation-1', 'environment': 'simulation', 'purpose': 'clinical_policy',
        'rule_id': str(trigger.id), 'window_policy_id': str(configs['window'].id),
        'dataset_revision_id': str(validation.dataset_revision_id), 'report_ref': validation.evidence_ref,
        'report_sha256': validation.evidence_sha256, 'evidence_refs': [reference]})
    release = create_release_bundle(environment='simulation', contact_version=contact,
        validation_config=validation_config, valid_until=contact.valid_until)
    components = {'rule': trigger, 'window': configs['window'], 'verification': configs['qualification'],
        'mapping': configs['unit_mapping'], 'visibility': visibility, 'content': content, 'notification': configs['notification']}
    for kind, config in components.items():
        add_release_component(release=release, component_kind=kind, config=config)
    approve_release(release=release)
    enable_release(release=release, environment_gate=gate, expected_gate_version=gate.version)
    reason = release_qualification_reason(release)
    if reason:
        raise RuntimeError(reason)
    contact.refresh_from_db()
    if ContactVersion.objects.get(id=contact.id).status != 'active':
        raise RuntimeError('模拟联系未通过保存重读')
    result = {'mode': 'SIMULATION_ONLY', 'patient_id': saved['patient_id'], 'release_id': str(release.id),
        'contact_version_id': str(contact.id), 'trigger_id': str(trigger.id), 'content_id': str(content.id),
        'visibility_id': str(visibility.id), 'summary_config_id': str(summary.id), 'validation_config_id': str(validation_config.id),
        'created_at': timezone.now().isoformat(), 'clinical_approval': 'NOT_RUN', 'reused': False}
    updated_input = saved | {'config_ids': saved['config_ids'] | {'trigger': str(trigger.id),
        'content': str(content.id), 'visibility': str(visibility.id), 'summary': str(summary.id)}}
    transaction.on_commit(lambda: INPUT.write_text(json.dumps(updated_input, ensure_ascii=False, indent=2)))
    transaction.on_commit(lambda: TARGET.write_text(json.dumps(result, ensure_ascii=False, indent=2)))
    return result


print(json.dumps(prepare(), ensure_ascii=False, indent=2))
