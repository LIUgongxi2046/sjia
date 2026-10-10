import json

import psycopg
from django.conf import settings

from tests.verify_v2_migration import ROOT, database_args, object_manifest, require, sha256, snapshot
from tests.verify_archive_page_order_migration import check_schema


def main():
    report_path = ROOT / '.planning-work/t42-page-order-migration.json'
    applied = json.loads(report_path.read_text())
    require(applied['status'] == 'PASS' and applied['source_applied'] is True,
        '页面顺序迁移缺少实际应用证据。')
    backup = ROOT / applied['backup_path']
    require(sha256(backup) == applied['backup_sha256'], '迁移前数据库备份摘要不一致。')
    objects_zip = backup.with_name('objects.zip')
    require(sha256(objects_zip) == applied['objects_sha256'], '迁移前原件备份摘要不一致。')
    with psycopg.connect(**database_args(settings.DATABASES['default']['NAME'])) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        current = snapshot(source)
        check_schema(source)
        objects = object_manifest(source, settings.FILE_OBJECT_ROOT)
    with psycopg.connect(**database_args(applied['restored_database'])) as restored:
        restored.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        require(snapshot(restored) == current, '当前业务资料与实际恢复库不一致。')
        check_schema(restored)
        require(object_manifest(restored, backup.parent / 'restored-objects') == objects,
            '当前原件与实际恢复原件不一致。')
    require(len(current) == applied['business_tables'] and len(objects) == applied['originals']
        and sum(item['size_bytes'] for item in objects) == applied['original_bytes'], '迁移证据数量与当前来源不一致。')
    result = {'status': 'PASS', 'checks': 3, 'scope': 'ACTUAL_PAGE_ORDER_SCHEMA_BACKUP_AND_RESTORE_READBACK',
        'business_tables': len(current), 'originals': len(objects),
        'original_bytes': sum(item['size_bytes'] for item in objects), 'business_data_unchanged': True,
        'batch_count': current['archive_batch']['count'], 'page_count': current['archive_page']['count'],
        'migration_report_sha256': sha256(report_path), 'authenticated_business_validation': 'NOT_RUN'}
    (ROOT / '.planning-work/t42-page-order-current.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
