import argparse
import json
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from tests.verify_v2_migration import ROOT, command, database_args, migration_connection
from tests.verify_v2_migration import object_manifest, require, restore_objects, sha256, snapshot
from django.apps import apps
from django.conf import settings
from django.core.management.base import no_translations
from django.db import connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState


@no_translations
def migrate(connection):
    executor = MigrationExecutor(connection)
    changes = MigrationAutodetector(executor.loader.project_state(), ProjectState.from_apps(apps)).changes(
        graph=executor.loader.graph)
    require(not changes, 'ArchivePage ORM 与 migration state 不一致。')
    target = [('archives', '0004_page_order')]
    plan = executor.migration_plan(target)
    require([(migration.app_label, migration.name, backwards) for migration, backwards in plan] ==
        [('archives', '0004_page_order', False)], '页面顺序迁移包含其他变更。')
    executor.migrate(target)
    require(not MigrationExecutor(connection).migration_plan(target), '页面顺序迁移没有完成。')


def check_schema(database):
    column = database.execute("SELECT is_nullable, data_type FROM information_schema.columns "
        "WHERE table_schema='sjia' AND table_name='archive_page' AND column_name='ordinal'").fetchone()
    require(column == ('NO', 'integer'), '批次页面顺序没有保存准确整数。')
    constraints = database.execute("SELECT conname, contype, convalidated FROM pg_constraint "
        "WHERE conrelid='sjia.archive_page'::regclass AND conname IN "
        "('archive_page_ordinal_positive','archive_page_batch_ordinal_unique') ORDER BY conname").fetchall()
    require(constraints == [('archive_page_batch_ordinal_unique', 'u', True),
        ('archive_page_ordinal_positive', 'c', True)], '页面顺序缺少有效约束。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    arguments = parser.parse_args()
    database = settings.DATABASES['default']
    require(database['NAME'] == 'sjia_dev', '迁移只允许当前已接收的本地数据库。')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work = ROOT / '.planning-work' / f'archive-page-order-{stamp}'
    work.mkdir(mode=0o700)
    restored_name = f'sjia_page_order_{stamp.lower()}'
    commands = []
    options = ['--host', database['HOST'], '--port', str(database['PORT']), '--username', database['USER']]
    backup = work / 'database.dump'
    objects_zip = work / 'objects.zip'
    with psycopg.connect(**database_args(database['NAME'])) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        before = snapshot(source)
        require(before['archive_page']['count'] == 0 and before['archive_batch']['count'] == 0,
            '已有正式批次需要依据准确原请求保存顺序，不能生成顺序。')
        snapshot_id = source.execute('SELECT pg_export_snapshot()').fetchone()[0]
        objects = object_manifest(source, settings.FILE_OBJECT_ROOT, objects_zip)
        command(['pg_dump', *options, '--format', 'custom', '--snapshot', snapshot_id, '--file', str(backup),
            database['NAME']], commands)
    backup.chmod(0o600)
    objects_zip.chmod(0o600)
    with psycopg.connect(**database_args('postgres'), autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored_name)))
    command(['pg_restore', *options, '--dbname', restored_name, '--no-owner', '--exit-on-error', str(backup)], commands)
    restored_objects = work / 'restored-objects'
    restore_objects(objects_zip, objects, restored_objects)
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored) == before, '数据库备份恢复内容不一致。')
        require(object_manifest(restored, restored_objects) == objects, '原件备份恢复内容不一致。')
    migrate(migration_connection(restored_name, 'page_order_restore'))
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored) == before, '恢复数据库迁移改变业务资料。')
        check_schema(restored)
    if arguments.apply:
        with psycopg.connect(**database_args(database['NAME'])) as source:
            require(snapshot(source) == before, '当前数据库已变化，需要重新取得一致备份。')
            require(object_manifest(source, settings.FILE_OBJECT_ROOT) == objects, '当前原件已变化，需要重新核查。')
        migrate(connections['default'])
        with psycopg.connect(**database_args(database['NAME'])) as source:
            require(snapshot(source) == before, '当前迁移改变业务资料。')
            require(object_manifest(source, settings.FILE_OBJECT_ROOT) == objects, '当前迁移改变原件。')
            check_schema(source)
    report = {'status': 'PASS', 'scope': 'ACTUAL_DATABASE_AND_ORIGINALS_RESTORE_PAGE_ORDER_MIGRATION',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'backup_path': str(backup.relative_to(ROOT)),
        'backup_sha256': sha256(backup), 'objects_sha256': sha256(objects_zip),
        'restored_database': restored_name, 'business_tables': len(before), 'business_data_unchanged': True,
        'originals': len(objects), 'original_bytes': sum(item['size_bytes'] for item in objects),
        'migration': 'archives.0004_page_order', 'source_applied': arguments.apply,
        'batch_count': before['archive_batch']['count'], 'page_count': before['archive_page']['count'],
        'clinical_data_created': False, 'batch_business_validation': 'NOT_RUN', 'commands': commands}
    (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (ROOT / '.planning-work/t42-page-order-migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'commands'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
