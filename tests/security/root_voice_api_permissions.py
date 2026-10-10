import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.simulation')

import django
import httpx

django.setup()

from django.db import connection
from django.utils import timezone
from core.management.commands.seed_simulation import require_simulation

require_simulation()
with connection.cursor() as cursor:
    cursor.execute('SELECT current_database(), current_schema(), inet_server_port()')
    if cursor.fetchone() != ('sjia_simulation', 'sjia', 5433):
        raise RuntimeError('语音权限检查仅允许指定模拟数据库')
fixture = json.loads((ROOT / '.simulation-work/t55-voice-fixture.json').read_text())
created = json.loads((ROOT / '.planning-work/parallel/window3/T55-voice-created-task.json').read_text())
assertion = unittest.TestCase()
checks = []
paths = [f'/api/v2/patients/{fixture["patient_id"]}/voice-input-options',
    f'/api/v2/patients/{fixture["patient_id"]}/voice-inputs/{created["voice_input_id"]}',
    f'/api/v2/patients/{fixture["patient_id"]}/voice-inputs?request_id={created["request_id"]}']


def checked_read(client, path, expected):
    response = client.get(path)
    assertion.assertEqual(response.status_code, expected, response.text[:300])
    assertion.assertIn('no-store', response.headers['Cache-Control'])
    checks.append({'path': path, 'http_status': response.status_code, 'body': response.json(),
        'body_sha256': hashlib.sha256(response.content).hexdigest()})


with httpx.Client(base_url='http://127.0.0.1:8001', timeout=30, trust_env=False) as client:
    for path in paths:
        checked_read(client, path, 401)
    bootstrap = client.get('/api/auth/bootstrap')
    bootstrap.raise_for_status()
    client.headers['X-CSRFToken'] = bootstrap.json()['csrf_token']
    login = client.post('/api/auth/login', json={'username': 'simulation-family',
        'password': os.environ['SJIA_SIMULATION_PASSWORD']})
    login.raise_for_status()
    client.headers['X-CSRFToken'] = login.json()['csrf_token']
    for path in paths:
        checked_read(client, path, 403)
report = {'status': 'PASS', 'mode': 'SIMULATION_ONLY', 'checked_at': timezone.now().isoformat(),
    'checks': checks, 'anonymous_and_other_family_denied': True, 'business_writes': 0,
    'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'visual': 'NOT_RUN'}
target = ROOT / '.planning-work/coordination/root/voice-api-permissions.json'
data = json.dumps(report, ensure_ascii=False, indent=2).encode()
target.write_bytes(data)
if target.read_bytes() != data:
    raise RuntimeError('语音权限检查报告保存重读不一致')
print(json.dumps({'status': 'PASS', 'checks': len(checks),
    'report_sha256': hashlib.sha256(data).hexdigest()}, ensure_ascii=False))
