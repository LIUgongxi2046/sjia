"""Replay trusted dispositions before a restored runtime can adopt content.

Callbacks are fixed application functions, never names supplied by a manifest.
Journal proofs only establish this supported content slice; permission revocation,
configuration qualification and external-effect reconciliation remain separate
explicit release checks. Nothing here starts services or enables network access.
"""
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from .content_journal import (ContentJournalRejected, checksum, durable_replace,
    read_json, reject, safe_path, validate_checkpoint)

BLOCKED = 'ADOPTION_BLOCKED.json'
PROOF = 'CONTENT_REPLAY_VERIFIED.json'
ADMITTED = 'ADOPTION_ADMITTED.json'


def validate_target(target):
    try:
        if set(target) != {'restore_id', 'database', 'cluster_id', 'object_root'}:
            raise ValueError()
        UUID(target['restore_id'])
        if any(not isinstance(target[key], str) or not target[key] for key in target):
            raise ValueError()
        if str(safe_path(target['object_root'])) != target['object_root']:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        reject('RESTORE_TARGET_INVALID')
    return target


def _marker(root, target):
    root = safe_path(root)
    validate_target(target)
    if safe_path(target['object_root']) != root / 'objects':
        reject('RESTORE_OBJECT_ROOT_MISMATCH')
    marker = read_json(root / BLOCKED)
    if marker.get('state') != 'ADOPTION_BLOCKED' or marker.get('target') != target:
        reject('RESTORE_TARGET_MISMATCH')
    return marker


def _validate_backup_checkpoint(checkpoint, state):
    validate_checkpoint(checkpoint)
    latest, entries = state['checkpoint'], state['entries']
    if checkpoint['journal_id'] != latest['journal_id'] or checkpoint['sequence'] > latest['sequence']:
        reject('RESTORE_BACKUP_CHECKPOINT_MISMATCH')
    expected = entries[checkpoint['sequence'] - 1]['entry_sha256'] if checkpoint['sequence'] else '0' * 64
    if checkpoint['entry_sha256'] != expected:
        reject('RESTORE_BACKUP_CHECKPOINT_MISMATCH')


def replay_restore(journal, restore_root, target, *, replay, verify):
    """Apply every known manifest, then attest the latest still-stable watermark.

    Replaying all entries handles durable intent preceding the backup snapshot's
    DB commit. Exact missing targets must be verified by the application adapter;
    this layer cannot interpret absence or a callback returning an unknown value.
    """
    root = safe_path(restore_root)
    marker = _marker(root, target)
    # The lock prevents an admitted checkpoint from racing a new deletion intent.
    with journal.locked():
        state = journal._read()
        _validate_backup_checkpoint(marker.get('content_checkpoint'), state)
        for entry in state['entries']:
            envelope = entry['envelope']
            replay(envelope)
            if verify(envelope) is not True:
                reject('RESTORE_CONTENT_REPLAY_NOT_VERIFIED')
        for entry in state['entries']:
            if verify(entry['envelope']) is not True:
                reject('RESTORE_CONTENT_REPLAY_NOT_VERIFIED')
        if journal._read()['checkpoint'] != state['checkpoint']:
            reject('RESTORE_JOURNAL_CHANGED_DURING_REPLAY')
        proof = {'schema_version': 'content-restore-proof-1', 'target': target,
            'backup_manifest_sha256': marker['backup_manifest_sha256'],
            'checkpoint': state['checkpoint'], 'verified_entries': len(state['entries']),
            'verified_at': datetime.now(timezone.utc).isoformat(),
            'state': 'CONTENT_REPLAY_VERIFIED', 'automatic_restart': False}
        durable_replace(root / PROOF, proof)
        return proof


def verify_restore(journal, restore_root, target, *, verify=None):
    root = safe_path(restore_root)
    marker = _marker(root, target)
    with journal.locked():
        state = journal._read()
        _validate_backup_checkpoint(marker.get('content_checkpoint'), state)
        proof = read_json(root / PROOF)
        if (proof.get('schema_version') != 'content-restore-proof-1'
                or proof.get('state') != 'CONTENT_REPLAY_VERIFIED'
                or proof.get('target') != target
                or proof.get('backup_manifest_sha256') != marker['backup_manifest_sha256']
                or proof.get('checkpoint') != state['checkpoint']
                or proof.get('verified_entries') != len(state['entries'])):
            reject('RESTORE_CONTENT_PROOF_STALE_OR_INVALID')
        if verify is not None:
            for entry in state['entries']:
                if verify(entry['envelope']) is not True:
                    reject('RESTORE_CONTENT_REPLAY_NOT_VERIFIED')
        if journal._read()['checkpoint'] != state['checkpoint']:
            reject('RESTORE_JOURNAL_CHANGED_DURING_VERIFICATION')
        return proof


