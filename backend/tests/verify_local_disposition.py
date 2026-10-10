import json
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

from tests.verify_v2_migration import database_args, object_manifest, require, sha256, snapshot
from django.conf import settings
from files.deletion import LocalDeletionRejected, delete_local_object, deletion_keys, digest, object_path, prepare_local_deletion, read_deletion_receipt, sync_directory
from files.models import FileObject
from files.service import object_store
from files.storage import LocalFileStore


def main():
    with psycopg.connect(**database_args(settings.DATABASES['default']['NAME'])) as connection:
        before = snapshot(connection)
        original_objects = object_manifest(connection, settings.FILE_OBJECT_ROOT)
    file = FileObject.objects.filter(purpose='original', status='available').order_by('created_at', 'id').first()
    require(file is not None, '缺少可用真实原件')
    other = FileObject.objects.filter(purpose='original', status='available').exclude(checksum=file.checksum).order_by('created_at', 'id').first()
    require(other is not None, '缺少具有不同摘要的实际原件')
    data = object_store.get(file.object_key)
    other_data = object_store.get(other.object_key)
    require(digest(data) == file.checksum and len(data) == file.size_bytes, '原件摘要及数量不一致')
    require(digest(other_data) == other.checksum and len(other_data) == other.size_bytes, '第二项原件摘要及数量不一致')
    work = ROOT / '.planning-work' / ('local-disposition_' + datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f'))
    work.mkdir(mode=0o700)
    store = LocalFileStore(work / 'objects')
    store.root.mkdir(mode=0o700)
    checks = []
    assertions = unittest.TestCase()

    operation = uuid.uuid4()
    key = f'local-verification/{operation}/original'
    store.put_immutable(key, data)
    receipt_key, receipt_data, receipt = delete_local_object(store, operation_id=operation,
        object_key=key, checksum=file.checksum, size_bytes=file.size_bytes)
    require(not store.exists(key) and receipt.observation == 'removed_now', '实际对象删除未得到确认')
    require(sha256(store.root / receipt_key) == digest(receipt_data), '实际回执摘要不一致')
    repeated_key, repeated_data, repeated_receipt = delete_local_object(store, operation_id=operation,
        object_key=key, checksum=file.checksum, size_bytes=file.size_bytes)
    require((receipt_key, receipt_data, receipt) == (repeated_key, repeated_data, repeated_receipt), '重复执行未保持原有准确回执')
    checks.append({'name': '实际文件删除、回执读取及相同身份重新读取', 'status': 'PASS', 'operation_id': str(operation),
        'receipt_path': str((store.root / receipt_key).relative_to(ROOT)), 'receipt_sha256': digest(receipt_data)})

    operation = uuid.uuid4()
    key = f'local-verification/{operation}/original'
    store.put_immutable(key, data)
    with assertions.assertRaises(LocalDeletionRejected) as raised:
        delete_local_object(store, operation_id=operation, object_key=key, checksum=other.checksum, size_bytes=file.size_bytes)
    require(raised.exception.reason_code == 'DELETION_CHECKSUM_MISMATCH', '不同实际摘要没有被拒绝')
    journal_key, receipt_key = deletion_keys(operation)
    require(store.get(key) == data and not store.exists(journal_key) and not store.exists(receipt_key), '拒绝之后改变了对象或生成了回执')
    checks.append({'name': '不同原件摘要拒绝及文件保留', 'status': 'PASS', 'reason_code': raised.exception.reason_code})

    operation = uuid.uuid4()
    key = f'local-verification/{operation}/original'
    store.put_immutable(key, data)
    prepare_local_deletion(store, operation_id=operation, object_key=key, checksum=file.checksum, size_bytes=file.size_bytes)
    store.put(key, other_data)
    with assertions.assertRaises(LocalDeletionRejected) as raised:
        delete_local_object(store, operation_id=operation, object_key=key, checksum=file.checksum, size_bytes=file.size_bytes)
    require(raised.exception.reason_code == 'DELETION_OBJECT_CHANGED' and store.get(key) == other_data, '准备之后的真实对象更换未被拒绝')
    checks.append({'name': '准备之后更换为另一实际文件的拒绝', 'status': 'PASS', 'reason_code': raised.exception.reason_code})

    operation = uuid.uuid4()
    key = f'local-verification/{operation}/original'
    store.put_immutable(key, data)
    prepare_local_deletion(store, operation_id=operation, object_key=key, checksum=file.checksum, size_bytes=file.size_bytes)
    target = object_path(store, key)
    target.unlink()
    sync_directory(target.parent)
    receipt_key, receipt_data, receipt = delete_local_object(store, operation_id=operation,
        object_key=key, checksum=file.checksum, size_bytes=file.size_bytes)
    require(receipt.observation == 'recovered_absence' and not store.exists(key), '实际准备记录和已删除对象不能重新核验')
    checks.append({'name': '存在准备证明且对象实际删除后的重新核验', 'status': 'PASS', 'operation_id': str(operation),
        'receipt_path': str((store.root / receipt_key).relative_to(ROOT)), 'receipt_sha256': digest(receipt_data)})

    protected = work / 'protected-original'
    protected.write_bytes(other_data)
    alias = store.root / 'protected-alias'
    alias.symlink_to(protected)
    for prohibited in ('protected-alias', '../protected-original', str(protected)):
        with assertions.assertRaises(LocalDeletionRejected) as raised:
            delete_local_object(store, operation_id=uuid.uuid4(), object_key=prohibited,
                checksum=other.checksum, size_bytes=other.size_bytes)
        require(raised.exception.reason_code == 'DELETION_PATH_INVALID', '对象路径范围未拒绝')
    require(protected.read_bytes() == other_data, '禁止路径检查改变受保护文件')
    checks.append({'name': '符号链接、父级路径及绝对路径拒绝', 'status': 'PASS', 'paths_checked': 3})

    operation = uuid.uuid4()
    key = f'local-verification/{operation}/original'
    with assertions.assertRaises(LocalDeletionRejected) as raised:
        read_deletion_receipt(store, operation_id=operation, object_key=key,
            checksum=file.checksum, size_bytes=file.size_bytes)
    require(raised.exception.reason_code == 'DELETION_RECEIPT_REQUIRED', '缺少实际回执未被拒绝')
    with assertions.assertRaises(LocalDeletionRejected) as raised:
        delete_local_object(store, operation_id=operation, object_key=key,
            checksum=file.checksum, size_bytes=file.size_bytes)
    require(raised.exception.reason_code == 'DELETION_PREPARATION_REQUIRED', '缺少实际文件及准备证明未被拒绝')
    journal_key, receipt_key = deletion_keys(operation)
    require(not store.exists(journal_key) and not store.exists(receipt_key), '缺少实际证明时生成了删除记录')
    checks.append({'name': '缺少实际回执、文件及准备证明的拒绝', 'status': 'PASS',
        'reason_codes': ['DELETION_RECEIPT_REQUIRED', 'DELETION_PREPARATION_REQUIRED']})

    with psycopg.connect(**database_args(settings.DATABASES['default']['NAME'])) as connection:
        require(snapshot(connection) == before, '文件系统验证改变业务数据库')
        require(object_manifest(connection, settings.FILE_OBJECT_ROOT) == original_objects, '文件系统验证改变原有文件')
        inventory = {'requests': connection.execute('SELECT count(*) FROM sjia.disposition_request').fetchone()[0],
            'items': connection.execute('SELECT count(*) FROM sjia.disposition_item').fetchone()[0]}
    report = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'status': 'PASS',
        'scope': 'ACTUAL_FILESYSTEM_AND_RECEIPTS', 'checks': checks, 'preserved_tables': len(before),
        'preserved_objects': len(original_objects), 'source_file_ids': [str(file.id), str(other.id)],
        'source_checksums': [file.checksum, other.checksum], 'work_directory': str(work.relative_to(ROOT)),
        'disposition_inventory': inventory, 'clinical_data_created': False, 'clinical_objects_deleted': 0,
        'business_approval_validation': 'NOT_RUN', 'business_execution_validation': 'NOT_RUN',
        'worker_recovery_validation': 'NOT_RUN', 'external_validation': 'NOT_RUN', 'visual_validation': 'NOT_RUN'}
    output = ROOT / '.planning-work/t40-local-disposition.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    output.chmod(0o600)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
