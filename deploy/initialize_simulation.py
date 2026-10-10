import os
import secrets
import subprocess
import sys
from pathlib import Path

import django
import psycopg
from dotenv import set_key
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings.development'
django.setup()

from django.conf import settings


database = settings.DATABASES['default']
if database['HOST'] not in ('127.0.0.1', 'localhost'):
    raise RuntimeError('模拟数据库仅允许本地 PostgreSQL')
directory = ROOT / '.simulation-work'
for name in ('object-store', 'config-evidence', 'tmp'):
    (directory / name).mkdir(parents=True, exist_ok=True)
environment = directory / 'environment.env'
if not environment.exists():
    environment.touch(mode=0o600)
    set_key(str(environment), 'SJIA_SECRET_KEY', secrets.token_urlsafe(64))
    set_key(str(environment), 'SJIA_SIMULATION_NOTIFICATION_KEY', secrets.token_urlsafe(64))
    set_key(str(environment), 'SJIA_SIMULATION_PASSWORD', secrets.token_urlsafe(24))
    set_key(str(environment), 'SJIA_DEBUG', '1')
environment.chmod(0o600)
with psycopg.connect(dbname='postgres', user=database['USER'], password=database['PASSWORD'],
        host=database['HOST'], port=database['PORT'], autocommit=True) as connection:
    exists = connection.execute('SELECT 1 FROM pg_database WHERE datname = %s', ('sjia_simulation',)).fetchone()
    if exists is None:
        connection.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(
            sql.Identifier('sjia_simulation'), sql.Identifier(database['USER'])))
child_environment = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'config.settings.simulation',
    'TMPDIR': str(directory / 'tmp')}
subprocess.run([sys.executable, 'manage.py', 'migrate', '--noinput'], cwd=ROOT / 'backend',
    env=child_environment, check=True)
print('SIMULATION：独立数据库、文件目录、签名密钥和消息命名空间已初始化')
