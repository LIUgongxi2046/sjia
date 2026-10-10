import argparse
import json
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from tests.verify_v2_migration import ROOT, command, database_args, migration_connection
from tests.verify_v2_migration import object_manifest, require, restore_objects, sha256, snapshot
from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


TARGET = [('archives', '0005_workflow_evaluation')]


def check_schema(database):
    row = database.execute("SELECT pg_get_constraintdef(oid), convalidated FROM pg_constraint "
        "WHERE conrelid='sjia.purpose_copy'::regclass AND conname='purpose_copy_purpose_check'").fetchone()
    require(row is not None and row[1] is True, '用途副本缺少有效用途约束。')
    for purpose in ('external_recognition', 'format_evaluation', 'workflow_evaluation',
            'clinical_evaluation', 'competition_display', 'export'):
        require(f"'{purpose}'::text" in row[0], '用途副本约束缺少准确用途。')
    return row[0]


def migrate(connection):
    executor = MigrationExecutor(connection)
    require([(item.app_label, item.name, backwards) for item, backwards in executor.migration_plan(TARGET)]
        == [('archives', '0005_workflow_evaluation', False)], '流程评价迁移包含其他变更。')
    executor.migrate(TARGET)
    require(not MigrationExecutor(connection).migration_plan(TARGET), '流程评价迁移没有完成。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    arguments = parser.parse_args()
    config = settings.DATABASES['default']
    require(config['NAME'] == 'sjia_dev', '迁移只允许当前已接收的本地数据库。')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    work = ROOT / '.planning-work' / f'workflow-evaluation-{stamp}'
    work.mkdir(mode=0o700)
    restored_name = f'sjia_workflow_{stamp.lower()}'
    commands = []
    options = ['--host', config['HOST'], '--port', str(config['PORT']), '--username', config['USER']]
    backup, objects_zip = work / 'database.dump', work / 'objects.zip'
    with psycopg.connect(**database_args(config['NAME'])) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        before = snapshot(source)
        snapshot_id = source.execute('SELECT pg_export_snapshot()').fetchone()[0]
        objects = object_manifest(source, settings.FILE_OBJECT_ROOT, objects_zip)
        command(['pg_dump', *options, '--format', 'custom', '--snapshot', snapshot_id,
            '--file', str(backup), config['NAME']], commands)
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
    migrate(migration_connection(restored_name, 'workflow_evaluation_restore'))
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored) == before, '恢复数据库迁移改变业务资料。')
        constraint = check_schema(restored)
    if arguments.apply:
        with psycopg.connect(**database_args(config['NAME'])) as source:
            require(snapshot(source) == before, '当前数据库已变化，需要重新取得一致备份。')
            require(object_manifest(source, settings.FILE_OBJECT_ROOT) == objects, '当前原件已变化，需要重新核查。')
        migrate(connections['default'])
        with psycopg.connect(**database_args(config['NAME'])) as source:
            require(snapshot(source) == before, '当前迁移改变业务资料。')
            require(object_manifest(source, settings.FILE_OBJECT_ROOT) == objects, '当前迁移改变原件。')
            require(check_schema(source) == constraint, '当前和恢复数据库的用途约束不一致。')
    report = {'status': 'PASS', 'scope': 'ACTUAL_WORKFLOW_EVALUATION_PURPOSE_MIGRATION',
        'checked_at': datetime.now(timezone.utc).isoformat(), 'backup_path': str(backup.relative_to(ROOT)),
        'backup_sha256': sha256(backup), 'objects_sha256': sha256(objects_zip),
        'restored_database': restored_name, 'business_tables': len(before), 'business_data_unchanged': True,
        'originals': len(objects), 'original_bytes': sum(item['size_bytes'] for item in objects),
        'migration': 'archives.0005_workflow_evaluation', 'source_applied': arguments.apply,
        'purpose_copy_count': before['purpose_copy']['count'], 'clinical_data_created': False,
        'workflow_business_validation': 'NOT_RUN', 'constraint': constraint, 'commands': commands}
    (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (ROOT / '.planning-work/t48-workflow-migration.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'commands'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
