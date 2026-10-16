"""Run and HTTP-check the local frontend/API with existing local PostgreSQL/Redis.

This is an offline engineering smoke test. It creates no patients, configuration
approvals or external model calls. --hold leaves the local processes running.
"""
import argparse
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def validate_runtime_health(liveness_status, liveness, readiness_status, health):
    """A missing runtime config is an honest 503, never fabricated local approval."""
    assert liveness_status == 200 and liveness.get('status') == 'alive'
    assert liveness.get('scope') == 'process_only'
    dependencies = health.get('dependencies', {})
    assert all(dependencies.get(name) is True for name in ('database', 'broker', 'schema'))
    assert set(dependencies) == {'database', 'broker', 'schema', 'configuration'}
    assert health.get('clinical_qualification') == 'NOT_VERIFIED_BY_HEALTH_PROBE'
    if dependencies['configuration'] is True:
        assert readiness_status == 200 and health.get('status') == 'ready'
        assert health.get('errors') == {}
    else:
        assert dependencies['configuration'] is False
        assert readiness_status == 503 and health.get('status') == 'not_ready'
        assert health.get('errors') == {'configuration': 'RUNTIME_CONFIGURATION_REQUIRED'}
    return {'api_liveness_http': liveness_status, 'api_readiness_http': readiness_status,
        'runtime_readiness': health['status'], 'runtime_dependencies': dependencies,
        'runtime_errors': health['errors'], 'clinical_qualification': health['clinical_qualification']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hold', action='store_true')
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT / 'backend'))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
    import django
    django.setup()
    from django.conf import settings
    if (not settings.DEBUG or settings.DATABASES['default']['HOST'] not in ('127.0.0.1', 'localhost')
            or settings.SJIA_ENVIRONMENT != 'test'):
        raise RuntimeError('HTTP smoke requires the isolated local test environment')
    log_dir = ROOT / '.qa-work' / 'repair'
    log_dir.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'SJIA_ARK_API_KEY': '', 'DJANGO_SETTINGS_MODULE': 'config.settings.development'}
    processes, logs = [], []
    synthetic_account = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(url):
        try:
            response = opener.open(url, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.read(), response.headers

    def post(path, payload, token):
        request = urllib.request.Request('http://127.0.0.1:5173' + path,
            data=json.dumps(payload).encode(), method='POST',
            headers={'Content-Type': 'application/json', 'X-CSRFToken': token})
        status, body, headers = get(request)
        return status, json.loads(body), headers

    def await_http(url):
        until = time.monotonic() + 30
        while time.monotonic() < until:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError('A local server exited; inspect local smoke logs')
            try:
                return get(url)
            except (urllib.error.URLError, TimeoutError):
                time.sleep(0.2)
        raise RuntimeError(f'Local server did not become available: {url}')

    try:
        for name, command, cwd in [
            ('api', [sys.executable, 'manage.py', 'runserver', '127.0.0.1:8000', '--noreload'], ROOT / 'backend'),
            ('web', ['npm', 'run', 'dev'], ROOT / 'guardian-h5'),
        ]:
            log = (log_dir / f'{name}-http.log').open('w')
            logs.append(log)
            processes.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT))
        live_status, live_body, _ = await_http('http://127.0.0.1:8000/internal/liveness')
        api_status, api_body, _ = get('http://127.0.0.1:8000/internal/readiness')
        health = json.loads(api_body)
        health_report = validate_runtime_health(live_status, json.loads(live_body), api_status, health)
        web_status, html, _ = await_http('http://127.0.0.1:5173/login')
        assert web_status == 200 and b'id="root"' in html
        status, bootstrap, headers = get('http://127.0.0.1:5173/api/auth/bootstrap')
        assert status == 200 and 'no-store' in headers.get('Cache-Control', '')
        status, session, _ = get('http://127.0.0.1:5173/api/session')
        assert status == 401 and json.loads(session)['code'] == 'AUTH_REQUIRED'
        synthetic_account = 'repair-smoke-' + uuid4().hex
        password = uuid4().hex + uuid4().hex
        status, registered, _ = post('/api/auth/register',
            {'username': synthetic_account, 'password': password}, json.loads(bootstrap)['csrf_token'])
        assert status == 201
        status, _, _ = post('/api/session/logout', {}, registered['csrf_token'])
        assert status == 200
        _, bootstrap, _ = get('http://127.0.0.1:5173/api/auth/bootstrap')
        status, logged_in, _ = post('/api/auth/login',
            {'username': synthetic_account, 'password': password}, json.loads(bootstrap)['csrf_token'])
        assert status == 200 and logged_in['principal_id'] == registered['principal_id']
        status, session, _ = get('http://127.0.0.1:5173/api/session')
        assert status == 200 and json.loads(session)['principal_id'] == registered['principal_id']
        post('/api/session/logout', {}, logged_in['csrf_token'])
        report = {**health_report, 'scope': 'LOCAL_HTTP_ENGINEERING_SMOKE', 'frontend_login_http': 200,
            'api_database_redis_health_http': api_status, 'frontend_api_proxy_bootstrap_http': 200,
            'anonymous_session_is_denied': True, 'real_provider_calls': 0,
            'synthetic_register_login_session_http': [201, 200, 200],
            'clinical_configuration_approved': False, 'patient_data_created': False,
            'browser_visual_verification': 'NOT_RUN', 'passed': True}
        (log_dir / 'local-http-report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2), flush=True)
        if args.hold:
            print('Local processes are running in this execution session; Ctrl-C stops them.', flush=True)
            while all(process.poll() is None for process in processes):
                time.sleep(2)
            raise RuntimeError('A held local server exited unexpectedly')
    finally:
        if synthetic_account is not None:
            cleanup = '''import django; django.setup()
from identity.models import PrincipalCredential, PrincipalIdentity, Principal
from django.db import transaction
import sys
with transaction.atomic():
    ids = list(PrincipalCredential.objects.filter(username=sys.argv[1]).values_list('principal_id', flat=True))
    PrincipalCredential.objects.filter(principal_id__in=ids).delete()
    PrincipalIdentity.objects.filter(principal_id__in=ids).delete()
    Principal.objects.filter(id__in=ids).delete()
'''
            cleanup_result = subprocess.run([sys.executable, '-c', cleanup, synthetic_account],
                env=environment, cwd=ROOT / 'backend')
            if cleanup_result.returncode:
                print('Synthetic account cleanup failed; inspect the local test database.', file=sys.stderr)
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    main()
