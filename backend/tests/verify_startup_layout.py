"""Fresh PostgreSQL startup regression using an isolated, disposable test database.

Requires local development CREATEDB permission. Does not move production tables or
change search_path/security settings. No clinical fixtures or external calls.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')
import django

django.setup()
from django.conf import settings


def main():
    config = settings.DATABASES['default']
    if not settings.DEBUG or config['HOST'] not in ('127.0.0.1', 'localhost'):
        raise RuntimeError('This synthetic startup check requires a local development database')
    database = f'test_repair_startup_{uuid4().hex}'
    params = {'host': config['HOST'], 'port': config['PORT'], 'user': config['USER'],
        'password': config['PASSWORD'], 'dbname': 'postgres'}
    env = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings.development',
        'SJIA_DB_NAME': database, 'SJIA_DB_USER': config['USER'], 'SJIA_DB_PASSWORD': config['PASSWORD'],
        'SJIA_DB_HOST': config['HOST'], 'SJIA_DB_PORT': str(config['PORT']),
        'SJIA_ENVIRONMENT': 'test', 'SJIA_ARK_API_KEY': ''}
    created = False
    report = {'scope': 'SYNTHETIC_STARTUP_LAYOUT_ONLY', 'real_provider_calls': 0}
    try:
        with psycopg.connect(**params, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
        created = True
        for label in ('fresh_migration', 'new_process_second_migration'):
            result = subprocess.run([sys.executable, str(ROOT / 'manage.py'), 'migrate', '--noinput'],
                env=env, cwd=ROOT, text=True, capture_output=True)
            if result.returncode:
                raise RuntimeError(f'{label} failed:\n{result.stderr}')
            report[label] = 'PASS'
        check = '''import django; django.setup()
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.backends.db import SessionStore
assert ContentType.objects.count() > 0
session = SessionStore(); session['synthetic_startup'] = True; session.save()
assert SessionStore(session.session_key)['synthetic_startup'] is True
session.delete()
'''
        subprocess.run([sys.executable, '-c', check], env=env, cwd=ROOT, check=True)
        report['new_connection_session_and_content_type_read_write'] = 'PASS'
        with psycopg.connect(**{**params, 'dbname': database}, autocommit=True) as conn:
            # Build an existing-public-layout analogue from the fresh schema.
            # Keep sjia's originals untouched and verify public takes precedence.
            for table in ('django_content_type', 'django_session'):
                conn.execute(sql.SQL('CREATE TABLE public.{} (LIKE sjia.{} INCLUDING ALL)').format(
                    sql.Identifier(table), sql.Identifier(table)))
            conn.execute("INSERT INTO public.django_content_type(id, app_label, model) VALUES (100000, 'repair_layout', 'public_marker')")
            before = conn.execute('SELECT count(*) FROM sjia.django_session').fetchone()[0]
        public_check = '''import django; django.setup()
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.backends.db import SessionStore
from django.db import connection
assert ContentType.objects.count() == 1
assert ContentType.objects.get(app_label='repair_layout').model == 'public_marker'
session = SessionStore(); session['synthetic_public_layout'] = True; session.save()
with connection.cursor() as cursor:
    cursor.execute("SELECT n.nspname FROM django_session s JOIN pg_class c ON c.oid=s.tableoid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE session_key=%s", [session.session_key])
    assert cursor.fetchone()[0] == 'public'
assert SessionStore(session.session_key)['synthetic_public_layout'] is True
session.delete()
'''
        subprocess.run([sys.executable, '-c', public_check], env=env, cwd=ROOT, check=True)
        with psycopg.connect(**{**params, 'dbname': database}) as conn:
            assert conn.execute('SELECT count(*) FROM sjia.django_session').fetchone()[0] == before
        report['existing_public_layout_compatibility'] = 'PASS'
        report['moved_or_deleted_application_tables'] = False
    finally:
        if created:
            with psycopg.connect(**params, autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(database)))
    report['passed'] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
