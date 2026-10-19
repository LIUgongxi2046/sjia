from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from django.db import transaction
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from files.deletion import object_path, sync_directory
from files.models import S3StoredObject
from files.s3_registered import S3RegisteredStore
from files.s3_storage import S3DeleteResult, S3ObjectReference, S3StorageRejected
from files.storage import ObjectNotFound


class S3DeletionJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["s3-deletion-journal-1"] = "s3-deletion-journal-1"
    operation_id: UUID
    registration_id: UUID
    object_reference: S3ObjectReference
    prepared_at: AwareDatetime

    @model_validator(mode="after")
    def validate_identity(self):
        if self.operation_id.int == 0 or self.registration_id.int == 0:
            raise ValueError("S3_DELETION_IDENTITY_INVALID")
        return self


class S3DeletionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["s3-deletion-receipt-1"] = "s3-deletion-receipt-1"
    operation_id: UUID
    registration_id: UUID
    journal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prepared_at: AwareDatetime
    result: S3DeleteResult

    @model_validator(mode="after")
    def validate_result(self):
        if (self.operation_id.int == 0 or self.registration_id.int == 0
                or self.operation_id != self.result.operation_id
                or self.result.verified_at < self.prepared_at):
            raise ValueError("S3_DELETION_RECEIPT_MISMATCH")
        return self


def encode_proof(value):
    return value.model_dump_json().encode()


class S3RegisteredDeletion:
    def __init__(self, store: S3RegisteredStore):
        self.store = store

    def _keys(self, reference):
        journal = self.store.fence_key(reference.object_key)
        return journal, str(Path(journal).with_name("receipt.json"))

    def _path(self, key, reference):
        self.store._fenced(reference.object_key)
        return object_path(self.store.control_store, key)

    def _read(self, key, reference):
        path = self._path(key, reference)
        current = path.stat()
        if (not path.is_file() or current.st_uid != os.getuid() or current.st_mode & 0o077
                or current.st_size > 65536):
            raise S3StorageRejected("S3_DELETION_PROOF_UNAVAILABLE")
        return path.read_bytes()

    def _save(self, key, data, reference):
        path = self._path(key, reference)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self._path(key, reference)
        temporary = path.with_name(".pending-" + str(uuid4()))
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            if stream.write(data) != len(data):
                raise S3StorageRejected("S3_DELETION_PROOF_WRITE_INCOMPLETE")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        root = self.store.control_store.root
        for directory in (path.parent, *path.parent.parents):
            if directory.is_relative_to(root):
                sync_directory(directory)
        if self._read(key, reference) != data:
            raise S3StorageRejected("S3_DELETION_PROOF_WRITE_MISMATCH")

    def _row(self, reference, operation_id):
        if not isinstance(operation_id, UUID) or operation_id.int == 0:
            raise S3StorageRejected("S3_DELETE_OPERATION_ID_INVALID")
        if reference.binding_sha256 != self.store.driver.configuration.binding_sha256:
            raise S3StorageRejected("S3_BINDING_MISMATCH")
        self.store._lock(reference.object_key)
        self.store._fenced(reference.object_key)
        row = self.store._row(reference.object_key, lock=True)
        if row is None:
            raise ObjectNotFound(reference.object_key)
        if self.store._reference(row) != reference:
            raise S3StorageRejected("S3_DELETION_REGISTRATION_MISMATCH")
        return row

    def _journal(self, reference, operation_id, row):
        journal_key, _ = self._keys(reference)
        data = self._read(journal_key, reference)
        journal = S3DeletionJournal.model_validate_json(data)
        if (journal.operation_id != operation_id or journal.registration_id != row.id
                or journal.object_reference != reference or journal.prepared_at < row.created_at):
            raise S3StorageRejected("S3_DELETION_JOURNAL_MISMATCH")
        return data, journal

    def _prepare(self, reference, operation_id, row):
        journal_key, receipt_key = self._keys(reference)
        if self._path(journal_key, reference).exists():
            return self._journal(reference, operation_id, row)[1]
        if row.status != S3StoredObject.Status.AVAILABLE:
            raise S3StorageRejected("S3_DELETION_PREPARATION_REQUIRED")
        if self._path(receipt_key, reference).exists():
            raise S3StorageRejected("S3_DELETION_JOURNAL_REQUIRED")
        self.store.driver.validate_bucket()
        self.store.driver.verify_reference(reference)
        versions, markers, _ = self.store.driver._key_versions(reference.object_key)
        if (markers or len(versions) != 1 or versions[0]["VersionId"] != reference.version_id
                or versions[0].get("IsLatest") is not True):
            raise S3StorageRejected("S3_IMMUTABLE_KEY_VERSION_CONFLICT")
        journal = S3DeletionJournal(operation_id=operation_id, registration_id=row.id,
            object_reference=reference, prepared_at=datetime.now(timezone.utc))
        self._save(journal_key, encode_proof(journal), reference)
        return journal

    def _absent(self, reference):
        self.store.driver.validate_bucket()
        versions, markers, request_id = self.store.driver._key_versions(reference.object_key)
        if versions or markers:
            raise S3StorageRejected("S3_DELETION_ABSENCE_NOT_VERIFIED")
        return request_id

    def _receipt(self, reference, operation_id, row):
        journal_data, journal = self._journal(reference, operation_id, row)
        _, receipt_key = self._keys(reference)
        data = self._read(receipt_key, reference)
        receipt = S3DeletionReceipt.model_validate_json(data)
        if (receipt.operation_id != operation_id or receipt.registration_id != row.id
                or receipt.prepared_at != journal.prepared_at
                or receipt.journal_sha256 != hashlib.sha256(journal_data).hexdigest()
                or receipt.result.object_reference != reference):
            raise S3StorageRejected("S3_DELETION_RECEIPT_MISMATCH")
        if row.status == S3StoredObject.Status.DISPOSED and row.disposed_at != receipt.result.verified_at:
            raise S3StorageRejected("S3_DELETION_DISPOSED_TIME_MISMATCH")
        self._absent(reference)
        return receipt_key, data, receipt

    @transaction.atomic
    def prepare(self, reference: S3ObjectReference, *, operation_id: UUID):
        row = self._row(reference, operation_id)
        return self._prepare(reference, operation_id, row)

    @transaction.atomic
    def read_receipt(self, reference: S3ObjectReference, *, operation_id: UUID):
        row = self._row(reference, operation_id)
        return self._receipt(reference, operation_id, row)

    @transaction.atomic
    def delete(self, reference: S3ObjectReference, *, operation_id: UUID):
        row = self._row(reference, operation_id)
        _, receipt_key = self._keys(reference)
        journal = self._prepare(reference, operation_id, row)
        if not self._path(receipt_key, reference).exists():
            result = self.store.driver.delete_version(reference, operation_id=operation_id)
            self._absent(reference)
            journal_data, _ = self._journal(reference, operation_id, row)
            receipt = S3DeletionReceipt(operation_id=operation_id, registration_id=row.id,
                journal_sha256=hashlib.sha256(journal_data).hexdigest(),
                prepared_at=journal.prepared_at, result=result)
            self._save(receipt_key, encode_proof(receipt), reference)
        answer = self._receipt(reference, operation_id, row)
        if row.status == S3StoredObject.Status.AVAILABLE:
            S3StoredObject.objects.filter(id=row.id, status="available").update(
                status="disposed", disposed_at=answer[2].result.verified_at)
        return answer
