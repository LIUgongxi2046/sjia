import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from urllib.parse import urljoin
from urllib.request import urlopen


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument('--runtime-source', type=Path, required=True)
    arguments.add_argument('--settings-module', required=True)
    arguments.add_argument('--pythonpath', action='append', type=Path, default=[])
    arguments.add_argument('--api-url')
    arguments.add_argument('--frontend-url')
    options = arguments.parse_args()
    root = options.runtime_source.resolve(strict=True)
    sys.path[:0] = [str(root / 'backend'), *(str(path.resolve(strict=True)) for path in options.pythonpath)]
    os.environ['DJANGO_SETTINGS_MODULE'] = options.settings_module
    import django

    django.setup()

    from django.conf import settings
    from django.db import connection, transaction
    from django.db.migrations.loader import MigrationLoader
    from configurations.evidence import backend_artifact_manifest
    from configurations.service import compute_checksum
    from family_content.models import EnvironmentGate
    import redis

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
            cursor.execute('SELECT version(), current_database(), current_user, inet_server_port()')
            version, name, user, port = cursor.fetchone()
            inventory = {}
            for label, table in [('records', 'record'), ('record_versions', 'record_version'),
                ('files', 'file_object'), ('migrations', None)]:
                cursor.execute('SELECT count(*) FROM public.django_migrations' if table is None
                    else 'SELECT count(*) FROM sjia.' + table)
                inventory[label] = cursor.fetchone()[0]
        loader = MigrationLoader(connection)
        pending = sorted(set(loader.disk_migrations) - set(loader.applied_migrations))
        require(not pending, f'明确源码对应的数据库仍有待执行迁移：{pending}')
        inventory['pending_migrations'] = pending
        gate_identity = None
        if options.api_url:
            gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
            gate_identity = {'environment': gate.environment, 'gate_version': gate.version,
                'publication_epoch': gate.publication_epoch,
                'runtime_config_id': str(gate.runtime_config_id) if gate.runtime_config_id else None}
    report = {'python': platform.python_version(), 'django': django.get_version(),
        'celery': importlib.metadata.version('celery'), 'source_root': str(root),
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'backend_artifact_sha256': compute_checksum(backend_artifact_manifest()),
        'database': {'version': version, 'name': name, 'user': user, 'port': port},
        'inventory': inventory, 'environment_gate': gate_identity, 'read_only': True}
    require(bool(options.api_url) == bool(options.frontend_url), 'API和前端地址需要共同提供')
    if options.api_url:
        broker = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3)
        require(broker.ping(), 'Redis读取失败')
        report['redis'] = broker.info('server')['redis_version']
        with urlopen(urljoin(options.api_url, '/internal/readiness'), timeout=10) as response:
            readiness = json.load(response)
        require(readiness['status'] == 'ready' and readiness['runtime_admission'] == 'admitted_current',
            '当前API依赖或运行准入未就绪')
        loaded = readiness['loaded_config_version']
        require(all(loaded[key] == value for key, value in gate_identity.items()), 'API与明确数据库的运行身份不同')
        with urlopen(options.frontend_url, timeout=10) as response:
            status, cache = response.status, response.headers.get('Cache-Control')
        require(status == 200 and cache == 'no-store', '前端未提供正常禁止缓存响应')
        report.update(readiness=readiness, frontend_http=status, frontend_cache=cache,
            api_url=options.api_url, frontend_url=options.frontend_url)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
