import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'

import django

django.setup()

from django.conf import settings
from django.test import Client
from identity.models import PrincipalCredential
from tasks.models import Task


if settings.DATABASES['default']['NAME'] != 'sjia_simulation':
    raise RuntimeError('T40 task API check requires sjia_simulation')

actor = PrincipalCredential.objects.get(username='simulation-support').principal
tasks = Task.objects.filter(actor=actor, task_type=Task.TaskType.DISPOSITION,
    schema_version='simulation-provider-deletion-1', status=Task.Status.COMPLETED).order_by('-created_at')
task = next((item for item in tasks if item.input_refs.get('mode') == 'SIMULATION'
    and item.input_refs.get('disposition_ref') is None), None)
if task is None:
    raise RuntimeError('No completed standalone simulation deletion task exists')

schema = json.loads((ROOT / 'backend-design/v2-protocol.schema.json').read_text())
required = set(schema['$defs']['TaskView']['required'])
client = Client()
login = client.post('/api/v2/support/session/login', data=json.dumps({'username': 'simulation-support',
    'password': os.environ['SJIA_SIMULATION_PASSWORD']}), content_type='application/json', secure=True)
if login.status_code != 200 or login.json()['audience'] != 'support':
    raise RuntimeError('Simulation support login failed')

first = client.get(f'/api/v2/tasks/{task.id}', secure=True)
second = client.get(f'/api/v2/tasks/{task.id}', secure=True)
if first.status_code != 200 or second.status_code != 200:
    raise RuntimeError(f'Task API read failed: {first.status_code} {first.content!r}; {second.status_code} {second.content!r}')
first_payload = first.json()
if first_payload != second.json():
    raise RuntimeError('Repeated task API reads differ')
if not required <= first_payload.keys():
    raise RuntimeError(f'Task API response is missing schema fields: {required - first_payload.keys()}')
if (first_payload['task_id'] != str(task.id) or first_payload['status'] != 'completed'
        or first_payload['task_type'] != 'disposition' or first_payload['result_type'] is not None
        or first_payload['result_ref'] is not None or first_payload['error_code'] is not None):
    raise RuntimeError('Task API response does not match the persisted standalone task')

report = {'mode': 'SIMULATION', 'database': settings.DATABASES['default']['NAME'],
    'status': 'PASS', 'task_id': str(task.id), 'api_reads': [first.status_code, second.status_code],
    'stable_payload': True, 'provider_state': 'deleted', 'task_view': first_payload}
report_path = ROOT / '.planning-work/main-window/t40-task-api-read.json'
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
report_path.chmod(0o600)
saved_report = json.loads(report_path.read_text())
if saved_report != report:
    raise RuntimeError('Task API read report did not persist accurately')
print(json.dumps({'mode': report['mode'], 'database': report['database'], 'status': report['status'],
    'task_id': report['task_id'], 'api_reads': report['api_reads'], 'stable_payload': report['stable_payload'],
    'provider_state': report['provider_state'], 'report_path': str(report_path)}, ensure_ascii=False, indent=2))
