import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
from urllib.parse import urlsplit
from uuid import UUID

import django
import httpx

django.setup()

from identity.models import PrincipalCredential
from retention.models import DispositionItem, DispositionRequest
from tasks.models import OutboxEvent, Task


parser = argparse.ArgumentParser(description='通过真实HTTPS及已保存的Outbox事件核验处置任务引用')
parser.add_argument('--base-url', required=True)
parser.add_argument('--ca-file', required=True, type=Path)
parser.add_argument('--family-credentials', required=True, type=Path)
parser.add_argument('--support-credentials', required=True, type=Path)
parser.add_argument('--disposition-id', required=True, type=UUID)
parser.add_argument('--output', required=True, type=Path)
arguments = parser.parse_args()
os.umask(0o077)


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


require(not arguments.output.exists(), 'DISPOSITION_READBACK_REPORT_EXISTS')
url = urlsplit(arguments.base_url)
require(url.scheme == 'https' and url.hostname == '127.0.0.1' and url.port is not None
    and not any((url.username, url.password, url.path, url.query, url.fragment)),
    'DISPOSITION_READBACK_EXACT_LOCAL_HTTPS_REQUIRED')
row = DispositionRequest.objects.get(pk=arguments.disposition_id)
events = OutboxEvent.objects.filter(aggregate_id=row.id, event_type__in=(
    'disposition.local_requested', 'disposition.s3_requested', 'disposition.provider_requested'))
tasks = Task.objects.filter(id__in=events.values('task_id')).order_by('created_at', 'id')
require(tasks.exists() and not tasks.exclude(patient_id=row.patient_id, task_type='disposition').exists(),
    'DISPOSITION_READBACK_ACTUAL_PATIENT_TASK_EVENTS_REQUIRED')
expected_items = [{'item_id': str(item.id), 'target_type': item.target_type, 'target_id': item.target_id,
    'target_version': item.target_version, 'status': item.status, 'reason': item.reason,
    'receipt_sha256': item.receipt_sha256} for item in DispositionItem.objects.filter(request=row)
    .order_by('target_type', 'target_id', 'target_version')]
context = ssl.create_default_context(cafile=str(arguments.ca_file))


@contextmanager
def authenticated(path, audience):
    credentials = json.loads(path.read_bytes())
    principal = PrincipalCredential.objects.get(username=credentials['username']).principal
    require(str(principal.id) == credentials['principal_id'], 'DISPOSITION_READBACK_ACTUAL_ACCOUNT_REQUIRED')
    with httpx.Client(base_url=arguments.base_url, verify=context, trust_env=False, timeout=60) as client:
        bootstrap = client.get('/api/auth/bootstrap')
        bootstrap.raise_for_status()
        login_path = '/api/auth/login' if audience == 'family' else '/api/v2/support/session/login'
        response = client.post(login_path, json={'username': credentials['username'], 'password': credentials['password']},
            headers={'Origin': arguments.base_url, 'X-CSRFToken': bootstrap.json()['csrf_token']})
        response.raise_for_status()
        login = response.json()
        require(login['principal_id'] == str(principal.id) and login['audience'] == audience,
            'DISPOSITION_READBACK_AUTHENTICATED_AUDIENCE_REQUIRED')
        try:
            yield client, principal
        finally:
            logout = client.post('/api/session/logout', json={}, headers={
                'Origin': arguments.base_url, 'X-CSRFToken': login['csrf_token']})
            logout.raise_for_status()


observations = []
for audience, credentials, endpoint in (
        ('family', arguments.family_credentials, '/api/v2/dispositions/'),
        ('support', arguments.support_credentials, '/api/v2/support/dispositions/')):
    with authenticated(credentials, audience) as (client, principal):
        if audience == 'family':
            require(row.requested_by_id == principal.id, 'DISPOSITION_READBACK_REQUEST_OWNER_REQUIRED')
        expected_tasks = tasks if audience == 'family' else tasks.filter(actor=principal)
        expected_ids = [str(task.id) for task in expected_tasks]
        response = client.get(endpoint + str(row.id))
        response.raise_for_status()
        require('no-store' in response.headers.get('Cache-Control', ''), 'DISPOSITION_READBACK_PRIVATE_RESPONSE_REQUIRED')
        payload = response.json()
        actual_items = [{name: item[name] for name in expected_items[0]} for item in payload['items']] if expected_items else []
        valid = (payload['task_ids'] == expected_ids and payload['status'] == row.status
            and payload['patient_id'] == str(row.patient_id) and actual_items == expected_items
            and len(payload['items']) == len(expected_items))
        observations.append({'audience': audience, 'expected_task_ids_from_outbox': expected_ids,
            'actual_task_ids': payload['task_ids'], 'request_status': payload['status'],
            'items_match_saved_receipts': actual_items == expected_items, 'result': 'PASS' if valid else 'FAIL',
            'trace_id': response.headers.get('X-Trace-ID')})
valid = all(item['result'] == 'PASS' for item in observations)
report = {'result': 'PASS' if valid else 'FAIL', 'measured_at': datetime.now(timezone.utc).isoformat(),
    'disposition_id': str(row.id), 'tls_verified': True, 'expectation_source': 'persisted_outbox_events',
    'task_schemas': sorted(set(tasks.values_list('schema_version', flat=True))),
    'audiences': observations, 'support_actor_filter_verified': True, 'new_model_calls': 0}
with arguments.output.open('x') as output:
    json.dump(report, output, indent=2)
    output.write('\n')
print(json.dumps({'result': report['result'], 'audiences': len(observations),
    'tasks_from_outbox': tasks.count(), 'tls_verified': True, 'new_model_calls': 0}))
require(valid, 'DISPOSITION_READBACK_MUST_MATCH_ACTUAL_TASKS_AND_RECEIPTS')
