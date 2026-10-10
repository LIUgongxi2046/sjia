import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from configurations.models import ConfigVersion
from core.management.commands.seed_simulation import qualify_simulation_configuration, require_simulation
from family_content.models import EnvironmentGate
from identity.models import Principal
from retention.models import RetentionPolicy
from sources.models import SourceRevision


ROOT = Path(__file__).resolve().parents[4]
INPUTS = settings.SIMULATION_ROOT / 't29-business-fixture.json'
REPORT = ROOT / '.planning-work' / 'parallel' / 'window1' / 't29-configuration-proofs.json'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Command(BaseCommand):
    help = '刷新T29及其通知Provider的模拟配置资格证明'

    @transaction.atomic
    def handle(self, *args, **options):
        require_simulation()
        require(connection.settings_dict['NAME'] == 'sjia_simulation', 'Database must be sjia_simulation.')
        saved = json.loads(INPUTS.read_text(encoding='utf-8'))
        require(saved['mode'] == 'SIMULATION_ONLY' and saved['patient_id'] == '405514ce-9335-4705-a750-d811148bda4c',
            'T29 simulation input identity or mode is invalid.')
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
            database, schema, port = cursor.fetchone()
        require((database, schema, port) == ('sjia_simulation', 'sjia', 5433), 'Database identity is outside the permitted simulation scope.')

        gate = EnvironmentGate.objects.select_for_update().get(environment='simulation', enabled=True)
        actor = Principal.objects.get(id=saved['support_id'], account_status='active')
        evidence_source = SourceRevision.objects.get(id=saved['source_revision_id'], status='approved')
        retention = RetentionPolicy.objects.get(policy_key='simulation-configuration_evidence', status='active',
            approved_by__isnull=False, approved_at__isnull=False)
        provider_ids = [entry['config_id'] for entry in gate.runtime_config.payload['providers']
            if entry['kind'] == 'notification' and entry['enabled']]
        required_ids = set(saved['config_ids'].values()) | {str(gate.runtime_config_id), *provider_ids}
        configs = list(ConfigVersion.objects.select_for_update().filter(id__in=required_ids,
            status='enabled').order_by('config_key', 'id'))
        require({str(row.id) for row in configs} == required_ids, 'Enabled T29 or simulation notification configurations are incomplete.')

        results = []
        for config in configs:
            qualify_simulation_configuration(config, actor=actor, gate=gate, source=evidence_source,
                reference=saved['evidence_ref'], retention=retention)
            results.append({'config_id': str(config.id), 'config_key': config.config_key, 'checksum': config.checksum,
                'qualified_at': timezone.now().isoformat()})

        report = {'task_id': 'T29', 'mode': 'SIMULATION_ONLY', 'status': 'PASS',
            'database': {'name': database, 'schema': schema, 'port': port},
            'patient_id': saved['patient_id'], 'evidence_source_revision_id': str(evidence_source.id),
            'validation_dataset_revision_id': str(evidence_source.id), 'configurations': results,
            'configuration_count': len(results), 'business_object_writes': 0, 'model_calls': 0,
            'notification_sends': 0, 'external_calls': 0}
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        reread = json.loads(REPORT.read_text(encoding='utf-8'))
        require(reread == report, 'T29 configuration proof report readback differs from saved content.')
        self.stdout.write(json.dumps({'status': report['status'], 'configuration_count': len(results),
            'report_path': str(REPORT), 'saved_and_read': True}, ensure_ascii=False))
