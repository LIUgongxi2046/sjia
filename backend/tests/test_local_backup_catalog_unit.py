"""Synthetic local backup bytes only; never runs PostgreSQL, Docker or a provider."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import importlib.util
import unittest
from unittest.mock import patch
from uuid import uuid4

# Load operator tooling by its actual source path, matching test_recovery_tooling.
ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('sjia_local_backup_catalog', ROOT / 'deploy/local_backup_catalog.py')
_catalog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_catalog)
recovery = _catalog.recovery
BackupExpiryRejected, catalog_backup, expire_backup = _catalog.BackupExpiryRejected, _catalog.catalog_backup, _catalog.expire_backup
from retention.content_journal import checksum


class LocalBackupCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.backup, self.catalog = root / 'exact-backup', root / 'protected-catalog'
        self.backup.mkdir()
        self.catalog.mkdir()
        (self.backup / 'objects').mkdir()
        (self.backup / 'config-evidence').mkdir()
        (self.backup / 'objects/synthetic-object').write_bytes(b'SYNTHETIC BACKUP CANARY')
        (self.backup / 'database.dump').write_bytes(b'SYNTHETIC NONDATABASE BYTES')
        manifest = {'format': recovery.FORMAT, 'state': 'COMPLETE',
            'database_sha256': recovery.sha256(self.backup / 'database.dump'),
            'objects': recovery.inventory(self.backup / 'objects'),
            'config_evidence': [], 'object_references': []}
        recovery.write_json(self.backup / 'manifest.json', manifest)
        recovery.write_json(self.backup / 'COMPLETE.json',
            {'manifest_sha256': recovery.sha256(self.backup / 'manifest.json')})
        self.approval = {'operation': 'catalog-local-backup', 'operator_approved': True,
            'backup_id': str(uuid4()), 'backup_root': str(self.backup), 'catalog_root': str(self.catalog),
            'protected_catalog_confirmed': True, 'approval_reference': 'synthetic-explicit-approval',
            'operator_reference': 'synthetic-operator',
            'approval_expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
            'policy_sha256': '1' * 64, 'manifest_sha256': recovery.sha256(self.backup / 'manifest.json')}

    def expiry_approval(self):
        entry = catalog_backup(self.approval, self.backup, self.catalog)
        return {**self.approval, 'operation': 'expire-local-backup', 'catalog_entry_sha256': checksum(entry),
            'irreversible_expiry_approved': True, 'exclusive_backup_writer_scope_confirmed': True,
            'legal_hold_state': 'clear'}

    def test_exact_expiry_removes_only_approved_version_and_persists_receipt(self):
        adjacent = self.backup.parent / 'other-backup'
        adjacent.mkdir()
        (adjacent / 'keep').write_text('SYNTHETIC UNRELATED')
        approval = self.expiry_approval()
        receipt = expire_backup(approval, self.backup, self.catalog)
        self.assertFalse(self.backup.exists())
        self.assertEqual((adjacent / 'keep').read_text(), 'SYNTHETIC UNRELATED')
        self.assertFalse(receipt['all_physical_copies_erased'])
        self.assertEqual(expire_backup(approval, self.backup, self.catalog), receipt)

    def test_missing_expiry_or_policy_is_not_an_implicit_default(self):
        for field in ('expires_at', 'policy_sha256'):
            document = {key: value for key, value in self.approval.items() if key != field}
            with self.assertRaises(BackupExpiryRejected):
                catalog_backup(document, self.backup, self.catalog)

    def test_future_expiry_and_unresolved_hold_keep_backup(self):
        self.approval['expires_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        approval = self.expiry_approval()
        with self.assertRaisesRegex(BackupExpiryRejected, 'NOT_EXPIRED'):
            expire_backup(approval, self.backup, self.catalog)
        approval['legal_hold_state'] = 'unresolved'
        with self.assertRaisesRegex(BackupExpiryRejected, 'HOLD_OR_APPROVAL'):
            expire_backup(approval, self.backup, self.catalog)
        self.assertTrue((self.backup / 'database.dump').exists())

    def test_unknown_raced_file_cannot_be_deleted(self):
        approval = self.expiry_approval()
        (self.backup / 'unexpected').write_bytes(b'SYNTHETIC NEW VERSION')
        with self.assertRaisesRegex(BackupExpiryRejected, 'CHANGED_SINCE_CATALOG'):
            expire_backup(approval, self.backup, self.catalog)
        self.assertTrue((self.backup / 'database.dump').exists())
        self.assertTrue((self.backup / 'unexpected').exists())

    def test_wrong_manifest_or_missing_irreversible_approval_cannot_expire(self):
        approval = self.expiry_approval()
        for change in ({'manifest_sha256': '0' * 64}, {'irreversible_expiry_approved': False}):
            with self.assertRaises(BackupExpiryRejected):
                expire_backup({**approval, **change}, self.backup, self.catalog)
        self.assertTrue(self.backup.exists())

    def test_catalog_cannot_live_inside_backup(self):
        with self.assertRaisesRegex(BackupExpiryRejected, 'INDEPENDENT'):
            catalog_backup(self.approval, self.backup, self.backup / 'catalog')

    def test_interrupted_expiry_resumes_only_durable_exact_intent(self):
        approval = self.expiry_approval()
        original = Path.unlink
        removed = []
        def crash_after_first_file(path, *args, **kwargs):
            original(path, *args, **kwargs)
            removed.append(path)
            raise OSError('SYNTHETIC CRASH AFTER UNLINK')
        with patch.object(Path, 'unlink', crash_after_first_file):
            with self.assertRaises(OSError):
                expire_backup(approval, self.backup, self.catalog)
        self.assertEqual(len(removed), 1)
        self.assertFalse((self.catalog / (approval['backup_id'] + '.expired.json')).exists())
        receipt = expire_backup(approval, self.backup, self.catalog)
        self.assertEqual(receipt['outcome'], 'local_cataloged_backup_absent')
        self.assertFalse(self.backup.exists())
