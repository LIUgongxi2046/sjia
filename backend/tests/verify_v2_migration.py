import argparse
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

import django

django.setup()

from django.apps import apps
from django.conf import settings
from django.core.management.base import no_translations
from django.db import connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database_args(name):
    database = settings.DATABASES['default']
    return {'dbname': name, 'user': database['USER'], 'password': database['PASSWORD'], 'host': database['HOST'], 'port': database['PORT']}


def snapshot(conn, table_names=None):
    if table_names is None:
        table_names = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='sjia' ORDER BY tablename").fetchall()
        table_names = [row[0] for row in table_names]
    report = {}
    for name in table_names:
        digest = hashlib.sha256()
        count = 0
        query = sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text').format(sql.Identifier('sjia', name))
        for row in conn.execute(query):
            raw = row[0].encode('utf-8')
            digest.update(len(raw).to_bytes(8, 'big'))
            digest.update(raw)
            count += 1
        report[name] = {'count': count, 'sha256': digest.hexdigest()}
    return report


def command(args, commands):
    database = settings.DATABASES['default']
    environment = os.environ.copy()
    environment['PGPASSWORD'] = database['PASSWORD']
    environment['TMPDIR'] = str(ROOT / '.planning-work/tmp')
    result = subprocess.run(args, env=environment, cwd=ROOT, text=True, capture_output=True, check=True)
    commands.append({'command': args, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})


def object_manifest(conn, root, zip_path=None):
    manifest = []
    rows = conn.execute("SELECT id::text, object_key, object_version, checksum, size_bytes FROM sjia.file_object WHERE status='available' ORDER BY id").fetchall()
    archive = zipfile.ZipFile(zip_path, 'x', compression=zipfile.ZIP_DEFLATED) if zip_path is not None else None
    for file_id, object_key, version, expected, size in rows:
        path = (root / object_key).resolve()
        require(path.is_relative_to(root.resolve()), '原件路径超出对象目录')
        require(path.is_file(), '可用对象缺少实际文件')
        require(path.stat().st_size == size, '对象字节数量不一致')
        require(sha256(path) == expected, '对象 checksum 不一致')
        if archive is not None:
            archive.write(path, arcname=object_key)
        manifest.append({'file_id': file_id, 'object_key': object_key, 'object_version': version, 'checksum': expected, 'size_bytes': size})
    if archive is not None:
        archive.close()
    return manifest


def restore_objects(zip_path, manifest, root):
    root.mkdir(mode=0o700)
    with zipfile.ZipFile(zip_path) as archive:
        require(set(archive.namelist()) == {item['object_key'] for item in manifest}, '对象备份清单不一致')
        for item in manifest:
            path = (root / item['object_key']).resolve()
            require(path.is_relative_to(root.resolve()), '备份对象路径超出恢复目录')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(archive.read(item['object_key']))
            path.chmod(0o600)
            require(sha256(path) == item['checksum'], '恢复对象 checksum 不一致')


def migration_connection(name, alias):
    configuration = settings.DATABASES['default'].copy()
    configuration['NAME'] = name
    configuration['CONN_MAX_AGE'] = 0
    connections.databases[alias] = configuration
    return connections[alias]


@no_translations
def migrate(conn):
    executor = MigrationExecutor(conn)
    state = executor.loader.project_state()
    changes = MigrationAutodetector(state, ProjectState.from_apps(apps)).changes(graph=executor.loader.graph)
    require(not changes, f'ORM 和 migration state 不一致：{sorted(changes)}')
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)
    require([(migration.app_label, migration.name) for migration, backwards in plan] == [('archives', '0001_initial'), ('archives', '0002_legacy_facts')], '迁移计划包含未经本任务核验的变更')
    executor.migrate(targets)
    require(not MigrationExecutor(conn).migration_plan(targets), '迁移后仍有待执行项')
    return [f'{migration.app_label}.{migration.name}' for migration, backwards in plan]


