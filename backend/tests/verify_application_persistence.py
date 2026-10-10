import argparse
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql

from tests.verify_v2_migration import ROOT, require, command, database_args, object_manifest, sha256
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.state import ProjectState
from django.apps import apps
from django.core.management.base import no_translations
from relations.models import RelationApplication
from requests.models import WriteIntent


parser = argparse.ArgumentParser()
parser.add_argument('--apply', action='store_true')
parser.add_argument('--baseline-backup')
options = parser.parse_args()
require(settings.DATABASES['default']['NAME'] == 'sjia_dev', '仅允许当前本地业务数据库。')
stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
work = ROOT / '.planning-work' / f't54_application_{stamp}'
work.mkdir(mode=0o700)
commands = []
backup = work / 'database.dump'
database = settings.DATABASES['default']
command(['pg_dump', '--host', database['HOST'], '--port', str(database['PORT']), '--username', database['USER'],
    '--dbname', database['NAME'], '--format=custom', '--file', str(backup)], commands)


def preserved_rows(conn, table, columns):
    return conn.execute(sql.SQL('SELECT row_to_json(t)::text FROM (SELECT {} FROM {}) t ORDER BY row_to_json(t)::text').format(
        sql.SQL(',').join(sql.Identifier(column) for column in columns), sql.Identifier('sjia', table))).fetchall()


with psycopg.connect(**database_args(database['NAME'])) as source:
    source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    tables = ['relation_application', 'write_intent']
    columns = {table: [row[0] for row in source.execute(
        'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position', ('sjia', table))]
        for table in tables}
    original = {table: preserved_rows(source, table, columns[table]) for table in tables}
    objects = object_manifest(source, settings.FILE_OBJECT_ROOT)

baseline = None
restored_name = None
if options.baseline_backup:
    baseline = (ROOT / options.baseline_backup).resolve()
    require(baseline.is_relative_to((ROOT / '.planning-work').resolve()) and baseline.is_file(), '迁移前备份路径无效。')
    restored_name = f'sjia_application_readback_{stamp}'
    with psycopg.connect(**database_args('postgres'), autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored_name)))
    command(['pg_restore', '--host', database['HOST'], '--port', str(database['PORT']), '--username', database['USER'],
        '--dbname', restored_name, '--no-owner', '--exit-on-error', str(baseline)], commands)
    with psycopg.connect(**database_args(restored_name)) as restored:
        restored.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        tables = [row[0] for row in restored.execute("SELECT tablename FROM pg_tables WHERE schemaname='sjia' ORDER BY tablename")]
        columns = {table: [row[0] for row in restored.execute(
            'SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position', ('sjia', table))]
            for table in tables}
        original = {table: preserved_rows(restored, table, columns[table]) for table in tables}
        require(object_manifest(restored, settings.FILE_OBJECT_ROOT) == objects, '迁移前原件清单与当前文件不一致。')

executor = MigrationExecutor(connection)
targets = [('relations', '0002_application_input'), ('requests', '0004_recovery_payload')]
plan = executor.migration_plan(targets)
require(all(not backwards and (migration.app_label, migration.name) in targets for migration, backwards in plan),
    '本次迁移包含未指定操作。')
if options.apply:
    executor.migrate(targets)
require(not MigrationExecutor(connection).migration_plan(targets), '申请保存所需迁移尚未完成。')

@no_translations
def pending_model_changes():
    current = MigrationExecutor(connection)
    return MigrationAutodetector(current.loader.project_state(), ProjectState.from_apps(apps)).changes(graph=current.loader.graph)


changes = pending_model_changes()
require(not changes, '当前模型仍有未登记字段变化：' + repr({name: [operation.describe()
    for migration in migrations for operation in migration.operations] for name, migrations in changes.items()}))

with psycopg.connect(**database_args(database['NAME'])) as source:
    source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    for table in tables:
        require(preserved_rows(source, table, columns[table]) == original[table], '新增字段改变了已有保存内容。')
    require(object_manifest(source, settings.FILE_OBJECT_ROOT) == objects, '新增字段改变了已有原件。')
    constraint = source.execute("SELECT convalidated FROM pg_constraint WHERE conname='application_requested_access_valid'").fetchall()
    require(constraint == [(True,)], '申请权限数据库约束尚未验证。')

legacy_applications = RelationApplication.objects.count()
unknown_access = RelationApplication.objects.filter(requested_access__isnull=True).count()
legacy_requests = WriteIntent.objects.count()
payload_requests = WriteIntent.objects.filter(recovery_payload__isnull=False).count()
report = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS',
    'scope': 'APPLICATION_ADDITIVE_MIGRATION_AND_EXISTING_DATA_PRESERVATION',
    'backup_path': str(backup.relative_to(ROOT)), 'backup_sha256': sha256(backup),
    'original_backup_path': str(baseline.relative_to(ROOT)) if baseline else None,
    'original_backup_sha256': sha256(baseline) if baseline else None, 'restored_database': restored_name,
    'migrations': [f'{migration.app_label}.{migration.name}' for migration, backwards in plan],
    'preserved_existing_rows': {table: len(original[table]) for table in tables},
    'preserved_original_objects': len(objects), 'preserved_original_object_bytes': sum(item['size_bytes'] for item in objects),
    'legacy_applications': legacy_applications, 'unknown_requested_access': unknown_access,
    'existing_write_intents': legacy_requests, 'recoverable_payload_intents': payload_requests,
    'application_access_constraint': 'PASS', 'model_migration_consistency': 'PASS',
    'applications_created': False, 'approvals_created': False, 'credentials_submitted': False,
    'actual_application_submission_and_recovery': 'NOT_RUN', 'actual_concurrent_submission': 'NOT_RUN',
    'actual_withdrawal_and_approval': 'NOT_RUN', 'visual_validation': 'NOT_RUN', 'commands': commands}
(work / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
(ROOT / '.planning-work/t54-application-persistence.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
