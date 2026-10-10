import hashlib
import json
import os
from pathlib import Path

import django

os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.qa'
os.environ['SJIA_ARK_REQUEST_TIMEOUT_SECONDS'] = '2'
django.setup()

from django.test import Client
from files.models import FileObject

root = Path(__file__).resolve().parents[2] / '.qa-work/QA04'
checksum = hashlib.sha256((root / 'report.png').read_bytes()).hexdigest()
file = FileObject.objects.filter(checksum=checksum, status='available').first()
assert file is not None
client = Client(HTTP_HOST='127.0.0.1')
login = client.post('/api/auth/login', {'username': 'acceptance-family', 'password': 'Local-accept-2026'}, content_type='application/json')
assert login.status_code == 200, login.content
response = client.post('/api/recognition/report-ocr', {'file_id': str(file.id)}, content_type='application/json')
body = response.json()
(root / 'timeout.json').write_text(json.dumps({'provider': 'actual Ark', 'configured_timeout_seconds': 2, 'status': response.status_code, 'body': body}, ensure_ascii=False, indent=2))
assert response.status_code == 504, body
assert body['code'] == 'UPSTREAM_TIMEOUT'
assert body['retryable'] is True
print('实际 Ark 请求在2秒期限内超时，接口返回504和明确重试提示。')
