import argparse
from datetime import datetime, timezone
from http.cookies import SimpleCookie
import json
from pathlib import Path
import ssl
from urllib.parse import urlsplit

import django
import httpx

django.setup()

from django.conf import settings
from django.contrib.sessions.models import Session
from django.middleware.csrf import CSRF_SESSION_KEY
from family_content.models import EnvironmentGate
from identity.models import PrincipalCredential


parser = argparse.ArgumentParser(description='使用真实HTTPS验证独立S3环境的登录、CSRF、权限与会话退出')
parser.add_argument('--base-url', required=True)
parser.add_argument('--ca-file', required=True, type=Path)
parser.add_argument('--credentials', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
arguments = parser.parse_args()


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


require(not arguments.output.exists(), 'S3_RUNTIME_HTTP_REPORT_EXISTS')
url = urlsplit(arguments.base_url)
require(url.scheme == 'https' and url.hostname == '127.0.0.1' and url.port is not None
    and not any((url.username, url.password, url.query, url.fragment, url.path)), 'S3_RUNTIME_EXACT_LOCAL_HTTPS_REQUIRED')
credentials = json.loads(arguments.credentials.read_bytes())
principal = PrincipalCredential.objects.get(username=credentials['username']).principal
require(str(principal.id) == credentials['principal_id'], 'S3_RUNTIME_ACTUAL_SOFTWARE_ACCOUNT_REQUIRED')
gate = EnvironmentGate.objects.select_related('runtime_config').get(environment=settings.SJIA_ENVIRONMENT)
config_path = '/api/support/configs/' + str(gate.runtime_config_id)
cases = []


def checked(client, case, method, path, expected_status, *, code=None, **kwargs):
    response = client.request(method, path, **kwargs)
    require(response.status_code == expected_status, 'S3_RUNTIME_HTTP_STATUS_MISMATCH:' + case)
    if path.startswith('/api/'):
        require('no-store' in response.headers.get('Cache-Control', '')
            and 'private' in response.headers.get('Cache-Control', ''), 'S3_RUNTIME_PRIVATE_RESPONSE_REQUIRED:' + case)
    if code is not None:
        require(response.json()['code'] == code, 'S3_RUNTIME_HTTP_REASON_MISMATCH:' + case)
    cases.append({'case': case, 'method': method, 'path': path, 'http_status': response.status_code,
        'reason_code': code, 'trace_id': response.headers.get('X-Trace-ID')})
    return response


context = ssl.create_default_context(cafile=str(arguments.ca_file))
with httpx.Client(base_url=arguments.base_url, verify=context, trust_env=False, timeout=60) as client:
    readiness = checked(client, 'https_readiness', 'GET', '/internal/readiness', 200).json()
    require(readiness['status'] == 'ready' and readiness['runtime_admission'] == 'admitted_current'
        and readiness['loaded_config_version']['runtime_config_id'] == str(gate.runtime_config_id),
        'S3_RUNTIME_CURRENT_API_ADMISSION_REQUIRED')
    checked(client, 'anonymous_support_read', 'GET', '/api/v2/support/session', 401, code='AUTH_REQUIRED')
    checked(client, 'anonymous_configuration_read', 'GET', config_path, 401, code='AUTH_REQUIRED')
    login_payload = {'username': credentials['username'], 'password': credentials['password']}
    checked(client, 'login_without_csrf', 'POST', '/api/v2/support/session/login', 403, code='CSRF_FAILED',
        json=login_payload, headers={'Origin': arguments.base_url})
    bootstrap = checked(client, 'anonymous_csrf_bootstrap', 'GET', '/api/auth/bootstrap', 200).json()
    anonymous_key = client.cookies.get(settings.SESSION_COOKIE_NAME)
    anonymous = Session.objects.get(session_key=anonymous_key).get_decoded()
    require('_sjia_principal_id' not in anonymous and bool(anonymous[CSRF_SESSION_KEY]),
        'S3_RUNTIME_ANONYMOUS_SESSION_REQUIRED')
    headers = {'Origin': arguments.base_url, 'X-CSRFToken': bootstrap['csrf_token']}
    checked(client, 'invalid_password', 'POST', '/api/v2/support/session/login', 401, code='CREDENTIAL_INVALID',
        json={**login_payload, 'password': credentials['password'] + '-invalid'}, headers=headers)
    login_response = checked(client, 'support_password_login', 'POST', '/api/v2/support/session/login', 200,
        json=login_payload, headers=headers)
    login = login_response.json()
    require(login['principal_id'] == str(principal.id) and login['audience'] == 'support',
        'S3_RUNTIME_SUPPORT_LOGIN_SCOPE_REQUIRED')
    expected_capabilities = {'manage_configs', 'manage_config', 'approve_config', 'register_validation',
        'publish_config', 'manage_disposition'}
    require({item['capability_id'] for item in login['capabilities']} == expected_capabilities,
        'S3_RUNTIME_EXACT_SOFTWARE_CAPABILITIES_REQUIRED')
    cookies = SimpleCookie()
    for value in login_response.headers.get_list('set-cookie'):
        cookies.load(value)
    cookie = cookies[settings.SESSION_COOKIE_NAME]
    require(cookie['secure'] and cookie['httponly'] and cookie['path'] == '/' and not cookie['domain']
        and cookie['samesite'] == 'Lax' and settings.SESSION_COOKIE_NAME.startswith('__Host-'),
        'S3_RUNTIME_SECURE_SESSION_COOKIE_REQUIRED')
    session_key = client.cookies.get(settings.SESSION_COOKIE_NAME)
    persisted = Session.objects.get(session_key=session_key)
    decoded = persisted.get_decoded()
    require(session_key != anonymous_key and not Session.objects.filter(session_key=anonymous_key).exists()
        and decoded['_sjia_principal_id'] == str(principal.id) and decoded['_sjia_audience'] == 'support'
        and decoded[CSRF_SESSION_KEY] != anonymous[CSRF_SESSION_KEY], 'S3_RUNTIME_SESSION_AND_CSRF_ROTATION_REQUIRED')
    current_headers = {'Origin': arguments.base_url, 'X-CSRFToken': login['csrf_token']}
    checked(client, 'previous_csrf_rejected', 'POST', '/api/session/logout', 403, code='CSRF_FAILED', json={}, headers=headers)
    checked(client, 'unapproved_origin_rejected', 'POST', '/api/session/logout', 403, code='CSRF_FAILED', json={},
        headers={**current_headers, 'Origin': 'https://unapproved.invalid'})
    for index in range(2):
        session = checked(client, 'support_session_read_' + str(index + 1), 'GET', '/api/v2/support/session', 200).json()
        current = Session.objects.get(session_key=session_key)
        require(session['principal_id'] == str(principal.id) and session['audience'] == 'support'
            and current.session_data == persisted.session_data and current.expire_date == persisted.expire_date
            and datetime.fromisoformat(session['expires_at']) == current.expire_date,
            'S3_RUNTIME_PERSISTED_READONLY_SUPPORT_EXPIRY_REQUIRED')
    config = checked(client, 'authorized_runtime_configuration_read', 'GET', config_path, 200).json()
    require(config['id'] == str(gate.runtime_config_id) and config['checksum'] == gate.runtime_config.checksum
        and config['status'] == 'enabled' and config['config']['payload']['object_storage']['kind'] == 's3',
        'S3_RUNTIME_EXACT_S3_CONFIGURATION_READ_REQUIRED')
    checked(client, 'support_logout', 'POST', '/api/session/logout', 200, json={}, headers=current_headers)
    require(not Session.objects.filter(session_key=session_key).exists(), 'S3_RUNTIME_SUPPORT_SESSION_MUST_BE_REMOVED')
    checked(client, 'support_read_after_logout', 'GET', '/api/v2/support/session', 401, code='AUTH_REQUIRED')
    fresh = checked(client, 'family_csrf_bootstrap', 'GET', '/api/auth/bootstrap', 200).json()
    family = checked(client, 'explicit_family_audience_login', 'POST', '/api/auth/login', 200,
        json=login_payload, headers={'Origin': arguments.base_url, 'X-CSRFToken': fresh['csrf_token']}).json()
    require(family['audience'] == 'family', 'S3_RUNTIME_EXPLICIT_FAMILY_AUDIENCE_REQUIRED')
    family_key = client.cookies.get(settings.SESSION_COOKIE_NAME)
    checked(client, 'family_cannot_read_support_session', 'GET', '/api/v2/support/session', 403,
        code='SUPPORT_AUDIENCE_REQUIRED')
    checked(client, 'family_cannot_read_support_configuration', 'GET', config_path, 403,
        code='SUPPORT_AUDIENCE_REQUIRED')
    checked(client, 'family_logout', 'POST', '/api/session/logout', 200, json={},
        headers={'Origin': arguments.base_url, 'X-CSRFToken': family['csrf_token']})
    require(not Session.objects.filter(session_key=family_key).exists(), 'S3_RUNTIME_FAMILY_SESSION_MUST_BE_REMOVED')
    checked(client, 'final_anonymous_support_read', 'GET', '/api/v2/support/session', 401, code='AUTH_REQUIRED')
report = {'result': 'PASS', 'measured_at': datetime.now(timezone.utc).isoformat(), 'base_url': arguments.base_url,
    'environment': settings.SJIA_ENVIRONMENT, 'runtime_id': str(gate.runtime_config_id), 'tls_verified': True,
    'cases': cases, 'case_count': len(cases), 'persisted_expiry_verified': True,
    'session_rotation_verified': True, 'csrf_rotation_verified': True, 'secure_cookie_verified': True,
    'audience_separation_verified': True, 'owned_sessions_removed': True, 'new_model_calls': 0}
with arguments.output.open('x') as output:
    json.dump(report, output, indent=2)
    output.write('\n')
print(json.dumps({'result': 'PASS', 'cases': len(cases), 'tls_verified': True,
    'persisted_expiry_verified': True, 'audience_separation_verified': True, 'new_model_calls': 0}))