def verify_structure(conn, alias, *, model_registry=apps, expected_constraints=414):
    inventory = {}
    app_models = list(model_registry.get_app_config('archives').get_models())
    require(len(app_models) == 22, '新增 ORM 实体数量不正确')
    for model in app_models:
        table = model._meta.db_table.split('"."')[1]
        columns = conn.execute('SELECT column_name, is_nullable, COALESCE(domain_name, udt_name) FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position', ('sjia', table)).fetchall()
        expected = {field.column: field for field in model._meta.local_fields}
        require(set(expected) == {row[0] for row in columns}, f'ORM 字段与数据库列不一致：{table}')
        for name, nullable, dtype in columns:
            field = expected[name]
            require((nullable == 'YES') == field.null, f'空值语义不一致：{table}.{name}')
            field_type = field.db_type(connections[alias])
            mapped_types = {'uuid': 'uuid', 'text': 'text', 'bigint': 'int8', 'integer': 'int4', 'timestamp with time zone': 'timestamptz', 'time': 'time', 'jsonb': 'jsonb', 'numeric': 'numeric', 'sjia.sha256_hex': 'sha256_hex'}
            require(field_type in mapped_types and mapped_types[field_type] == dtype, f'字段类型不一致：{table}.{name}')
        values = list(model.objects.using(alias).values())
        database_count = conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier('sjia', table))).fetchone()[0]
        require(len(values) == database_count, f'ORM 回读数量不一致：{table}')
        inventory[table] = {'columns': len(columns), 'orm_readback_count': database_count}
    constraints = conn.execute("SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='sjia' AND t.relname=ANY(%s)", (list(inventory),)).fetchone()[0]
    require(constraints == expected_constraints, '新增约束数量不一致')
    require(conn.execute("SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='sjia' AND t.relname=ANY(%s) AND c.contype='f' AND (NOT c.convalidated OR c.confdeltype='c')", (list(inventory),)).fetchone()[0] == 0, '引用未验证或使用级联删除')
    return {'models': inventory, 'constraints': constraints}


