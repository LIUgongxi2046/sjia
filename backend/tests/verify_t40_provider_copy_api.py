import json
import os
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.simulation'

import django

django.setup()

from django.conf import settings
from django.test import Client
from django.urls import resolve

from archives.models import PurposeCopy, PurposeCopyReview
from requests.models import WriteIntent
from requests.service import compute_digest, fail_intent
from retention.models import SimulatedProviderCopy


if settings.DATABASES['default']['NAME'] != 'sjia_simulation':
    raise RuntimeError('T40接口检查仅允许sjia_simulation')

intent = WriteIntent.objects.get(id=UUID('00ac7516-2096-4148-b791-b011faac84d6'),
    request_id=UUID('00b41905-7682-4741-8c02-099e5b733ccd'), operation_id='simulation_provider_copy_create')
copy_id = resolve('/api/v2' + intent.target_key).kwargs['copy_id']
copy = PurposeCopy.objects.get(id=copy_id, status='approved', environment='simulation')
matching_review = None
for candidate_review in PurposeCopyReview.objects.filter(copy_id=copy.id, copy_version=copy.version,
        revoked_at__isnull=True).order_by('-reviewed_at', '-id'):
    candidate_payload = {'expected_version': str(copy.version), 'review_id': str(candidate_review.id)}
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', candidate_payload) == intent.payload_sha256:
        matching_review = candidate_review
        break
if matching_review is None:
    raise RuntimeError('没有模拟用途副本复核记录')
payload = {'expected_version': str(copy.version), 'review_id': str(matching_review.id)}
client = Client()
login = client.post('/api/v2/support/session/login', data=json.dumps({'username': 'simulation-support',
    'password': os.environ['SJIA_SIMULATION_PASSWORD']}), content_type='application/json', secure=True)
if login.status_code != 200 or login.json()['audience'] != 'support':
    raise RuntimeError('模拟支持账号登录接口失败')

saved_response = client.post(f'/api/v2{intent.target_key}', data=json.dumps(payload),
    content_type='application/json', secure=True, HTTP_X_CSRFTOKEN=login.json()['csrf_token'],
    HTTP_X_WRITE_INTENT=str(intent.id), HTTP_IDEMPOTENCY_KEY=str(intent.request_id),
    HTTP_X_INTENT_GENERATION=str(intent.generation))
if (saved_response.status_code != 422 or saved_response.json()['code'] != 'EXTERNAL_COPY_REVIEW_REQUIRED'):
    raise RuntimeError(f'当前复核已失效时副本登记未被拒绝：{saved_response.status_code} {saved_response.content!r}')
intent.refresh_from_db()
fail_intent(intent=intent, error_code='EXTERNAL_COPY_REVIEW_REQUIRED')
request_response = client.get(f'/api/v2/requests/{intent.id}', secure=True)
if request_response.status_code != 200 or request_response.json()['state'] != 'failed':
    raise RuntimeError('失败的模拟副本登记意图未能重新读取')
if SimulatedProviderCopy.objects.filter(purpose_copy=copy).exists():
    raise RuntimeError('过期复核副本生成了模拟提供方对象')

report = {'mode': 'SIMULATION', 'database': settings.DATABASES['default']['NAME'], 'status': 'PASS',
    'checks': ['支持账号通过实际API会话登录', '失效的人工复核阻止提供方副本登记',
        '拒绝后准确请求状态保存为failed并经API重读', '拒绝路径没有创建模拟提供方副本'],
    'request_id': str(intent.request_id), 'task': 'POSITIVE_PROVIDER_COPY_CREATION_NOT_RUN',
    'reason': 'NO_CURRENT_DISTINCT_REVIEW', 'external_provider': 'NOT_RUN'}
output = ROOT / '.planning-work/main-window/t40-provider-copy-api.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
output.chmod(0o600)
print(json.dumps(report, ensure_ascii=False, indent=2))
