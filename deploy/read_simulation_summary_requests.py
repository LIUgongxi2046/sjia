import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django
import httpx

django.setup()

fixture = json.loads((ROOT / '.simulation-work/t29-first-alert-business-fixture.json').read_text())
if fixture['mode'] != 'SIMULATION_ONLY':
    raise RuntimeError('资料必须属于明确模拟范围')
with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
    bootstrap = client.get('/api/auth/bootstrap')
    bootstrap.raise_for_status()
    login = client.post('/api/auth/login', json={'username': fixture['family_username'],
        'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
    login.raise_for_status()
    response = client.get(f"/api/patients/{fixture['patient_id']}/requests?operation_id=summary_create")
    response.raise_for_status()
    directory = response.json()
    exact = []
    for request in directory['requests']:
        detail = client.get(f"/api/requests/{request['intent_id']}")
        detail.raise_for_status()
        exact.append(detail.json())
result = {'mode': 'SIMULATION_ONLY', 'patient_id': fixture['patient_id'],
    'requests': directory, 'exact_requests': exact, 'business_writes': 0, 'saved_and_read': True}
output = ROOT / '.planning-work/coordination/root/current-summary-requests.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
if json.loads(output.read_text()) != result:
    raise RuntimeError('准确请求报告保存重读不一致')
print(json.dumps(result, ensure_ascii=False))
