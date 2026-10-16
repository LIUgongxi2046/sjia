"""Validate packaged runtime assets and imports, without a database or provider call.

Run in the image or from a tree staged with the Dockerfile COPY instructions:
    DJANGO_SETTINGS_MODULE=config.settings.development python deploy/smoke_runtime.py
"""
import importlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
# Runtime readers use this repository-relative layout, including cross-file $refs.
REQUIRED_SCHEMAS = (
    'backend-design/protocol.schema.json',
    'backend-design/runtime.schema.json',
    'backend-design/v2-protocol.schema.json',
    'backend-design/archive-delta.schema.json',
    'backend-design/observation-period.schema.json',
    'backend-design/archive-relations.schema.json',
    'backend-design/archive-search.schema.json',
    'backend-design/personal-record-task.schema.json',
    'backend-design/material-reminder-notice.schema.json',
    'backend-design/notification.schema.json',
    'data-design/dataset.schema.json',
    'data-design/case-extension.schema.json',
    'alert-design/policy.schema.json',
    'alert-design/node.schema.json',
    'alert-design/v2-recognition.schema.json',
    'backend/archives/quota.schema.json',
    'backend/records/search.schema.json',
)


def main():
    sys.path.insert(0, str(ROOT / 'backend'))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
    from jsonschema import Draft202012Validator

    for name in REQUIRED_SCHEMAS:
        Draft202012Validator.check_schema(json.loads((ROOT / name).read_text()))
    assert (ROOT / 'data-design/v2-schema.sql').is_file(), 'Missing migration SQL'
    with patch('django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection',
            side_effect=AssertionError('Runtime import smoke must not connect to a database')):
        import django

        django.setup()
        # This build-time probe verifies packaging, not serving admission. A
        # real WSGI import must query the durable restore marker and fail closed
        # without a migrated database. Isolate that boundary only in this probe
        # while asserting WSGI still invokes it; there is no runtime bypass flag.
        with patch('config.runtime_admission.admit_configured_process') as admission:
            importlib.import_module('config.wsgi')
        admission.assert_called_once_with('api')
        urls = importlib.import_module('config.urls')
        assert urls.urlpatterns, 'No API URL patterns loaded'
        from config.celery import app

        app.loader.import_default_modules()
    print(f'Runtime smoke passed (import-only; not production readiness): {len(REQUIRED_SCHEMAS)} required schemas, migration SQL, WSGI, API URLs and worker imports.')


if __name__ == '__main__':
    main()
