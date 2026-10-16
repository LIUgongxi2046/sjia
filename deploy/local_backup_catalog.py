"""Exact local backup catalog/expiry. No scheduler or default retention period.

Supports only immutable backup directories produced by deploy.recovery. This does
not purge PostgreSQL WAL, storage snapshots, cloud object versions or providers.
The separate protected catalog must outlive backups and must not be restored from
one of them. Actual expiry requires a new exact irreversible-action approval.
"""
from datetime import datetime, timezone
import fcntl
from functools import wraps
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
# This is an operator tool, not an installed backend package. Resolve its
# fixed sibling by file so standard backend test/distribution paths work.
_spec = importlib.util.spec_from_file_location('sjia_backup_recovery', Path(__file__).with_name('recovery.py'))
recovery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recovery)
sys.path.insert(0, str(ROOT / 'backend'))
from retention.content_journal import (checksum, durable_create, read_json,
    safe_path, sync_directory)


class BackupExpiryRejected(RuntimeError):
    pass


def reject(code):
    raise BackupExpiryRejected(code)


def _expiry(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError):
        reject('BACKUP_EXPLICIT_EXPIRY_REQUIRED')
    return parsed


def _paths(backup_root, catalog_root):
    backup, catalog = safe_path(backup_root), safe_path(catalog_root)
    if backup.is_relative_to(catalog) or catalog.is_relative_to(backup):
        reject('BACKUP_CATALOG_MUST_BE_INDEPENDENT')
    if not catalog.is_dir():
        reject('BACKUP_PROTECTED_CATALOG_REQUIRED')
    return backup, catalog


def _approval(document, operation, backup, catalog):
    try:
        UUID(document['backup_id'])
        if (document['operation'] != operation or document['operator_approved'] is not True
                or document['backup_root'] != str(backup) or document['catalog_root'] != str(catalog)
                or document['protected_catalog_confirmed'] is not True
                or not document['approval_reference'] or not document['operator_reference']
                or _expiry(document['approval_expires_at']) <= datetime.now(timezone.utc)):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        reject('EXACT_BACKUP_CATALOG_APPROVAL_REQUIRED')