def verify_facts(conn):
    mismatches = conn.execute('''
        SELECT count(*) FROM sjia.record_field f
        LEFT JOIN sjia.archive_fact_version v ON v.field_id=f.id
        LEFT JOIN sjia.medical_time mt ON (mt.id,mt.patient_id)=(f.medical_time_id,f.patient_id)
        WHERE v.id IS NULL OR v.patient_id<>f.patient_id OR v.record_version_id<>f.record_version_id
          OR v.medical_time_id IS DISTINCT FROM f.medical_time_id
          OR v.raw_time_text IS DISTINCT FROM mt.raw_text
          OR v.source_anchor_id IS DISTINCT FROM f.anchor_id
          OR v.confirmation_id IS DISTINCT FROM f.confirmation_id
          OR v.time_origin<>'legacy' OR v.context_type<>'unknown' OR v.assertion_state<>'unknown'
          OR v.available_at IS NOT NULL OR v.availability_status<>'unknown'
    ''').fetchone()[0]
    require(mismatches == 0, '历史事实逐字段回读不一致')
    count = conn.execute('SELECT count(*) FROM sjia.record_field').fetchone()[0]
    require(conn.execute('SELECT count(*) FROM sjia.archive_fact_version').fetchone()[0] == count, '历史事实数量不一致')
    require(conn.execute('SELECT count(*) FROM sjia.archive_fact').fetchone()[0] == count, '历史事实身份数量不一致')
    require(conn.execute('''SELECT count(*) FROM sjia.archive_fact_version v
        JOIN sjia.record_field f ON f.id=v.field_id
        LEFT JOIN sjia.archive_fact a ON (a.id,a.patient_id,a.current_version)=(v.fact_id,v.patient_id,v.version)
        WHERE a.id IS NULL''').fetchone()[0] == 0, '事实当前指针不存在')
    return {'fields_readback': count, 'mismatches': mismatches, 'availability_known_added': 0, 'verification_added': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    arguments = parser.parse_args()
    name = settings.DATABASES['default']['NAME']
    require(name == 'sjia_dev', '本任务只允许已接收的 sjia_dev 本地数据库')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    work = ROOT / '.planning-work' / f't39_{stamp}'
    work.mkdir(mode=0o700)
    restored_name = f'sjia_v2_verify_{stamp}'
    commands = []
    database = settings.DATABASES['default']
    options = ['--host', database['HOST'], '--port', str(database['PORT']), '--username', database['USER']]
    dump_path = work / 'database.dump'
    zip_path = work / 'objects.zip'
    with psycopg.connect(**database_args(name)) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        source.execute("SET LOCAL statement_timeout='120s'")
        identity = source.execute('SELECT current_database(), current_user, version()').fetchone()
        before = snapshot(source)
        snapshot_id = source.execute('SELECT pg_export_snapshot()').fetchone()[0]
        original_objects = object_manifest(source, settings.FILE_OBJECT_ROOT, zip_path)
        command(['pg_dump', *options, '--format', 'custom', '--snapshot', snapshot_id, '--file', str(dump_path), name], commands)
    dump_path.chmod(0o600)
    zip_path.chmod(0o600)
    with psycopg.connect(**database_args('postgres'), autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored_name)))
    command(['pg_restore', *options, '--dbname', restored_name, '--no-owner', '--exit-on-error', str(dump_path)], commands)
    object_root = work / 'restored-objects'
    restore_objects(zip_path, original_objects, object_root)
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored) == before, '数据库备份恢复后逐表内容不一致')
        require(object_manifest(restored, object_root) == original_objects, '恢复后的原件清单不一致')
    restored_django = migration_connection(restored_name, 'v2_restore')
    restored_plan = migrate(restored_django)
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored, list(before)) == before, '迁移改变原有业务数据')
        structure = verify_structure(restored, 'v2_restore')
        legacy = verify_facts(restored)
        new_names = list(structure['models'])
        migrated = snapshot(restored, new_names)
        module = importlib.import_module('archives.migrations.0002_legacy_facts')
        restored.execute(module.LEGACY_SQL)
        require(snapshot(restored, new_names) == migrated, '重复历史迁移产生新资料')
        require(snapshot(restored, list(before)) == before, '重复执行改变原有业务数据')
    source_plan = None
    source_readback = None
    if arguments.apply:
        with psycopg.connect(**database_args(name)) as source:
            require(snapshot(source) == before, '运行数据库已变化，需要重新取得一致备份')
            require(object_manifest(source, settings.FILE_OBJECT_ROOT) == original_objects, '原件已变化，需要重新核查')
        source_django = connections['default']
        source_plan = migrate(source_django)
        with psycopg.connect(**database_args(name)) as source:
            require(snapshot(source, list(before)) == before, '运行迁移改变原有资料')
            require(object_manifest(source, settings.FILE_OBJECT_ROOT) == original_objects, '运行迁移改变原件')
            source_readback = {'structure': verify_structure(source, 'default'), 'legacy': verify_facts(source)}
    manifest_path = work / 'backup-manifest.json'
    manifest_path.write_text(json.dumps({'source_database': name, 'restored_database': restored_name, 'tables': before, 'objects': original_objects, 'database_sha256': sha256(dump_path), 'objects_sha256': sha256(zip_path), 'commands': commands}, ensure_ascii=False, indent=2) + '\n')
    manifest_path.chmod(0o600)
    shutil.copyfile(dump_path, ROOT / '.planning-work/t39-database.dump')
    shutil.copyfile(zip_path, ROOT / '.planning-work/t39-objects.zip')
    shutil.copyfile(manifest_path, ROOT / '.planning-work/t39-backup-manifest.json')
    for name in ('t39-database.dump', 't39-objects.zip', 't39-backup-manifest.json'):
        (ROOT / '.planning-work' / name).chmod(0o600)
    source_paths = ['backend/archives/models.py', 'backend/archives/fields.py', 'backend/archives/migrations/0001_initial.py', 'backend/archives/migrations/0002_legacy_facts.py', 'backend/tests/verify_v2_migration.py', 'data-design/v2-schema.sql', 'backend/config/settings/base.py', 'deploy/Dockerfile']
    report = {
        'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS', 'capability': 'REAL',
        'database_identity': {'name': identity[0], 'user': identity[1], 'version': identity[2]},
        'restored_database': restored_name, 'backup_manifest': str(manifest_path.relative_to(ROOT)),
        'backup_manifest_sha256': sha256(manifest_path), 'preserved_tables': len(before),
        'available_objects_readback': len(original_objects), 'available_object_bytes': sum(item['size_bytes'] for item in original_objects),
        'restored_migrations': restored_plan, 'restored_structure': structure, 'restored_legacy': legacy,
        'repeated_backfill': 'PASS', 'source_migrations': source_plan, 'source_readback': source_readback,
        'clinical_answers': 'NOT_RUN', 'target_postgresql17': 'NOT_RUN', 'visual_validation': 'NOT_RUN',
        'source_sha256': {path: sha256(ROOT / path) for path in source_paths},
    }
    output = ROOT / '.planning-work/t39-migration.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
