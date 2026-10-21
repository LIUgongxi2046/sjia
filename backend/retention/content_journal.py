"""Durable exact-object dispositions, independent of database/object backups.

The checkpoint is a separately protected monotonic trust anchor, not a second
copy inside a backup. Restoring/rolling back both stores defeats that trust model;
operators must provision and protect them independently. This module does not
create credentials, choose retention periods or authorize a disposition.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import local
from uuid import UUID, uuid4

ZERO = '0' * 64
RECORD_NAME = re.compile(r'^[0-9]{20}\.json$')
DIGEST = re.compile(r'^[0-9a-f]{64}$')
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
_HELD_LOCKS = local()


def _after_fork_child():
    # A child must never inherit logical ownership or keep the parent's lock
    # pinned. Close its inherited descriptor copies without unlocking the OFD.
    global _HELD_LOCKS
    for descriptor, _ in getattr(_HELD_LOCKS, 'descriptors', {}).values():
        os.close(descriptor)
    _HELD_LOCKS = local()


os.register_at_fork(after_in_child=_after_fork_child)


class ContentJournalRejected(RuntimeError):
    def __init__(self, reason_code):
        self.reason_code = reason_code
        super().__init__(reason_code)


def reject(code):
    raise ContentJournalRejected(code)


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
            ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError):
        reject('CONTENT_JOURNAL_INVALID_JSON')


def checksum(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_path(value):
    if not value:
        reject('CONTENT_JOURNAL_CONFIGURATION_REQUIRED')
    path = Path(value).absolute()
    if any(node.is_symlink() for node in (path, *path.parents)):
        reject('CONTENT_JOURNAL_UNSAFE_PATH')
    return path.resolve()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                reject('CONTENT_JOURNAL_UNSAFE_FILE')
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
        if len(data) > MAX_DOCUMENT_BYTES:
            reject('CONTENT_JOURNAL_DOCUMENT_TOO_LARGE')
        # Duplicate keys would make audit representations ambiguous.
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    reject('CONTENT_JOURNAL_DUPLICATE_KEY')
                result[key] = value
            return result
        return json.loads(data, object_pairs_hook=pairs)
    except ContentJournalRejected:
        raise
    except (OSError, ValueError, TypeError):
        reject('CONTENT_JOURNAL_DOCUMENT_UNAVAILABLE')


def durable_create(path, value):
    data = canonical(value)
    if len(data) > MAX_DOCUMENT_BYTES:
        reject('CONTENT_JOURNAL_DOCUMENT_TOO_LARGE')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def durable_replace(path, value):
    temporary = path.with_name('.' + path.name + '.' + str(uuid4()))
    durable_create(temporary, value)
    try:
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def validate_envelope(envelope):
    from .content_schemas import ContentApproval, parse_manifest
    required = {'schema_version', 'plan_id', 'manifest_sha256', 'manifest', 'approval'}
    try:
        if not isinstance(envelope, dict) or set(envelope) != required:
            raise ValueError()
        if envelope['schema_version'] != 'content-disposition-journal-1':
            raise ValueError()
        UUID(envelope['plan_id'])
        manifest = parse_manifest(envelope['manifest'])
        approval = ContentApproval.model_validate(envelope['approval'])
        digest = checksum(envelope['manifest'])
        if (envelope['manifest_sha256'] != digest or approval.manifest_sha256 != digest
                or approval.policy_sha256 != manifest.policy_sha256):
            raise ValueError()
        # Round-trip through JSON before storing: no arbitrary Python objects.
        canonical(envelope)
    except (ValueError, TypeError, KeyError):
        reject('CONTENT_JOURNAL_MANIFEST_INVALID')
    return envelope


def validate_checkpoint(value):
    try:
        if set(value) != {'schema_version', 'journal_id', 'sequence', 'entry_sha256'}:
            raise ValueError()
        UUID(value['journal_id'])
        if (value['schema_version'] != 'content-journal-checkpoint-1'
                or type(value['sequence']) is not int or value['sequence'] < 0
                or not DIGEST.fullmatch(value['entry_sha256'])
                or (value['sequence'] == 0 and value['entry_sha256'] != ZERO)):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        reject('CONTENT_JOURNAL_CHECKPOINT_INVALID')
    return value


class ContentJournal:
    def __init__(self, root, checkpoint_path, *, forbidden_roots=()):
        self.root, self.checkpoint_path = safe_path(root), safe_path(checkpoint_path)
        # Separate directory trees allow independently protected trust storage.
        if (self.checkpoint_path.is_relative_to(self.root)
                or self.root.is_relative_to(self.checkpoint_path.parent)):
            reject('CONTENT_JOURNAL_CHECKPOINT_NOT_INDEPENDENT')
        for value in forbidden_roots:
            if not value:
                continue
            other = safe_path(value)
            if any(left.is_relative_to(other) or other.is_relative_to(left)
                   for left in (self.root, self.checkpoint_path.parent)):
                reject('CONTENT_JOURNAL_OVERLAPS_RESTORABLE_STORE')

    @contextmanager
    def locked(self):
        if safe_path(self.root) != self.root or safe_path(self.checkpoint_path) != self.checkpoint_path:
            reject('CONTENT_JOURNAL_UNSAFE_PATH')
        if not self.root.is_dir() or not self.checkpoint_path.parent.is_dir():
            reject('CONTENT_JOURNAL_CONFIGURATION_REQUIRED')
        # Verification inside a restore callback may construct another journal
        # instance. Reuse this thread's existing OFD instead of deadlocking on
        # a second flock. Other threads/processes still acquire the actual lock.
        held = getattr(_HELD_LOCKS, 'descriptors', None)
        if held is None:
            held = _HELD_LOCKS.descriptors = {}
        identity = (os.getpid(), str(self.root))
        if identity in held:
            descriptor, checkpoint = held[identity]
            if checkpoint != str(self.checkpoint_path):
                reject('CONTENT_JOURNAL_NESTED_CHECKPOINT_MISMATCH')
            current = (self.root / '.lock').stat(follow_symlinks=False)
            existing = os.fstat(descriptor)
            if (not stat.S_ISREG(current.st_mode)
                    or (current.st_dev, current.st_ino) != (existing.st_dev, existing.st_ino)):
                reject('CONTENT_JOURNAL_LOCK_IDENTITY_CHANGED')
            yield
            return
        # All entry/checkpoint operations take the same interprocess lock.
        fd = os.open(self.root / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                reject('CONTENT_JOURNAL_UNSAFE_FILE')
            fcntl.flock(fd, fcntl.LOCK_EX)
            held[identity] = (fd, str(self.checkpoint_path))
            yield
        finally:
            held.pop(identity, None)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def initialize(self):
        """Explicit provisioning on existing protected, empty directories only."""
        with self.locked():
            if self.checkpoint_path.exists() or any(path.name != '.lock' for path in self.root.iterdir()):
                reject('CONTENT_JOURNAL_ALREADY_INITIALIZED')
            identity = str(uuid4())
            durable_create(self.root / 'identity.json', {'schema_version': 'content-journal-store-1',
                'journal_id': identity})
            checkpoint = {'schema_version': 'content-journal-checkpoint-1', 'journal_id': identity,
                'sequence': 0, 'entry_sha256': ZERO}
            durable_create(self.checkpoint_path, checkpoint)
            return checkpoint

    def _read(self, *, allow_uncheckpointed=False):
        anchor = validate_checkpoint(read_json(self.checkpoint_path))
        if read_json(self.root / 'identity.json') != {'schema_version': 'content-journal-store-1',
                'journal_id': anchor['journal_id']}:
            reject('CONTENT_JOURNAL_IDENTITY_MISMATCH')
        paths = []
        for path in self.root.iterdir():
            if path.name in ('.lock', 'identity.json'):
                continue
            if not RECORD_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                reject('CONTENT_JOURNAL_UNRECOGNIZED_ENTRY')
            paths.append(path)
        entries, previous = [], ZERO
        for sequence, path in enumerate(sorted(paths), 1):
            entry = read_json(path)
            fields = {'schema_version', 'journal_id', 'sequence', 'previous_sha256',
                'recorded_at', 'envelope', 'entry_sha256'}
            if (set(entry) != fields or path.name != f'{sequence:020d}.json'
                    or entry['schema_version'] != 'content-journal-entry-1'
                    or entry['journal_id'] != anchor['journal_id']
                    or type(entry['sequence']) is not int or entry['sequence'] != sequence
                    or entry['previous_sha256'] != previous):
                reject('CONTENT_JOURNAL_CHAIN_BROKEN')
            try:
                timestamp = datetime.fromisoformat(entry['recorded_at'])
                if timestamp.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError):
                reject('CONTENT_JOURNAL_CHAIN_BROKEN')
            validate_envelope(entry['envelope'])
            if checksum({key: value for key, value in entry.items() if key != 'entry_sha256'}) != entry['entry_sha256']:
                reject('CONTENT_JOURNAL_CHAIN_BROKEN')
            previous = entry['entry_sha256']
            entries.append(entry)
        if len(entries) < anchor['sequence']:
            reject('CONTENT_JOURNAL_TRUNCATED')
        expected = entries[anchor['sequence'] - 1]['entry_sha256'] if anchor['sequence'] else ZERO
        if expected != anchor['entry_sha256']:
            reject('CONTENT_JOURNAL_CHECKPOINT_MISMATCH')
        if len(entries) != anchor['sequence'] and not allow_uncheckpointed:
            reject('CONTENT_JOURNAL_UNCHECKPOINTED_TAIL')
        if len({entry['envelope']['plan_id'] for entry in entries}) != len(entries):
            reject('CONTENT_JOURNAL_DUPLICATE_PLAN')
        return {'checkpoint': anchor, 'entries': entries}

    def verify(self):
        with self.locked():
            return self._read()

    def append_manifest(self, envelope):
        validate_envelope(envelope)
        with self.locked():
            state = self._read(allow_uncheckpointed=True)
            anchor, entries = state['checkpoint'], state['entries']
            extra = entries[anchor['sequence']:]
            if extra:
                # A crash after durable append but before checkpoint replacement
                # can only be continued by the exact original authorized intent.
                if len(extra) != 1 or extra[0]['envelope'] != envelope:
                    reject('CONTENT_JOURNAL_UNCHECKPOINTED_TAIL')
                entry = extra[0]
            else:
                for entry in entries:
                    if entry['envelope']['plan_id'] == envelope['plan_id']:
                        if entry['envelope'] != envelope:
                            reject('CONTENT_JOURNAL_PLAN_CONFLICT')
                        return entry
                entry = {'schema_version': 'content-journal-entry-1', 'journal_id': anchor['journal_id'],
                    'sequence': anchor['sequence'] + 1, 'previous_sha256': anchor['entry_sha256'],
                    'recorded_at': datetime.now(timezone.utc).isoformat(), 'envelope': envelope}
                entry['entry_sha256'] = checksum(entry)
                durable_create(self.root / f'{entry["sequence"]:020d}.json', entry)
            durable_replace(self.checkpoint_path, {**anchor, 'sequence': entry['sequence'],
                'entry_sha256': entry['entry_sha256']})
            return entry


def configured_journal():
    from django.conf import settings
    from files.storage_factory import storage_selection
    control_root = storage_selection.store.control_store.root if storage_selection.kind == 's3' else None
    return ContentJournal(getattr(settings, 'SJIA_CONTENT_JOURNAL_ROOT', None),
        getattr(settings, 'SJIA_CONTENT_CHECKPOINT_PATH', None),
        forbidden_roots=(getattr(settings, 'FILE_OBJECT_ROOT', None),
            getattr(settings, 'CONFIG_EVIDENCE_ROOT', None),
            getattr(settings, 'SJIA_RESTORE_ROOT', None), control_root))


def append_manifest(envelope):
    return configured_journal().append_manifest(envelope)


def verify_journal():
    return configured_journal().verify()
