import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.request import build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar
from uuid import uuid4

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
django.setup()

from api.urls import urlpatterns

root = Path(__file__).resolve().parents[2]
started = datetime.now(timezone.utc).isoformat()
results = []
public_paths = {'auth/bootstrap', 'auth/start', 'auth/complete', 'auth/register', 'auth/login', 'session/logout'}
for route in urlpatterns:
    template = route.pattern._route
    if template in public_paths or template.startswith('integrations/'):
        continue
    path = template
    for name, converter in route.pattern.converters.items():
        value = str(uuid4()) if converter.__class__.__name__ == 'UUIDConverter' else 'unavailable'
        token = f'<uuid:{name}>' if converter.__class__.__name__ == 'UUIDConverter' else f'<str:{name}>'
        path = path.replace(token, value)
    methods = route.callback.cls.http_method_names
    method = next(method.upper() for method in ['get', 'post', 'put'] if method in methods)
    request = Request(f'http://127.0.0.1:8000/api/{path}', method=method, data=None if method == 'GET' else b'{}', headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=15) as response:
            status = response.status
            body = response.read()
            cache = response.headers.get('Cache-Control')
    except HTTPError as error:
        status = error.code
        body = error.read()
        cache = error.headers.get('Cache-Control')
    results.append({'route': template, 'method': method, 'expected_status': 401, 'actual_status': status, 'status': 'PASS' if status == 401 and cache and 'no-store' in cache else 'FAIL', 'cache_control': cache, 'response_sha256': hashlib.sha256(body).hexdigest()})
for path, expected in [('auth/login', 403), ('auth/register', 403), ('auth/start', 503), ('auth/complete', 503)]:
    request = Request(f'http://127.0.0.1:8000/api/{path}', method='POST', data=b'{}', headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=15) as response:
            status = response.status
            body = response.read()
    except HTTPError as error:
        status = error.code
        body = error.read()
    results.append({'route': path, 'method': 'POST', 'expected_status': expected, 'actual_status': status, 'status': 'PASS' if status == expected else 'FAIL', 'response_sha256': hashlib.sha256(body).hexdigest()})
opener = build_opener(HTTPCookieProcessor(CookieJar()))
with opener.open('http://127.0.0.1:8000/api/auth/bootstrap', timeout=15) as response:
    bootstrap = json.load(response)
for path, expected in [('auth/login', 401), ('auth/register', 400)]:
    request = Request(f'http://127.0.0.1:8000/api/{path}', method='POST', data=b'{}', headers={'Content-Type': 'application/json', 'X-CSRFToken': bootstrap['csrf_token']})
    try:
        with opener.open(request, timeout=15) as response:
            status = response.status
            body = response.read()
    except HTTPError as error:
        status = error.code
        body = error.read()
    results.append({'route': path, 'method': 'POST', 'scenario': 'valid_csrf_empty_credentials', 'expected_status': expected, 'actual_status': status, 'status': 'PASS' if status == expected else 'FAIL', 'response_sha256': hashlib.sha256(body).hexdigest()})
report = {'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(), 'evidence_type': 'SECURITY', 'input_source': 'PRD AC-01; anonymous requests without clinical data', 'results': results}
output = root / '.qa-work' / 'http-security.json'
output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'checks': len(results), 'passed': sum(result['status'] == 'PASS' for result in results), 'failures': [result for result in results if result['status'] == 'FAIL']}, ensure_ascii=False, indent=2))
raise SystemExit(int(any(result['status'] == 'FAIL' for result in results)))
