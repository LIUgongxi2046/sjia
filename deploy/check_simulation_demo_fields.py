import hashlib
import json
import os
from pathlib import Path

from prepare_simulation_voice import guard
import httpx
from django.utils import timezone

from authorization.policy import matching_case_grants
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from core.management.commands.seed_simulation import account
from identity.models import Patient

ROOT = Path(__file__).resolve().parents[1]
guard()
actor = account('simulation-family')
patient = Patient.objects.get(id='e88faef5-8bf6-4256-b5a5-865c699103d6')
configurations = []
for config in ConfigVersion.objects.filter(kind='content', schema_version='field-config-1',
        payload__kind='observation', status='enabled'):
    configurations.append({'config_id': str(config.id), 'input_context': config.payload.get('input_context'),
        'reason_code': configuration_qualification_reason(config, 'simulation'),
        'fields': [field['field_code'] for field in config.payload['fields']]})
scope = {}
for action in ('read_raw', 'write_record'):
    grants = list(matching_case_grants(actor, patient, action, timezone.now(),
        author_id=actor.id, require_relation=True, purpose='family_observation'))
    scope[action] = sorted({code for grant in grants for code in grant.field_codes})
with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
    bootstrap = client.get('/api/auth/bootstrap')
    bootstrap.raise_for_status()
    client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
    login = client.post('/api/auth/login', json={'username': 'simulation-family',
        'password': os.environ['SJIA_SIMULATION_PASSWORD']})
    login.raise_for_status()
    client.headers['X-CSRFToken'] = login.json()['csrf_token']
    response = client.get(f'/api/patients/{patient.id}/field-config', params={'kind': 'observation'})
    body = response.json()
    value = {'mode': 'SIMULATION_ONLY', 'patient_id': str(patient.id), 'username': 'simulation-family',
        'configurations': configurations, 'scope': scope, 'http_status': response.status_code, 'body': body}
    target = ROOT / '.planning-work/coordination/root/demo-fields-current.json'
    data = json.dumps(value, ensure_ascii=False, indent=2).encode()
    target.write_bytes(data)
    if target.read_bytes() != data:
        raise RuntimeError('模拟演示字段诊断保存重读不一致')
    response.raise_for_status()
    print(json.dumps({'http_status': response.status_code, 'state': body['state'],
        'reason_code': body['reason_code'], 'config_version': body['config_version'], 'scope': scope,
        'configurations': configurations, 'report_sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