def _catalog_lock(function):
    @wraps(function)
    def wrapped(document, backup_root, catalog_root):
        _, catalog = _paths(backup_root, catalog_root)
        fd = os.open(catalog / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return function(document, backup_root, catalog_root)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    return wrapped


@_catalog_lock
def catalog_backup(document, backup_root, catalog_root):
    inventory, sha256, verify_backup = recovery.inventory, recovery.sha256, recovery.verify_backup
    backup, catalog = _paths(backup_root, catalog_root)
    _approval(document, 'catalog-local-backup', backup, catalog)
    verify_backup(backup)
    digest = sha256(backup / 'manifest.json')
    if document.get('manifest_sha256') != digest:
        reject('BACKUP_APPROVED_MANIFEST_MISMATCH')
    expires_at = _expiry(document.get('expires_at'))
    policy = document.get('policy_sha256')
    if not isinstance(policy, str) or len(policy) != 64 or any(c not in '0123456789abcdef' for c in policy):
        reject('BACKUP_EXPLICIT_POLICY_REQUIRED')
    entry = {'schema_version': 'local-backup-catalog-1', 'backup_id': document['backup_id'],
        'backup_root': str(backup), 'manifest_sha256': digest, 'policy_sha256': policy,
        'expires_at': expires_at.isoformat(), 'files': inventory(backup),
        'approval_reference': document['approval_reference'],
        'operator_reference': document['operator_reference']}
    path = catalog / (document['backup_id'] + '.json')
    if path.exists():
        if read_json(path) != entry:
            reject('BACKUP_CATALOG_ENTRY_CONFLICT')
    else:
        durable_create(path, entry)
    return entry


@_catalog_lock
def expire_backup(document, backup_root, catalog_root):
    inventory = recovery.inventory
    backup, catalog = _paths(backup_root, catalog_root)
    _approval(document, 'expire-local-backup', backup, catalog)
    if (document.get('irreversible_expiry_approved') is not True
            or document.get('exclusive_backup_writer_scope_confirmed') is not True
            or document.get('legal_hold_state') != 'clear'):
        reject('BACKUP_EXPIRY_HOLD_OR_APPROVAL_REQUIRED')
    entry = read_json(catalog / (document['backup_id'] + '.json'))
    if (entry.get('schema_version') != 'local-backup-catalog-1'
            or entry.get('backup_id') != document['backup_id']
            or entry.get('backup_root') != str(backup)
            or document.get('catalog_entry_sha256') != checksum(entry)
            or document.get('manifest_sha256') != entry.get('manifest_sha256')):
        reject('BACKUP_EXPIRY_SCOPE_MISMATCH')
    if _expiry(entry['expires_at']) > datetime.now(timezone.utc):
        reject('BACKUP_NOT_EXPIRED')
    receipt_path = catalog / (document['backup_id'] + '.expired.json')
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        intent = read_json(catalog / (document['backup_id'] + '.expiry-intent.json'))
        if (backup.exists() or receipt.get('catalog_entry_sha256') != checksum(entry)
                or receipt.get('schema_version') != 'local-backup-expiry-receipt-1'
                or receipt.get('backup_id') != document['backup_id']
                or receipt.get('intent_sha256') != checksum(intent)
                or receipt.get('outcome') != 'local_cataloged_backup_absent'):
            reject('BACKUP_EXPIRY_RECEIPT_INVALID')
        return receipt
    # A durable exact intent makes interrupted expiry resumable without deleting
    # an unknown file, newly created backup, or an adjacent backup version.
    intent = {'schema_version': 'local-backup-expiry-intent-1',
        'catalog_entry_sha256': checksum(entry), 'approval_reference': document['approval_reference'],
        'operator_reference': document['operator_reference']}
    intent_path = catalog / (document['backup_id'] + '.expiry-intent.json')
    if intent_path.exists():
        if read_json(intent_path) != intent:
            reject('BACKUP_EXPIRY_INTENT_CONFLICT')
    else:
        if not backup.exists():
            reject('BACKUP_EXPIRY_PREPARATION_REQUIRED')
        if inventory(backup) != entry['files']:
            reject('BACKUP_CHANGED_SINCE_CATALOG')
        durable_create(intent_path, intent)
    approved = {row['path']: row for row in entry['files']}
    if backup.exists():
        for row in inventory(backup):
            if approved.get(row['path']) != row:
                reject('BACKUP_CHANGED_SINCE_CATALOG')
        for relative, row in sorted(approved.items()):
            target = safe_path(backup / relative)
            if not target.is_relative_to(backup):
                reject('BACKUP_CATALOG_PATH_INVALID')
            if not target.exists():
                continue
            before = target.stat()
            digest = hashlib.sha256()
            with target.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            after = target.stat()
            identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            if (identity(before) != identity(after) or after.st_size != row['size_bytes']
                    or digest.hexdigest() != row['sha256']):
                reject('BACKUP_CHANGED_DURING_EXPIRY')
            target.unlink()
            sync_directory(target.parent)
        for directory in sorted((path for path in backup.rglob('*') if path.is_dir()),
                key=lambda path: len(path.parts), reverse=True):
            directory.rmdir()
        backup.rmdir()
        sync_directory(backup.parent)
    receipt = {'schema_version': 'local-backup-expiry-receipt-1', 'backup_id': document['backup_id'],
        'catalog_entry_sha256': checksum(entry), 'intent_sha256': checksum(intent),
        'verified_absent_at': datetime.now(timezone.utc).isoformat(),
        'outcome': 'local_cataloged_backup_absent', 'all_physical_copies_erased': False}
    durable_create(receipt_path, receipt)
    return receipt


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('catalog', 'expire'))
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--catalog', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    if not args.execute:
        print('PLAN_ONLY; no catalog entry or backup changed; exact separate approval required')
        return
    if not args.admission or not args.backup or not args.catalog:
        parser.exit(2, 'EXACT_BACKUP_CATALOG_APPROVAL_REQUIRED\n')
    try:
        document = json.loads(args.admission.read_text())
        operation = catalog_backup if args.operation == 'catalog' else expire_backup
        operation(document, args.backup, args.catalog)
        print('LOCAL_BACKUP_CATALOGED' if args.operation == 'catalog' else 'CATALOGED_LOCAL_BACKUP_ABSENCE_VERIFIED_ONLY')
    except Exception:
        parser.exit(2, 'LOCAL_BACKUP_OPERATION_FAILED; NO_ALL_COPY_ERASURE_CLAIM\n')


if __name__ == '__main__':
    main()
