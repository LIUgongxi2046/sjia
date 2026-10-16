"""Restore mount/DB-marker admission contracts; all ORM reads are mocked."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, override_settings

from retention.content_journal import ContentJournalRejected, durable_create
from retention.content_recovery import BLOCKED, configured_restore_root


class RestoreDatabaseMarkerTests(SimpleTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'restored'
        (self.root / 'objects').mkdir(parents=True)
        self.state = SimpleNamespace(id=uuid4(), database_name='synthetic_restore', cluster_id='isolated',
            object_root=str(self.root / 'objects'), backup_manifest_sha256='a' * 64, content_checkpoint=None)
        self.setting = override_settings(FILE_OBJECT_ROOT=self.root / 'objects', SJIA_RESTORE_ROOT=None)
        self.setting.enable()
        self.addCleanup(self.setting.disable)
        self.query = patch('retention.models.ContentRestoreState.objects.all')
        self.rows = self.query.start()
        self.addCleanup(self.query.stop)
        self.rows.return_value.__getitem__.return_value = [self.state]

    def marker(self):
        return {'state': 'ADOPTION_BLOCKED', 'target': {'restore_id': str(self.state.id),
            'database': self.state.database_name, 'cluster_id': self.state.cluster_id,
            'object_root': self.state.object_root}, 'content_checkpoint': self.state.content_checkpoint,
            'backup_manifest_sha256': self.state.backup_manifest_sha256}

    def test_database_marker_requires_missing_filesystem_marker(self):
        with self.assertRaisesRegex(ContentJournalRejected, 'DOCUMENT_UNAVAILABLE'):
            configured_restore_root()

    def test_database_marker_detects_omitted_explicit_restore_setting(self):
        durable_create(self.root / BLOCKED, self.marker())
        self.assertEqual(configured_restore_root(), self.root)

    def test_different_object_mount_cannot_bypass_database_restore_state(self):
        durable_create(self.root / BLOCKED, self.marker())
        with override_settings(FILE_OBJECT_ROOT=self.root.parent / 'unrelated/objects'):
            with self.assertRaisesRegex(ContentJournalRejected, 'OBJECT_ROOT_MISMATCH'):
                configured_restore_root()

    def test_wrong_marker_identity_cannot_satisfy_database_requirement(self):
        marker = self.marker()
        marker['target']['restore_id'] = str(uuid4())
        durable_create(self.root / BLOCKED, marker)
        with self.assertRaisesRegex(ContentJournalRejected, 'DATABASE_STATE_MISMATCH'):
            configured_restore_root()

    def test_unavailable_schema_is_never_treated_as_no_restore(self):
        self.rows.side_effect = RuntimeError('SYNTHETIC UNAVAILABLE SCHEMA')
        with self.assertRaises(RuntimeError):
            configured_restore_root()

    def test_empty_database_marker_and_absent_filesystem_marker_is_normal_deployment(self):
        self.rows.return_value.__getitem__.return_value = []
        self.assertIsNone(configured_restore_root())
