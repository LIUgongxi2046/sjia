import importlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from tests.verify_v2_migration import command, database_args, migration_connection, object_manifest, require, restore_objects, sha256, snapshot, verify_facts, verify_structure
from django.conf import settings
from django.core.management.base import no_translations
from django.db.migrations.executor import MigrationExecutor


@no_translations
def migrate_archive(connection):
    executor = MigrationExecutor(connection)
    target = [('archives', '0002_legacy_facts')]
    plan = executor.migration_plan(target)
    require([(migration.app_label, migration.name, backwards) for migration, backwards in plan] ==
        [('archives', '0001_initial', False), ('archives', '0002_legacy_facts', False)], '原始备份的迁移范围不一致')
    executor.migrate(target)
    require(not MigrationExecutor(connection).migration_plan(target), '历史事实迁移仍未完成')
    return ['archives.0001_initial', 'archives.0002_legacy_facts']


def main():
    require(settings.DATABASES['default']['NAME'] == 'sjia_dev', '仅允许已接收的本地数据库')
    original_receipt_path = ROOT / 'docs/evidence/CHECK-T39/receipt.json'
    require(sha256(original_receipt_path) == '9fa574da917a48bea04218eb7d89893f245280d0d21322f899f39a640c37991b',
        '原始迁移证明内容不一致')
    receipt = json.loads(original_receipt_path.read_text())
    require(receipt['exit_code'] == 0 and receipt['inputs_unchanged'], '原始迁移证明未通过')
    for artifact in receipt['artifacts']:
        require(sha256(Path(artifact['path'])) == artifact['sha256'], '原始迁移产物内容不一致')
    manifest_path = ROOT / '.planning-work/t39-backup-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    dump_path = ROOT / '.planning-work/t39-database.dump'
    zip_path = ROOT / '.planning-work/t39-objects.zip'
    require(sha256(dump_path) == manifest['database_sha256'] and sha256(zip_path) == manifest['objects_sha256'],
        '原始数据库或文件备份校验和不一致')
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    work = ROOT / '.planning-work' / f't39_readback_{stamp}'
    work.mkdir(mode=0o700)
    restored_name = f'sjia_archive_readback_{stamp}'
    commands = []
    database = settings.DATABASES['default']
    options = ['--host', database['HOST'], '--port', str(database['PORT']), '--username', database['USER']]
    with psycopg.connect(**database_args('postgres'), autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored_name)))
    command(['pg_restore', *options, '--dbname', restored_name, '--no-owner', '--exit-on-error', str(dump_path)], commands)
    object_root = work / 'restored-objects'
    restore_objects(zip_path, manifest['objects'], object_root)
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored) == manifest['tables'], '原始备份恢复后资料内容不一致')
        require(object_manifest(restored, object_root) == manifest['objects'], '原始文件恢复后内容不一致')
    alias = 'archive_readback'
    restored_django = migration_connection(restored_name, alias)
    migrations = migrate_archive(restored_django)
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored, list(manifest['tables'])) == manifest['tables'], '迁移改变原有字段内容')
        historical_models = MigrationExecutor(restored_django).loader.project_state([('archives', '0002_legacy_facts')]).apps
        structure = verify_structure(restored, alias, model_registry=historical_models, expected_constraints=414)
        facts = verify_facts(restored)
        before_repeat = snapshot(restored)
    module = importlib.import_module('archives.migrations.0002_legacy_facts')
    with psycopg.connect(**database_args(restored_name)) as restored:
        restored.execute(module.LEGACY_SQL)
        require(snapshot(restored) == before_repeat, '重复迁移改变历史事实或原有内容')
    executor = MigrationExecutor(restored_django)
    target = [('archives', '0005_workflow_evaluation')]
    increment_plan = executor.migration_plan(target)
    require([(migration.app_label, migration.name, backwards) for migration, backwards in increment_plan] ==
        [('archives', '0003_review_unknown', False), ('archives', '0004_page_order', False),
            ('archives', '0005_workflow_evaluation', False)], '当前增量迁移范围不一致')
    executor.migrate(target)
    with psycopg.connect(**database_args(restored_name)) as restored:
        require(snapshot(restored) == before_repeat, '当前增量迁移改变原有内容')
        restored_current_structure = verify_structure(restored, alias, expected_constraints=417)
    with psycopg.connect(**database_args('sjia_dev')) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        current_structure = verify_structure(source, 'default', expected_constraints=417)
        current_facts = verify_facts(source)
        current_objects = object_manifest(source, settings.FILE_OBJECT_ROOT)
        require(current_objects == manifest['objects'], '当前文件内容与原始备份不一致')
    report = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS', 'capability': 'REAL',
        'scope': 'ORIGINAL_BACKUP_MIGRATION_AND_CURRENT_READBACK', 'original_receipt': str(original_receipt_path.relative_to(ROOT)),
        'original_receipt_sha256': sha256(original_receipt_path), 'original_backup_manifest_sha256': sha256(manifest_path),
        'restored_database': restored_name, 'restored_migrations': migrations, 'preserved_tables': len(manifest['tables']),
        'available_objects_readback': len(current_objects), 'available_object_bytes': sum(item['size_bytes'] for item in current_objects),
        'restored_structure': structure, 'restored_facts': facts, 'current_structure': current_structure,
        'restored_current_structure': restored_current_structure,
        'restored_incremental_migrations': ['archives.0003_review_unknown', 'archives.0004_page_order',
            'archives.0005_workflow_evaluation'],
        'current_facts': current_facts, 'repeat_execution': 'PASS', 'source_database_mutated': False, 'commands': commands,
        'provider_called': False, 'clinical_rows_inserted': 0, 'clinical_validation': 'NOT_RUN',
        'visual_validation': 'NOT_RUN', 'device_validation': 'NOT_RUN'}
    (ROOT / '.planning-work/t39-current.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
