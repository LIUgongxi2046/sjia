import json
import os
import argparse
from pathlib import Path

import django


ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')
django.setup()

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from family_content.models import EnvironmentGate, ReleaseComponent
from family_content.qualification import contact_qualification_reason, publication_policy_reason, release_qualification_reason


OUTPUT = ROOT / '.planning-work' / 'parallel' / 'window1' / 'current-simulation-qualification.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    OUTPUT = args.output.resolve()
    require(OUTPUT.is_relative_to((ROOT / '.planning-work' / 'parallel' / 'window1').resolve()),
        'Qualification report must remain in the window1 planning directory.')
    require(not OUTPUT.exists(), 'Qualification report path already exists; choose a new run-tagged path.')
    require(settings.DATABASES['default']['NAME'] == 'sjia_simulation',
        'Qualification inspection must use sjia_simulation.')
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            require(cursor.fetchone() == ('sjia_simulation', 'sjia', 5433),
                'Database identity differs from the approved simulation instance.')
        now = timezone.now()
        gate = EnvironmentGate.objects.select_related('runtime_config', 'active_release').get(
            environment=settings.SJIA_ENVIRONMENT)
        runtime = gate.runtime_config
        runtime_reason = configuration_qualification_reason(runtime, gate.environment, now,
            audience='support') if runtime is not None else 'RUNTIME_CONFIG_MISSING'
        runtime_providers = [] if runtime is None else runtime.payload['providers']
        provider_configs = ConfigVersion.objects.filter(id__in=[item['config_id'] for item in runtime_providers])
        provider_by_id = {str(row.id): row for row in provider_configs}
        providers = []
        for item in runtime_providers:
            config = provider_by_id.get(item['config_id'])
            providers.append({'kind': item['kind'], 'enabled': item['enabled'], 'config_id': item['config_id'],
                'config_status': config.status if config is not None else None,
                'config_schema_version': config.schema_version if config is not None else None,
                'config_qualification_reason': configuration_qualification_reason(config, gate.environment, now,
                    audience='support') if config is not None else 'PROVIDER_CONFIG_MISSING',
                'billing_scope_key': item.get('billing_scope_key'), 'daily_budget': item.get('daily_budget'),
                'currency': item.get('currency'), 'reserved_cost': item.get('reserved_cost')})
        release = gate.active_release
        if release is None:
            release_report = None
        else:
            components = ReleaseComponent.objects.filter(release_id=release.id).select_related('config').order_by(
                'component_kind', 'config_id')
            component_rows = list(components)
            release_validation = ConfigVersion.objects.get(id=release.validation_config_id)
            contact = release.contact_version
            contact_approval = ConfigVersion.objects.get(id=contact.approval_config_id)
            release_report = {'id': str(release.id), 'status': release.status,
                'publication_epoch': release.publication_epoch,
                'gate_publication_epoch': gate.publication_epoch,
                'qualification_reason': release_qualification_reason(release, now),
                'validation_config': {'id': str(release_validation.id), 'kind': release_validation.kind,
                    'schema_version': release_validation.schema_version, 'status': release_validation.status,
                    'qualification_reason': configuration_qualification_reason(release_validation, gate.environment,
                        now, audience='family')},
                'publication_policy_reason': publication_policy_reason(release, component_rows, gate, now),
                'contact': {'id': str(contact.id), 'status': contact.status,
                    'approval_config_id': str(contact_approval.id),
                    'approval_config_reason': configuration_qualification_reason(contact_approval,
                        gate.environment, now, audience='family'),
                    'qualification_reason': contact_qualification_reason(contact, gate.environment, now)},
                'components': [{'kind': row.component_kind, 'config_id': str(row.config_id),
                    'config_status': row.config.status, 'config_schema_version': row.config.schema_version,
                    'qualification_reason': configuration_qualification_reason(row.config, gate.environment, now,
                        audience='family')} for row in components]}
    report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'CURRENT_QUALIFICATION_READBACK',
        'database': {'name': 'sjia_simulation', 'schema': 'sjia', 'port': 5433},
        'observed_at': now.isoformat(), 'environment': gate.environment,
        'gate': {'enabled': gate.enabled, 'version': gate.version,
            'publication_epoch': gate.publication_epoch, 'runtime_config_id': str(runtime.id) if runtime else None,
            'runtime_config_reason': runtime_reason},
        'runtime_providers': providers, 'active_release': release_report,
        'external_calls': 0, 'model_calls': 0}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    OUTPUT.write_text(encoded, encoding='utf-8')
    require(json.loads(OUTPUT.read_text(encoding='utf-8')) == json.loads(encoded),
        'Saved qualification report differs from its readback.')
    print(json.dumps({'status': report['status'], 'runtime_config_id': report['gate']['runtime_config_id'],
        'runtime_config_reason': report['gate']['runtime_config_reason'],
        'release_reason': report['active_release']['qualification_reason'] if report['active_release'] else None,
        'output': str(OUTPUT.relative_to(ROOT)), 'saved_and_read': True}, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
