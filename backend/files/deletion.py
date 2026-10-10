import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class LocalDeletionRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


class DeletionJournal(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    schema_version: Literal['local-deletion-journal-2']
    operation_id: UUID = Field(strict=False)
    object_key: Annotated[str, Field(min_length=1)]
    checksum: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    size_bytes: Annotated[int, Field(ge=0)]
    device: int
    inode: int
    modified_ns: int
    changed_ns: int
    prepared_at: AwareDatetime = Field(strict=False)


class DeletionReceipt(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    schema_version: Literal['local-deletion-receipt-2']
    operation_id: UUID = Field(strict=False)
    object_key: Annotated[str, Field(min_length=1)]
    checksum: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    size_bytes: Annotated[int, Field(ge=0)]
    journal_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    prepared_at: AwareDatetime = Field(strict=False)
    verified_absent_at: AwareDatetime = Field(strict=False)
    observation: Literal['removed_now', 'recovered_absence']
    outcome: Literal['absent']


def digest(data):
    return hashlib.sha256(data).hexdigest()


def object_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def encode(model):
    return json.dumps(model.model_dump(mode='json'), ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')


def deletion_keys(operation_id):
    identity = UUID(str(operation_id))
    return f'.disposition-receipts/{identity}/journal.json', f'.disposition-receipts/{identity}/receipt.json'


def object_path(store, object_key):
    relative = Path(object_key)
    if relative.is_absolute() or '..' in relative.parts or relative.as_posix() != object_key or not relative.parts:
        raise LocalDeletionRejected('DELETION_PATH_INVALID')
    root = store.root.resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(node.is_symlink() for node in [path, *path.parents] if node.is_relative_to(root)):
        raise LocalDeletionRejected('DELETION_PATH_INVALID')
    return path


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_evidence(store, key, data):
    path = object_path(store, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    for directory in [path.parent, *path.parent.parents]:
        if directory.is_relative_to(store.root.resolve()):
            sync_directory(directory)


def verify_identity(proof, operation_id, object_key, checksum, size_bytes):
    if (proof.operation_id != UUID(str(operation_id)) or proof.object_key != object_key
            or proof.checksum != checksum or proof.size_bytes != size_bytes):
        raise LocalDeletionRejected('DELETION_IDENTITY_MISMATCH')


def read_deletion_receipt(store, *, operation_id, object_key, checksum, size_bytes):
    journal_key, receipt_key = deletion_keys(operation_id)
    receipt_path = object_path(store, receipt_key)
    if not receipt_path.is_file():
        raise LocalDeletionRejected('DELETION_RECEIPT_REQUIRED')
    data = receipt_path.read_bytes()
    receipt = DeletionReceipt.model_validate_json(data)
    verify_identity(receipt, operation_id, object_key, checksum, size_bytes)
    journal_path = object_path(store, journal_key)
    if not journal_path.is_file():
        raise LocalDeletionRejected('DELETION_JOURNAL_REQUIRED')
    journal_data = journal_path.read_bytes()
    journal = DeletionJournal.model_validate_json(journal_data)
    verify_identity(journal, operation_id, object_key, checksum, size_bytes)
    if receipt.journal_sha256 != digest(journal_data) or receipt.prepared_at != journal.prepared_at:
        raise LocalDeletionRejected('DELETION_JOURNAL_MISMATCH')
    if receipt.verified_absent_at < receipt.prepared_at or object_path(store, object_key).exists():
        raise LocalDeletionRejected('DELETION_NOT_VERIFIED')
    return receipt_key, data, receipt


def prepare_local_deletion(store, *, operation_id, object_key, checksum, size_bytes):
    target = object_path(store, object_key)
    journal_key, receipt_key = deletion_keys(operation_id)
    journal_path = object_path(store, journal_key)
    if journal_path.exists():
        journal = DeletionJournal.model_validate_json(journal_path.read_bytes())
        verify_identity(journal, operation_id, object_key, checksum, size_bytes)
    else:
        if not target.is_file():
            raise LocalDeletionRejected('DELETION_PREPARATION_REQUIRED')
        before = target.stat()
        data = target.read_bytes()
        after = target.stat()
        if object_identity(before) != object_identity(after):
            raise LocalDeletionRejected('DELETION_OBJECT_CHANGED')
        if digest(data) != checksum or len(data) != size_bytes:
            raise LocalDeletionRejected('DELETION_CHECKSUM_MISMATCH')
        journal = DeletionJournal(schema_version='local-deletion-journal-2', operation_id=UUID(str(operation_id)),
            object_key=object_key, checksum=checksum, size_bytes=size_bytes, device=after.st_dev, inode=after.st_ino,
            modified_ns=after.st_mtime_ns, changed_ns=after.st_ctime_ns, prepared_at=datetime.now(timezone.utc))
        save_evidence(store, journal_key, encode(journal))
    return journal


def delete_local_object(store, *, operation_id, object_key, checksum, size_bytes):
    target = object_path(store, object_key)
    journal_key, receipt_key = deletion_keys(operation_id)
    if object_path(store, receipt_key).exists():
        return read_deletion_receipt(store, operation_id=operation_id, object_key=object_key,
            checksum=checksum, size_bytes=size_bytes)
    journal = prepare_local_deletion(store, operation_id=operation_id, object_key=object_key,
        checksum=checksum, size_bytes=size_bytes)
    observation = 'recovered_absence'
    if target.exists():
        current = target.stat()
        data = target.read_bytes()
        if object_identity(current) != (journal.device, journal.inode, journal.size_bytes, journal.modified_ns, journal.changed_ns):
            raise LocalDeletionRejected('DELETION_OBJECT_CHANGED')
        if digest(data) != checksum or len(data) != size_bytes:
            raise LocalDeletionRejected('DELETION_CHECKSUM_MISMATCH')
        if object_identity(target.stat()) != object_identity(current):
            raise LocalDeletionRejected('DELETION_OBJECT_CHANGED')
        target.unlink()
        sync_directory(target.parent)
        observation = 'removed_now'
    if target.exists():
        raise LocalDeletionRejected('DELETION_NOT_VERIFIED')
    journal_data = object_path(store, journal_key).read_bytes()
    receipt = DeletionReceipt(schema_version='local-deletion-receipt-2', operation_id=journal.operation_id,
        object_key=object_key, checksum=checksum, size_bytes=size_bytes, journal_sha256=digest(journal_data),
        prepared_at=journal.prepared_at, verified_absent_at=datetime.now(timezone.utc), observation=observation, outcome='absent')
    save_evidence(store, receipt_key, encode(receipt))
    return read_deletion_receipt(store, operation_id=operation_id, object_key=object_key,
        checksum=checksum, size_bytes=size_bytes)
