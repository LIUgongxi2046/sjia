import json
import os
import sys
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django
import httpx

django.setup()

fixture = json.loads((ROOT / '.simulation-work/t29-first-alert-business-fixture.json').read_text())
if fixture['mode'] != 'SIMULATION_ONLY':
    raise RuntimeError('当前资料未标识模拟范围')
with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
    bootstrap = client.get('/api/auth/bootstrap')
    bootstrap.raise_for_status()
    login = client.post('/api/auth/login', json={'username': fixture['family_username'],
        'password': os.environ['SJIA_SIMULATION_PASSWORD']},
        headers={'X-CSRFToken': bootstrap.json()['csrf_token']})
    login.raise_for_status()
    started = monotonic()
    response = client.get(f"/api/patients/{fixture['patient_id']}/home")
    duration = monotonic() - started
    response.raise_for_status()
    home = response.json()
    if home['patient_id'] != fixture['patient_id'] or home['current_alert_id'] != 'a69b91f5-f5db-470b-981a-5ec740305111':
        raise RuntimeError('实际首页当前身份或预警不相符')
result = {'mode': 'SIMULATION_ONLY', 'status': 'PASS', 'patient_id': fixture['patient_id'],
    'http_status': response.status_code, 'duration_seconds': duration, 'home': home,
    'business_writes': 0, 'visual': 'NOT_RUN', 'saved_and_read': True}
output = ROOT / '.planning-work/coordination/root/current-home-response.json'
output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
if json.loads(output.read_text()) != result:
    raise RuntimeError('首页真实响应报告保存重读不一致')
print(json.dumps({'status': 'PASS', 'duration_seconds': duration, 'output': str(output.relative_to(ROOT))}))
