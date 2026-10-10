import argparse
import csv
import hashlib
import json
import os
import platform
import stat
import sys
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import django
import httpx
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'
django.setup()

from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from configurations.evidence import deployed_artifact_checksum
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from core.management.commands.seed_simulation import require_simulation
from drafts.models import Draft
from identity.models import Patient


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == 'script' and attributes.get('src'):
            self.paths.add(attributes['src'])
        if tag == 'link' and attributes.get('rel') in ('stylesheet', 'modulepreload'):
            self.paths.add(attributes['href'])


parser = argparse.ArgumentParser(description='SIMULATION 本地制品与实际业务运行检查')
parser.add_argument('--web', default='http://127.0.0.1:5184')
parser.add_argument('--api', default='http://127.0.0.1:8001')
parser.add_argument('--output', default='.planning-work/coordination/root/t33-delivery.json')
args = parser.parse_args()
for address in (args.web, args.api):
    parsed = urlparse(address)
    assert parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and not parsed.username
output = (ROOT / args.output).resolve()
assert output.is_relative_to(ROOT / '.planning-work')
require_simulation()
assert stat.S_IMODE((settings.SIMULATION_ROOT / 'environment.env').stat().st_mode) == 0o600
checks = []


def checked(client, method, path, expected=200, **kwargs):
    response = client.request(method, path, **kwargs)
    assert response.status_code == expected, (method, path, response.status_code, response.text[:500])
    return response


with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), current_schema(), version(), inet_server_port()')
    database, schema, database_version, database_port = cursor.fetchone()
assert database == 'sjia_simulation' and schema == 'sjia' and database_port == 5433
loader = MigrationLoader(connection)
assert not set(loader.graph.leaf_nodes()).difference(loader.applied_migrations)
checks.append('actual_database_identity_and_applied_migrations')
configs = []
for config in ConfigVersion.objects.filter(status='enabled').order_by('config_key'):
    reason = configuration_qualification_reason(config, 'simulation', audience=None)
    configs.append({'id': str(config.id), 'key': config.config_key, 'version': config.version,
        'checksum': config.checksum, 'artifact_sha256': deployed_artifact_checksum(config),
        'qualification_reason': reason})
assert configs
checks.append('configuration_inventory_with_exact_qualification')
patient = Patient.objects.get(display_id='SIMULATION模拟患儿A')
with httpx.Client(base_url=args.api, timeout=30, trust_env=False) as api:
    readiness = checked(api, 'GET', '/internal/readiness?environment=simulation').json()
    assert readiness['status'] == 'ready' and all(readiness['dependencies'].values())
    assert readiness['loaded_config_version']['environment'] == 'simulation'
    runtime_id = readiness['loaded_config_version']['runtime_config_id']
    assert next(config for config in configs if config['id'] == runtime_id)['qualification_reason'] is None
checks.append('live_api_database_broker_and_loaded_runtime')

