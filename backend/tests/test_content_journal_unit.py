"""Pure temporary synthetic journal/restore tests; no ORM or database access."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

from retention.content_journal import (ContentJournal, ContentJournalRejected,
    checksum, durable_create, read_json)
from retention.content_recovery import (ADMITTED, BLOCKED, PROOF, admit_restore,
    assert_admission, replay_restore, verify_restore)


def envelope():
    manifest = {'schema_version': 'content-disposition-manifest-1', 'patient_id': str(uuid4()),
        'policy_id': str(uuid4()), 'policy_version': 1, 'policy_sha256': '1' * 64,
        'registry_sha256': '2' * 64, 'preservation_sha256': '3' * 64,
        'mode': 'erase_selected_content', 'environment': 'synthetic',
        'hold_id': str(uuid4()), 'hold_version': 1,
        'items': [{'kind': 'database', 'adapter': 'records.recordversion',
            'object_id': str(uuid4()), 'version': '1', 'row_sha256': '4' * 64}],
        'obligations': ['backup_copies'], 'created_at': datetime.now(timezone.utc).isoformat()}
    manifest['roots'] = [{key: value for key, value in manifest['items'][0].items() if key != 'row_sha256'}]
    digest = checksum(manifest)
    return {'schema_version': 'content-disposition-journal-1', 'plan_id': str(uuid4()),
        'manifest_sha256': digest, 'manifest': manifest,
        'approval': {'schema_version': 'content-disposition-approval-1',
            'manifest_sha256': digest, 'policy_sha256': manifest['policy_sha256'],
            'approver_id': str(uuid4()), 'auth_epoch': 1, 'service_grant_id': str(uuid4()),
            'case_grant_ids': [str(uuid4())], 'source_revision_id': str(uuid4()),
            'approved_at': datetime.now(timezone.utc).isoformat()}}


class ContentJournalUnitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'journal').mkdir()
        (self.root / 'trust').mkdir()
        self.journal = ContentJournal(self.root / 'journal', self.root / 'trust/checkpoint.json')
        self.genesis = self.journal.initialize()

    def test_exact_manifest_is_durable_chained_and_idempotent(self):
        first = envelope()
        entry = self.journal.append_manifest(first)
        self.assertEqual(entry['sequence'], 1)
        self.assertEqual(self.journal.append_manifest(first), entry)
        second = self.journal.append_manifest(envelope())
        self.assertEqual(second['sequence'], 2)
        self.assertEqual(second['previous_sha256'], entry['entry_sha256'])
        reopened = ContentJournal(self.root / 'journal', self.root / 'trust/checkpoint.json')
        self.assertEqual(reopened.verify()['checkpoint']['entry_sha256'], second['entry_sha256'])

    def test_explicit_access_revocation_is_distinct_from_erasure(self):
        value = envelope()
        value['manifest']['mode'] = 'revoke_access'
        value['manifest_sha256'] = checksum(value['manifest'])
        value['approval']['manifest_sha256'] = value['manifest_sha256']
        self.journal.append_manifest(value)
        stored = self.journal.verify()['entries'][0]['envelope']
        self.assertEqual(stored['manifest']['mode'], 'revoke_access')

    def test_truncation_and_missing_sequence_cannot_rewind_trusted_checkpoint(self):
        self.journal.append_manifest(envelope())
        self.journal.append_manifest(envelope())
        (self.root / 'journal/00000000000000000002.json').unlink()
        with self.assertRaisesRegex(ContentJournalRejected, 'TRUNCATED'):
            self.journal.verify()

    def test_changed_payload_or_approval_and_unknown_clinical_fields_fail(self):
        value = envelope()
        value['manifest']['items'][0]['clinical_text'] = 'SYNTHETIC CANARY'
        with self.assertRaisesRegex(ContentJournalRejected, 'MANIFEST_INVALID'):
            self.journal.append_manifest(value)
        value = envelope()
        value['approval']['policy_sha256'] = '0' * 64
        with self.assertRaisesRegex(ContentJournalRejected, 'MANIFEST_INVALID'):
            self.journal.append_manifest(value)
        value = envelope()
        value['manifest']['items'][0]['object_id'] = str(uuid4())
        with self.assertRaisesRegex(ContentJournalRejected, 'MANIFEST_INVALID'):
            self.journal.append_manifest(value)

    def test_exact_retry_recovers_fsynced_tail_but_other_plan_cannot(self):
        value = envelope()
        with patch('retention.content_journal.durable_replace', side_effect=OSError('synthetic crash')):
            with self.assertRaises(OSError):
                self.journal.append_manifest(value)
        with self.assertRaisesRegex(ContentJournalRejected, 'UNCHECKPOINTED'):
            self.journal.verify()
        with self.assertRaisesRegex(ContentJournalRejected, 'UNCHECKPOINTED'):
            self.journal.append_manifest(envelope())
        self.assertEqual(self.journal.append_manifest(value)['sequence'], 1)
        self.assertEqual(self.journal.verify()['checkpoint']['sequence'], 1)

    def test_journal_and_checkpoint_cannot_live_in_backup_or_object_store(self):
        with self.assertRaisesRegex(ContentJournalRejected, 'NOT_INDEPENDENT'):
            ContentJournal(self.root / 'journal', self.root / 'journal/checkpoint.json')
        with self.assertRaisesRegex(ContentJournalRejected, 'OVERLAPS'):
            ContentJournal(self.root / 'journal', self.root / 'trust/checkpoint.json',
                forbidden_roots=[self.root / 'journal'])

    def test_symlink_and_missing_checkpoint_are_rejected(self):
        checkpoint = self.root / 'trust/checkpoint.json'
        checkpoint.unlink()
        with self.assertRaises(ContentJournalRejected):
            self.journal.verify()
        checkpoint.symlink_to(self.root / 'journal/identity.json')
        with self.assertRaises(ContentJournalRejected):
            self.journal.verify()

    def test_gap_and_hash_tampering_are_rejected(self):
        self.journal.append_manifest(envelope())
        self.journal.append_manifest(envelope())
        (self.root / 'journal/00000000000000000001.json').unlink()
        with self.assertRaisesRegex(ContentJournalRejected, 'CHAIN_BROKEN'):
            self.journal.verify()

    def test_concurrent_appends_have_one_continuous_sequence(self):
        values = [envelope() for _ in range(8)]
        with ThreadPoolExecutor(max_workers=4) as workers:
            entries = list(workers.map(self.journal.append_manifest, values))
        self.assertEqual(sorted(entry['sequence'] for entry in entries), list(range(1, 9)))
        self.assertEqual(self.journal.verify()['checkpoint']['sequence'], 8)

    def test_nested_verification_from_new_instance_is_bounded_and_reentrant(self):
        self.journal.append_manifest(envelope())
        source = ('from retention.content_journal import ContentJournal\n'
            f'j = ContentJournal({str(self.journal.root)!r}, {str(self.journal.checkpoint_path)!r})\n'
            'with j.locked():\n'
            '    assert ContentJournal(j.root, j.checkpoint_path).verify()["checkpoint"]["sequence"] == 1\n')
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        result = subprocess.run([sys.executable, '-c', source], env=env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_nested_verification_cannot_change_checkpoint_binding(self):
        other = ContentJournal(self.journal.root, self.journal.checkpoint_path.with_name('different.json'))
        with self.journal.locked():
            with self.assertRaisesRegex(ContentJournalRejected, 'NESTED_CHECKPOINT_MISMATCH'):
                other.verify()

    def test_modified_chained_record_does_not_verify(self):
        self.journal.append_manifest(envelope())
        path = self.root / 'journal/00000000000000000001.json'
        value = read_json(path)
        value['recorded_at'] = '2000-01-01T00:00:00+00:00'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ContentJournalRejected, 'CHAIN_BROKEN'):
            self.journal.verify()

    def prepare_restore(self):
        self.restore = self.root / 'restored'
        (self.restore / 'objects').mkdir(parents=True)
        self.target = {'restore_id': str(uuid4()), 'database': 'synthetic_restored',
            'cluster_id': 'synthetic-isolated', 'object_root': str(self.restore / 'objects')}
        durable_create(self.restore / BLOCKED, {'state': 'ADOPTION_BLOCKED', 'target': self.target,
            'backup_manifest_sha256': 'a' * 64, 'content_checkpoint': self.genesis})
        value = envelope()
        # This is a real mutation of restored synthetic local content, without
        # standing in for the separately required PostgreSQL adapter integration.
        key = self.restore / 'objects/synthetic-snapshot.json'
        key.write_text(json.dumps({'id': value['manifest']['items'][0]['object_id'],
            'payload': 'SYNTHETIC RESTORED CLINICAL CANARY'}))
        self.journal.append_manifest(value)
        def replay(manifest):
            row = json.loads(key.read_text())
            self.assertEqual(row['id'], manifest['manifest']['items'][0]['object_id'])
            row['payload'] = None
            key.write_text(json.dumps(row))
        def verify(manifest):
            return json.loads(key.read_text())['payload'] is None
        return replay, verify

    def release(self, proof):
        return {'operation': 'admit-restored-runtime', 'operator_approved': True, 'target': self.target,
            'approval_reference': 'synthetic-explicit-release', 'operator_reference': 'synthetic-operator',
            'expires_at': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'content_proof_sha256': checksum(proof),
            **{field: True for field in ('revocations_replayed', 'disabled_configuration_reconciled',
                'external_effects_reconciled', 'permissions_revalidated', 'release_qualified',
                'isolated_content_validation_complete')}}

    def test_actual_callback_replay_and_admission_with_newer_journal(self):
        replay, verify = self.prepare_restore()
        proof = replay_restore(self.journal, self.restore, self.target, replay=replay, verify=verify)
        self.assertEqual(proof['verified_entries'], 1)
        self.assertNotIn('SYNTHETIC RESTORED CLINICAL CANARY',
            (self.restore / 'objects/synthetic-snapshot.json').read_text())
        with self.assertRaises(ContentJournalRejected):
            assert_admission(self.journal, self.restore, self.target, verify=verify)
        admitted = admit_restore(self.journal, self.restore, self.target, self.release(proof), verify=verify)
        self.assertEqual(admitted['state'], 'ADMITTED')
        self.journal.append_manifest(envelope())
        with self.assertRaisesRegex(ContentJournalRejected, 'STALE_OR_INVALID'):
            assert_admission(self.journal, self.restore, self.target, verify=verify)

    def test_partial_replay_never_writes_a_proof_or_admission(self):
        replay, _ = self.prepare_restore()
        with self.assertRaisesRegex(ContentJournalRejected, 'NOT_VERIFIED'):
            replay_restore(self.journal, self.restore, self.target, replay=replay, verify=lambda entry: False)
        self.assertFalse((self.restore / PROOF).exists())
        self.assertFalse((self.restore / ADMITTED).exists())

    def test_forged_backup_watermark_and_target_cannot_be_admitted(self):
        replay, verify = self.prepare_restore()
        marker = read_json(self.restore / BLOCKED)
        marker['content_checkpoint']['sequence'] = 10
        (self.restore / BLOCKED).write_text(json.dumps(marker))
        with self.assertRaisesRegex(ContentJournalRejected, 'CHECKPOINT_MISMATCH'):
            replay_restore(self.journal, self.restore, self.target, replay=replay, verify=verify)
        with self.assertRaisesRegex(ContentJournalRejected, 'TARGET_MISMATCH'):
            replay_restore(self.journal, self.restore, {**self.target, 'database': 'wrong'},
                replay=replay, verify=verify)

    def test_stale_or_incomplete_release_and_resurrected_content_are_blocked(self):
        replay, verify = self.prepare_restore()
        proof = replay_restore(self.journal, self.restore, self.target, replay=replay, verify=verify)
        approval = self.release(proof)
        approval['external_effects_reconciled'] = False
        with self.assertRaisesRegex(ContentJournalRejected, 'SEPARATE_RELEASE'):
            admit_restore(self.journal, self.restore, self.target, approval, verify=verify)
        with self.assertRaisesRegex(ContentJournalRejected, 'NOT_VERIFIED'):
            verify_restore(self.journal, self.restore, self.target, verify=lambda entry: False)
