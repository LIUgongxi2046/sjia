import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django

django.setup()

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from configurations.models import ConfigValidation, ConfigVersion
from configurations.service import compute_checksum
from configurations.schemas import validate_payload
from core.management.commands.seed_simulation import qualify_simulation_configuration, require_simulation
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.service import add_release_component, approve_release, create_release_bundle, disable_release, enable_release
from family_content.qualification import release_qualification_reason
from identity.models import PrincipalCredential
from retention.models import RetentionPolicy


@transaction.atomic
def refresh():
    require_simulation()
    gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
    actor = PrincipalCredential.objects.get(username='simulation-support').principal
    retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active')
    configs = list(ConfigVersion.objects.select_related('source_revision__source').filter(status='enabled',
        source_revision__source__source_key__startswith='simulation:').exclude(
            schema_version__in=['purpose-processing-2', 'purpose-processing-text-1']).order_by('id'))
    for config in configs:
        source = config.source_revision
        qualify_simulation_configuration(config, actor=actor, gate=gate, source=source,
            reference=f'urn:sjia:source-revision:{source.id}', retention=retention)
    result = {'mode': 'SIMULATION_ONLY', 'qualified_config_ids': [str(row.id) for row in configs],
        'checked_at': timezone.now().isoformat(), 'clinical_approval': 'NOT_RUN'}
    if gate.active_release_id is not None:
        release = gate.active_release
        components = list(ReleaseComponent.objects.filter(release=release).select_related('config'))
        trigger = next(row.config for row in components if row.component_kind == 'rule')
        old_validation = release.validation_config
        latest = ConfigValidation.objects.filter(config=trigger, outcome='pass', revoked_at=None).order_by('-executed_at').first()
        if latest is None:
            raise RuntimeError('发布规则缺少实际配置验证')
        if old_validation.payload['report_ref'] != latest.evidence_ref:
            payload = old_validation.payload | {'dataset_revision_id': str(latest.dataset_revision_id),
                'report_ref': latest.evidence_ref, 'report_sha256': latest.evidence_sha256}
            validate_payload('validation', 'validation-1', payload)
            previous = ConfigVersion.objects.filter(config_key=old_validation.config_key)
            version = previous.order_by('-version').values_list('version', flat=True).first() + 1
            previous.filter(status='enabled').update(status='disabled')
            validation = ConfigVersion.objects.create(config_key=old_validation.config_key, version=version,
                kind='validation', schema_version='validation-1', payload=payload, checksum=compute_checksum(payload),
                source_revision=old_validation.source_revision, created_by=actor, created_at=timezone.now(), status='enabled')
            source = validation.source_revision
            qualify_simulation_configuration(validation, actor=actor, gate=gate, source=source,
                reference=f'urn:sjia:source-revision:{source.id}', retention=retention)
            disable_release(release=release, environment_gate=gate, expected_gate_version=gate.version)
            replacement = create_release_bundle(environment='simulation', contact_version=release.contact_version,
                validation_config=validation, valid_until=release.valid_until)
            for component in components:
                add_release_component(release=replacement, component_kind=component.component_kind, config=component.config)
            approve_release(release=replacement)
            enable_release(release=replacement, environment_gate=gate, expected_gate_version=gate.version)
            release = replacement
        reason = release_qualification_reason(release)
        if reason:
            raise RuntimeError(reason)
        result.update(release_id=str(release.id), validation_config_id=str(release.validation_config_id),
            publication_evidence_saved_at=timezone.now().isoformat())
        target = settings.SIMULATION_ROOT / 't29-publication-fixture.json'
        saved = json.loads(target.read_text()) | {'release_id': str(release.id),
            'validation_config_id': str(release.validation_config_id), 'created_at': timezone.now().isoformat()}
        transaction.on_commit(lambda: target.write_text(json.dumps(saved, ensure_ascii=False, indent=2)))
    return result


print(json.dumps(refresh(), ensure_ascii=False, indent=2))