assets = []
routes = []
with httpx.Client(base_url=args.web, timeout=30, trust_env=False) as web:
    html = checked(web, 'GET', '/').content
    assert html == (ROOT / 'guardian-h5/dist/index.html').read_bytes()
    parsed_html = Assets()
    parsed_html.feed(html.decode())
    assert parsed_html.paths
    for path in sorted(parsed_html.paths):
        assert path.startswith('/assets/')
        content = checked(web, 'GET', path).content
        built = ROOT / 'guardian-h5/dist' / path.removeprefix('/')
        assert content == built.read_bytes()
        assets.append({'path': path, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
    checks.append('served_index_and_assets_equal_build')
    with (ROOT / 'frontend-design/v2-routes.csv').open(newline='') as source:
        route_definitions = list(csv.DictReader(source))
    assert len(route_definitions) == 39
    for route in route_definitions:
        segments = route['path'].split('/')
        path = '/'.join(str(uuid.uuid4()) if segment.startswith(':') else segment for segment in segments)
        if path == '*':
            path = '/simulation-route-not-found'
        assert checked(web, 'GET', path).content == html
        routes.append({'screen_id': route['screen_id'], 'path': path, 'status': 200,
            'scope': 'SPA entry only; page business and visual are NOT_RUN'})
    checks.append('all_39_deep_links_serve_current_spa_entry')
    checked(web, 'GET', '/api/session', expected=401)
    checked(web, 'GET', f'/api/patients/{patient.id}/drafts', expected=401)
    bootstrap = checked(web, 'GET', '/api/auth/bootstrap').json()
    login = checked(web, 'POST', '/api/auth/login', json={'username': 'simulation-family',
        'password': os.environ['SJIA_SIMULATION_PASSWORD']}, headers={'X-CSRFToken': bootstrap['csrf_token']}).json()
    headers = {'X-CSRFToken': login['csrf_token']}
    checked(web, 'GET', '/api/session')
    relations = checked(web, 'GET', '/api/relations').json()
    assert str(patient.id) in json.dumps(relations)
    checks.append('anonymous_denial_and_actual_family_session_relation')
    for kind in ('observation', 'report', 'therapy'):
        field_config = checked(web, 'GET', f'/api/patients/{patient.id}/field-config?kind={kind}').json()
        assert field_config['state'] == 'available'
        if kind == 'therapy':
            assert len(field_config['fields']) == 13 and all(field['writable'] for field in field_config['fields'])
    checks.append('three_record_kinds_current_available_configuration')
    record_id = 'cef72711-8874-4599-96bf-d69d48d1e9f0'
    record = checked(web, 'GET', f'/api/records/{record_id}').json()
    record_schema = json.loads((ROOT / 'backend/records/search.schema.json').read_text())
    Draft202012Validator({**record_schema, '$ref': '#/$defs/RecordView'}, format_checker=FormatChecker()).validate(record)
    assert record['kind'] == 'therapy' and record['family_confirmation']['confirmed_by_self'] is True
    checks.append('persistent_therapy_record_and_confirmation_reloaded_via_served_build')
    draft_payload = {'mode': 'SIMULATION', 'delivery_check': str(uuid.uuid4()),
        'values': {'observation_note': 'SIMULATION本地交付保存与重新读取检查'}}
    draft = checked(web, 'POST', f'/api/patients/{patient.id}/drafts', expected=201,
        json={'form_type': 'observation', 'payload': draft_payload}, headers=headers).json()
    reloaded = checked(web, 'GET', f'/api/drafts/{draft["draft_id"]}').json()
    persisted = Draft.objects.get(id=draft['draft_id'], patient=patient)
    assert reloaded['payload'] == persisted.payload == draft_payload and reloaded['version'] == '1'
    checked(web, 'POST', f'/api/drafts/{draft["draft_id"]}/abandon',
        json={'expected_version': 1}, headers=headers)
    persisted.refresh_from_db()
    assert persisted.status == 'abandoned'
    checked(web, 'GET', f'/api/drafts/{draft["draft_id"]}', expected=409)
    checks.append('actual_http_draft_database_save_reload_and_abandon')
    summaries = checked(web, 'GET', f'/api/patients/{patient.id}/summaries').json()
    assert isinstance(summaries, dict)
    checks.append('authorized_summary_listing')
    checked(web, 'POST', '/api/session/logout', json={}, headers=headers)
    checked(web, 'GET', '/api/session', expected=401)
    checks.append('logout_revokes_current_session')

report = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'checked_at': datetime.now(timezone.utc).isoformat(),
    'profile': 'LOCAL_PROCESS', 'web': args.web, 'api': args.api, 'checks': checks,
    'database': {'name': database, 'schema': schema, 'port': database_port, 'version': database_version},
    'python': platform.python_version(), 'django': django.get_version(),
    'migrations': sorted(f'{app}.{name}' for app, name in loader.applied_migrations),
    'configurations': configs, 'web_assets': assets, 'routes': routes,
    'record_id': record_id, 'record_version_id': record['record_version_id'], 'draft_id': draft['draft_id'],
    'redis_databases': [14, 15], 'queue': 'simulation-default', 'queue_prefix': 'sjia-simulation:',
    'worker': 'NOT_STARTED', 'beat': 'NOT_STARTED', 'external_model_calls': 0,
    'visual': 'NOT_RUN', 'clinical': 'NOT_RUN', 'real_device': 'NOT_RUN', 'wechat': 'NOT_RUN',
    'production_deployment': 'NOT_RUN', 'git_version': 'UNAVAILABLE_NON_GIT_WORKSPACE',
    'web_entry_scope': 'Actual built assets and HTTP business smoke; 39 SPA entries do not prove page acceptance'}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
assert json.loads(output.read_text()) == report
print(json.dumps({'mode': report['mode'], 'status': report['status'], 'checks': len(checks),
    'route_entries': len(routes), 'evidence': str(output)}, ensure_ascii=False))
