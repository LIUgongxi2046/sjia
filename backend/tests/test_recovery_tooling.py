"""Pure synthetic files and mocked processes/DB; never backs up/restores a real DB."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


class RecoveryToolingTests(SimpleTestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('sjia_recovery', ROOT / 'deploy/recovery.py')
        self.recovery = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.recovery)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.objects, self.evidence = self.root / 'source-objects', self.root / 'source-evidence'
        self.objects.mkdir()
        self.evidence.mkdir()
        (self.objects / 'synthetic-object').write_bytes(b'SYNTHETIC ONLY')
        (self.evidence / 'synthetic-evidence.json').write_text('{"synthetic":true}')
        self.destination = self.root / 'backup'
        self.refs = [{'object_key': 'synthetic-object', 'object_version': 'v1',
            'checksum': self.recovery.sha256(self.objects / 'synthetic-object'),
            'size_bytes': 14, 'status': 'available'}]
        self.document = {'operation': 'backup', 'operator_approved': True, 'approval_reference': 'synthetic-only',
            'operator_reference': 'synthetic-operator', 'environment': 'synthetic',
            'database': 'synthetic_source', 'database_service': 'synthetic_service', 'cluster_id': '123',
            'compose_project': 'synthetic-project', 'release_reference': 'synthetic-release',
            'destination': str(self.destination), 'object_root': str(self.objects), 'evidence_root': str(self.evidence),
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'timeout_seconds': 300, 'destination_protected': True, 'scope_and_retention_approved': True,
            'exclusive_object_writer_scope_confirmed': True, 'external_effects_reconciled': True}
        self.commands = []
        self.conn = MagicMock()
        self.conn.execute.return_value.fetchall.return_value = []
        self.conn.execute.return_value.fetchone.return_value = (0,)

    @contextmanager
    def connector(self, document):
        yield self.conn

    def runner(self, command, **kwargs):
        self.commands.append(command)
        if command[0] == 'pg_dump':
            Path(command[command.index('--file') + 1]).write_bytes(b'SYNTHETIC NONDATABASE DUMP')
        if '--services' in command:
            return 'postgres\nredis\n'
        return ''

    def create_backup(self):
        with patch.object(self.recovery, 'check_identity') as identity, \
                patch.object(self.recovery, 'lock_writes') as lock, \
                patch.object(self.recovery, 'object_references', return_value=self.refs):
            result = self.recovery.backup(self.document, self.destination, self.objects, self.evidence,
                runner=self.runner, connector=self.connector)
        identity.assert_called_once_with(self.conn, self.document)
        lock.assert_called_once_with(self.conn)
        return result

    def restore_document(self):
        return {**self.document, 'operation': 'restore', 'database': 'synthetic_restore', 'cluster_id': '456',
            'source': str(self.destination), 'destination': str(self.root / 'restored'),
            **{field: True for field in ('isolated_cluster', 'outbound_blocked', 'application_and_workers_stopped',
                'empty_target_approved', 'backup_trusted_and_reviewed')}}

    def test_default_plan_never_opens_database_or_stops_services(self):
        result = subprocess.run([sys.executable, str(ROOT / 'deploy/recovery.py'), 'backup'],
            check=True, capture_output=True, text=True, timeout=30)
        self.assertIn('PLAN_ONLY', result.stdout)

    def test_unapproved_expired_or_mismatched_operation_is_rejected_before_command(self):
        for update in ({'operator_approved': False}, {'operation': 'restore'}, {'expires_at': '2000-01-01T00:00:00+00:00'},
                {'exclusive_object_writer_scope_confirmed': False}, {'external_effects_reconciled': False}):
            with self.subTest(update=update), self.assertRaises(self.recovery.RecoveryRejected):
                self.recovery.backup({**self.document, **update}, self.destination, self.objects, self.evidence,
                    runner=self.runner, connector=self.connector)
        self.assertEqual(self.commands, [])

    def test_backup_stops_exact_project_writers_and_produces_verified_complete_manifest(self):
        result = self.create_backup()
        self.assertEqual(result['state'], 'COMPLETE')
        self.assertEqual(self.recovery.verify_backup(self.destination), result)
        self.assertEqual(result['object_references'], self.refs)
        self.assertFalse(result['automatic_restart'])
        self.assertIn('synthetic-project', self.commands[0])
        self.assertEqual(self.commands[0][-len(self.recovery.WRITERS):], list(self.recovery.WRITERS))
        self.assertIn('--no-owner', self.commands[-1])
        self.assertFalse(any(token in ('up', 'start', 'down', '--clean', 'dropdb') for cmd in self.commands for token in cmd))

    def test_unknown_running_service_refuses_dump(self):
        def runner(command, **kwargs):
            self.commands.append(command)
            return 'postgres\nrogue-writer\n' if '--services' in command else ''
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'UNKNOWN_SERVICES'):
            self.recovery.backup(self.document, self.destination, self.objects, self.evidence,
                runner=runner, connector=self.connector)
        self.assertFalse(self.destination.exists())
        self.assertFalse(any(command[0] == 'pg_dump' for command in self.commands))

    def test_missing_or_wrong_object_bytes_refuses_complete_marker(self):
        self.refs[0]['checksum'] = '0' * 64
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'REFERENCE_INTEGRITY'):
            self.create_backup()
        self.assertFalse((self.destination / 'COMPLETE.json').exists())
        self.assertTrue((self.destination / 'WRITE_FREEZE.json').exists())

    def test_tampered_backup_or_added_file_is_rejected(self):
        self.create_backup()
        (self.destination / 'objects/extra').write_text('unexpected')
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'CHECKSUM_MISMATCH'):
            self.recovery.verify_backup(self.destination)

    def test_symlinks_and_overlapping_paths_are_rejected(self):
        (self.objects / 'link').symlink_to(self.evidence / 'synthetic-evidence.json')
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'UNSAFE_FILE_TYPE'):
            self.recovery.inventory(self.objects)
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'PATHS_OVERLAP'):
            self.recovery.distinct_paths(self.objects, self.objects / 'backup')

    def test_restore_is_transactional_to_empty_distinct_cluster_and_stays_blocked(self):
        self.create_backup()
        document = self.restore_document()
        with patch.object(self.recovery, 'check_identity'), patch.object(self.recovery, 'database_tables', return_value=[]), \
                patch.object(self.recovery, 'object_references', return_value=self.refs):
            result = self.recovery.restore(document, self.destination, self.root / 'restored',
                runner=self.runner, connector=self.connector)
        self.assertEqual(result['state'], 'RESTORED_ADOPTION_BLOCKED')
        self.assertFalse(result['outbound_enabled'])
        command = self.commands[-1]
        self.assertEqual(command[0], 'pg_restore')
        self.assertIn('--single-transaction', command)
        self.assertNotIn('--clean', command)
        self.assertTrue((self.root / 'restored/ADOPTION_BLOCKED.json').exists())

    def test_restore_refuses_existing_cluster_database_or_nonempty_target(self):
        self.create_backup()
        document = self.restore_document()
        for update in ({'cluster_id': '123'}, {'database': 'synthetic_source'}, {'outbound_blocked': False}):
            with self.assertRaises(self.recovery.RecoveryRejected):
                self.recovery.restore({**document, **update}, self.destination, self.root / 'restored',
                    runner=self.runner, connector=self.connector)
        with patch.object(self.recovery, 'check_identity'), \
                patch.object(self.recovery, 'database_tables', return_value=[('public', 'existing')]), \
                self.assertRaisesRegex(self.recovery.RecoveryRejected, 'NOT_EMPTY'):
            self.recovery.restore(document, self.destination, self.root / 'restored',
                runner=self.runner, connector=self.connector)
        self.assertFalse(any(command[0] == 'pg_restore' for command in self.commands))

    def test_database_identity_and_foreign_client_checks(self):
        self.conn.execute.return_value.fetchone.side_effect = [('other', '123')]
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'IDENTITY_MISMATCH'):
            self.recovery.check_identity(self.conn, self.document)
        self.conn.execute.return_value.fetchone.side_effect = [('synthetic_source', '123'), (1,)]
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'OTHER_CLIENT'):
            self.recovery.check_identity(self.conn, self.document)

    def test_share_locks_cover_every_app_table(self):
        with patch.object(self.recovery, 'database_tables', return_value=[('public', 'django_session'), ('sjia', 'task')]):
            self.recovery.lock_writes(self.conn)
        commands = [call.args[0] for call in self.conn.execute.call_args_list]
        self.assertEqual(commands[0], "SET LOCAL lock_timeout = '5s'")
        self.assertEqual(commands[1].as_string(), 'LOCK TABLE "public"."django_session" IN SHARE MODE')
        self.assertEqual(commands[2].as_string(), 'LOCK TABLE "sjia"."task" IN SHARE MODE')

    def test_path_mismatch_prevents_service_stop(self):
        with self.assertRaisesRegex(self.recovery.RecoveryRejected, 'EXACT_PATH'):
            self.recovery.backup({**self.document, 'object_root': '/different'}, self.destination,
                self.objects, self.evidence, runner=self.runner, connector=self.connector)
        self.assertEqual(self.commands, [])
