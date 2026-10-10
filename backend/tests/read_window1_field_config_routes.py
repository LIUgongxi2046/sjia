import hashlib
import json
import os
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / '.planning-work' / 'parallel' / 'window1' / 'field-config-routes-20261009.json'
PATIENT_ID = 'e88faef5-8bf6-4256-b5a5-865c699103d6'
ORIGINS = {
    'web_5183': 'http://127.0.0.1:5183',
    'web_5184': 'http://127.0.0.1:5184',
    'api_8001': 'http://127.0.0.1:8001',
}

results = []
for name, origin in ORIGINS.items():
    with httpx.Client(base_url=origin, timeout=30, trust_env=False) as client:
        bootstrap_response = client.get('/api/auth/bootstrap')
        bootstrap = bootstrap_response.json()
        login_response = client.post('/api/auth/login', json={
            'username': 'simulation-family',
            'password': os.environ['SJIA_SIMULATION_PASSWORD'],
        }, headers={'X-CSRFToken': bootstrap['csrf_token']})
        config_response = client.get(
            f'/api/patients/{PATIENT_ID}/field-config',
            params={'kind': 'observation'},
        )
        config = config_response.json()
        result = {
            'name': name,
            'origin': origin,
            'bootstrap_status': bootstrap_response.status_code,
            'login_status': login_response.status_code,
            'audience': login_response.json().get('audience') if login_response.is_success else None,
            'field_config_status': config_response.status_code,
            'patient_id': config.get('patient_id'),
            'kind': config.get('kind'),
            'state': config.get('state'),
            'reason_code': config.get('reason_code'),
            'config_version': config.get('config_version'),
            'checksum': config.get('checksum'),
            'writable_fields': [item['field_code'] for item in config.get('fields', []) if item.get('writable')],
            'response_sha256': hashlib.sha256(config_response.content).hexdigest(),
        }
        assert result['login_status'] == 200 and result['audience'] == 'family', result
        assert result['field_config_status'] == 200, result
        assert result['patient_id'] == PATIENT_ID and result['kind'] == 'observation', result
        results.append(result)

report = {
    'task_id': 'T60',
    'mode': 'SIMULATION_ONLY_READ_ONLY',
    'database_scope': 'sjia_simulation',
    'status': 'PASS',
    'results': results,
}
OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
saved = json.loads(OUTPUT.read_text(encoding='utf-8'))
assert saved == report
print(json.dumps({'task_id': report['task_id'], 'status': report['status'], 'results': results,
    'saved_and_read': True, 'output': str(OUTPUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