def admit_restore(journal, restore_root, target, approval, *, verify):
    """Record a separately approved release only after actual replay verification.

    This is an operator admission record, not a claim that software can verify
    externally enforced isolation or every provider's deletion protocol.
    """
    try:
        expiry = datetime.fromisoformat(approval['expires_at'])
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise ValueError()
        if (approval['operation'] != 'admit-restored-runtime'
                or approval['operator_approved'] is not True or approval['target'] != target
                or not approval['approval_reference'] or not approval['operator_reference']):
            raise ValueError()
        for field in ('revocations_replayed', 'disabled_configuration_reconciled',
                'external_effects_reconciled', 'permissions_revalidated', 'release_qualified',
                'isolated_content_validation_complete'):
            if approval[field] is not True:
                raise ValueError()
    except (KeyError, TypeError, ValueError):
        reject('RESTORE_SEPARATE_RELEASE_APPROVAL_REQUIRED')
    proof = verify_restore(journal, restore_root, target, verify=verify)
    if approval.get('content_proof_sha256') != checksum(proof):
        reject('RESTORE_RELEASE_PROOF_MISMATCH')
    admission = {'schema_version': 'content-restore-admission-1', 'target': target,
        'content_proof_sha256': checksum(proof), 'checkpoint': proof['checkpoint'],
        'approval_reference': approval['approval_reference'],
        'operator_reference': approval['operator_reference'],
        'admitted_at': datetime.now(timezone.utc).isoformat(),
        'state': 'ADMITTED', 'automatic_restart': False}
    durable_replace(safe_path(restore_root) / ADMITTED, admission)
    # Close the race between verification and writing admission. A new intent
    # leaves an unusable stale proof; no service start is attempted in either case.
    assert_admission(journal, restore_root, target, verify=verify)
    return admission


def assert_admission(journal, restore_root, target, *, verify=None):
    proof = verify_restore(journal, restore_root, target, verify=verify)
    admission = read_json(safe_path(restore_root) / ADMITTED)
    if (admission.get('schema_version') != 'content-restore-admission-1'
            or admission.get('state') != 'ADMITTED' or admission.get('target') != target
            or admission.get('content_proof_sha256') != checksum(proof)
            or admission.get('checkpoint') != proof['checkpoint']):
        reject('RESTORE_RUNTIME_NOT_ADMITTED')
    return admission


def configured_restore_root():
    from django.conf import settings
    from .models import ContentRestoreState
    # Missing schema is deliberately not treated as a normal fresh deployment.
    # Management commands do not call this gate; serving processes must migrate.
    states = list(ContentRestoreState.objects.all()[:2])
    if len(states) > 1:
        reject('RESTORE_DATABASE_STATE_INVALID')
    state = states[0] if states else None
    object_root = safe_path(settings.FILE_OBJECT_ROOT)
    explicit = getattr(settings, 'SJIA_RESTORE_ROOT', None)
    root = safe_path(explicit) if explicit else safe_path(state.object_root).parent if state else object_root.parent
    # A marker beside restored objects cannot be bypassed by turning off the
    # optional runtime-qualification flag or leaving SJIA_RESTORE_ROOT unset.
    if (explicit or state) and object_root != root / 'objects':
        reject('RESTORE_OBJECT_ROOT_MISMATCH')
    if state:
        marker = read_json(root / BLOCKED)
        expected = {'restore_id': str(state.id), 'database': state.database_name,
            'cluster_id': state.cluster_id, 'object_root': state.object_root}
        if (marker.get('target') != expected or marker.get('content_checkpoint') != state.content_checkpoint
                or marker.get('backup_manifest_sha256') != state.backup_manifest_sha256):
            reject('RESTORE_DATABASE_STATE_MISMATCH')
    return root if explicit or state or (root / BLOCKED).exists() or (root / ADMITTED).exists() else None


def configured_target(root):
    from django.conf import settings
    from django.db import connection
    marker = read_json(root / BLOCKED)
    target = validate_target(marker.get('target'))
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), system_identifier::text FROM pg_control_system()')
        database, cluster = cursor.fetchone()
    if (database != target['database'] or cluster != target['cluster_id']
            or str(safe_path(settings.FILE_OBJECT_ROOT)) != target['object_root']):
        reject('RESTORE_RUNTIME_TARGET_MISMATCH')
    return target


def assert_configured_restore_admission(*, verify_content=True):
    root = configured_restore_root()
    if root is None:
        return None
    from .content_journal import configured_journal
    from .content_engine import verify_manifest
    target = configured_target(root)
    return assert_admission(configured_journal(), root, target,
        verify=verify_manifest if verify_content else None)


class RestoreAdmissionMiddleware:
    """A running API also refuses stale replay proofs before serving requests."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.http import JsonResponse
        try:
            assert_configured_restore_admission(verify_content=False)
        except Exception:
            return JsonResponse({'error': {'code': 'RESTORE_RUNTIME_NOT_ADMITTED'}}, status=503)
        return self.get_response(request)
