"""Local process/readiness probe, with no secret-bearing configuration or redirects."""
import json
import sys
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        return None


def main():
    kind = sys.argv[1] if len(sys.argv) == 2 else ''
    if kind not in ('liveness', 'readiness'):
        raise SystemExit('Choose liveness or readiness')
    # Host is provided by Django's configured allowed host list; no sensitive values.
    from django.conf import settings
    host = next((value for value in settings.ALLOWED_HOSTS if value and value != '*'), 'localhost')
    if host.startswith('.'):
        host = host[1:]
    request = Request(f'http://127.0.0.1:8000/internal/{kind}', headers={'Host': host})
    try:
        with build_opener(NoRedirect).open(request, timeout=3) as response:
            result = json.load(response)
        if result.get('status') != ('alive' if kind == 'liveness' else 'ready'):
            raise SystemExit(1)
    except Exception:
        # Do not print exception bodies or network/configuration details.
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
