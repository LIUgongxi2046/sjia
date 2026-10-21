import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
from urllib.parse import urlsplit

import django
import httpx

django.setup()

from django.conf import settings
from django.contrib.sessions.models import Session
from identity.models import PrincipalCredential


parser = argparse.ArgumentParser(description='通过真实HTTPS和Session记录核验支持端会话期限')
parser.add_argument('--base-url', required=True)
parser.add_argument('--ca-file', required=True, type=Path)
parser.add_argument('--credentials', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
arguments = parser.parse_args()
if arguments.output.exists():
    raise RuntimeError('SUPPORT_SESSION_EXPIRY_REPORT_EXISTS')
url = urlsplit(arguments.base_url)
if (url.scheme != 'https' or url.hostname != '127.0.0.1' or url.port is None
        or url.username or url.password or url.query or url.fragment or url.path):
    raise RuntimeError('SUPPORT_SESSION_EXPLICIT_LOCAL_HTTPS_REQUIRED')
credentials = json.loads(arguments.credentials.read_bytes())
principal = PrincipalCredential.objects.get(username=credentials['username']).principal
if str(principal.id) != credentials['principal_id']:
    raise RuntimeError('SUPPORT_SESSION_ACTUAL_ACCOUNT_REQUIRED')
context = ssl.create_default_context(cafile=str(arguments.ca_file))
observations = []
with httpx.Client(base_url=arguments.base_url, verify=context, trust_env=False, timeout=60) as client:
    bootstrap = client.get('/api/auth/bootstrap')
    bootstrap.raise_for_status()
    login = client.post('/api/v2/support/session/login', json={
        'username': credentials['username'], 'password': credentials['password']},
        headers={'Origin': arguments.base_url, 'X-CSRFToken': bootstrap.json()['csrf_token']})
    login.raise_for_status()
    if login.json()['principal_id'] != str(principal.id) or login.json()['audience'] != 'support':
        raise RuntimeError('SUPPORT_SESSION_LOGIN_SCOPE_MISMATCH')
    session_key = client.cookies.get(settings.SESSION_COOKIE_NAME)
    before = Session.objects.get(session_key=session_key)
    for path in ('/api/v2/support/session', '/api/v2/support/session', '/api/session'):
        response = client.get(path)
        response.raise_for_status()
        current = Session.objects.get(session_key=session_key)
        payload = response.json()
        if payload['principal_id'] != str(principal.id) or payload['audience'] != 'support':
            raise RuntimeError('SUPPORT_SESSION_READ_SCOPE_MISMATCH')
        reported = datetime.fromisoformat(payload['expires_at'])
        observations.append({'path': path, 'http_status': response.status_code,
            'reported_expiry': reported.isoformat(), 'persisted_expiry': current.expire_date.isoformat(),
            'persisted_row_unchanged': current.session_data == before.session_data and current.expire_date == before.expire_date,
            'reported_after_persisted_expiry': reported > current.expire_date,
            'trace_id': response.headers['X-Trace-ID']})
    logout = client.post('/api/session/logout', json={}, headers={'Origin': arguments.base_url,
        'X-CSRFToken': login.json()['csrf_token']})
    logout.raise_for_status()
    if Session.objects.filter(session_key=session_key).exists():
        raise RuntimeError('SUPPORT_SESSION_LOGOUT_MUST_REMOVE_OWN_SESSION')
    after_logout = client.get('/api/v2/support/session')
    if after_logout.status_code != 401 or after_logout.json()['code'] != 'AUTH_REQUIRED':
        raise RuntimeError('SUPPORT_SESSION_LOGOUT_MUST_REJECT_SUBSEQUENT_READ')
valid = all(item['persisted_row_unchanged'] and not item['reported_after_persisted_expiry'] for item in observations)
root = Path(__file__).resolve().parents[2]
report = {'result': 'PASS' if valid else 'FAIL', 'measured_at': datetime.now(timezone.utc).isoformat(),
    'base_url': arguments.base_url, 'tls_verified': True, 'observations': observations,
    'logout_verified': True, 'new_model_calls': 0,
    'implementation_sha256': hashlib.sha256((root / 'backend/api/views_support_session.py').read_bytes()).hexdigest()}
with arguments.output.open('x') as output:
    json.dump(report, output, indent=2)
    output.write('\n')
print(json.dumps(report))
if not valid:
    raise AssertionError('SUPPORT_SESSION_EXPIRY_MUST_NOT_EXCEED_PERSISTED_SESSION')
