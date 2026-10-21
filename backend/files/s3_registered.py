from datetime import datetime, timezone
import hashlib
import io
import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from django.db import connection, transaction
from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from files.deletion import object_path
from files.models import FileObject, S3StoredObject
from files.s3_storage import S3ObjectReference, S3StorageRejected, S3VersionedStore, canonical_key
from files.storage import LocalFileStore, ObjectConflict, ObjectNotFound


class S3StoredObjectRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["s3-stored-object-1"] = "s3-stored-object-1"
    id: UUID
    reference: S3ObjectReference
    status: Literal["available", "disposed"]
    created_at: AwareDatetime
    disposed_at: AwareDatetime | None

    @model_validator(mode="after")
    def validate_state(self):
        if self.status == "available" and self.disposed_at is not None:
            raise ValueError("S3_STORED_OBJECT_STATE_INVALID")
        if self.status == "disposed" and (self.disposed_at is None or self.disposed_at < self.created_at):
            raise ValueError("S3_STORED_OBJECT_STATE_INVALID")
        return self


class S3RegisteredStore:
    def __init__(self, driver: S3VersionedStore, *, control_directory: Path):
        if (not control_directory.is_dir() or control_directory.is_symlink()
                or control_directory.stat().st_mode & 0o077
                or control_directory.stat().st_uid != os.getuid()
                or not os.access(control_directory, os.R_OK | os.X_OK)):
            raise S3StorageRejected("S3_CONTROL_DIRECTORY_UNAVAILABLE")
        self.driver = driver
        self.control_store = LocalFileStore(control_directory.resolve(strict=True))
        current = self.control_store.root.stat()
        self.control_identity = (current.st_dev, current.st_ino)

    def fence_key(self, key):
        canonical_key(key)
        identity = hashlib.sha256(key.encode()).hexdigest()
        return f".s3-lifecycle/{self.driver.configuration.binding_sha256}/{identity}/journal.json"

    def _fenced(self, key):
        root = self.control_store.root
        current = root.stat()
        if (root.is_symlink() or (current.st_dev, current.st_ino) != self.control_identity
                or current.st_uid != os.getuid() or current.st_mode & 0o077
                or not os.access(root, os.R_OK | os.X_OK)):
            raise S3StorageRejected("S3_CONTROL_DIRECTORY_UNAVAILABLE")
        relative = self.fence_key(key)
        parent = (root / relative).parent
        while not parent.exists():
            if parent == root:
                raise S3StorageRejected("S3_CONTROL_DIRECTORY_UNAVAILABLE")
            parent = parent.parent
        if not parent.is_dir() or not os.access(parent, os.R_OK | os.X_OK):
            raise S3StorageRejected("S3_CONTROL_DIRECTORY_UNAVAILABLE")
        return object_path(self.control_store, relative).exists()

    def _lock(self, key):
        if connection.vendor != "postgresql" or not connection.in_atomic_block:
            raise S3StorageRejected("S3_STORAGE_TRANSACTION_REQUIRED")
        identity = hashlib.sha256((self.driver.configuration.binding_sha256 + "\x00" + key).encode()).digest()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [int.from_bytes(identity[:8], signed=True)])

    def _row(self, key, *, lock=False):
        canonical_key(key)
        rows = S3StoredObject.objects.filter(binding_sha256=self.driver.configuration.binding_sha256, object_key=key)
        return rows.select_for_update().first() if lock else rows.first()

    def _reference(self, row):
        return S3ObjectReference(binding_sha256=row.binding_sha256, object_key=row.object_key,
            version_id=row.version_id, etag=row.etag, checksum=row.checksum, size_bytes=row.size_bytes)

    def _business_identity(self, reference):
        known = FileObject.objects.filter(object_key=reference.object_key)
        if known.exclude(checksum=reference.checksum, size_bytes=reference.size_bytes).exists():
            raise S3StorageRejected("S3_BUSINESS_OBJECT_MISMATCH")
        if known.filter(status="disposed").exists():
            raise S3StorageRejected("S3_BUSINESS_OBJECT_DISPOSED")

    def registration_for(self, key):
        row = self._row(key)
        if row is None:
            raise ObjectNotFound(key)
        return S3StoredObjectRecord(id=row.id, reference=self._reference(row), status=row.status,
            created_at=row.created_at, disposed_at=row.disposed_at)

    def reference_for(self, key):
        row = self._row(key)
        if row is None or row.status != S3StoredObject.Status.AVAILABLE or self._fenced(key):
            raise ObjectNotFound(key)
        reference = self._reference(row)
        self._business_identity(reference)
        return reference

    def _save_reference(self, reference):
        if reference.binding_sha256 != self.driver.configuration.binding_sha256:
            raise S3StorageRejected("S3_BINDING_MISMATCH")
        if self._fenced(reference.object_key):
            raise S3StorageRejected("S3_OBJECT_DISPOSITION_PENDING")
        self._business_identity(reference)
        row = self._row(reference.object_key, lock=True)
        if row is not None:
            if row.status != S3StoredObject.Status.AVAILABLE:
                raise S3StorageRejected("S3_STORED_OBJECT_DISPOSED")
            if self._reference(row) != reference:
                raise S3StorageRejected("S3_STORED_OBJECT_REFERENCE_MISMATCH")
            return row
        return S3StoredObject.objects.create(binding_sha256=reference.binding_sha256,
            object_key=reference.object_key, version_id=reference.version_id, etag=reference.etag,
            checksum=reference.checksum, size_bytes=reference.size_bytes, status="available",
            created_at=datetime.now(timezone.utc))

    @transaction.atomic
    def adopt_reference(self, reference):
        self._lock(reference.object_key)
        if reference.binding_sha256 != self.driver.configuration.binding_sha256:
            raise S3StorageRejected("S3_BINDING_MISMATCH")
        if self._fenced(reference.object_key):
            raise S3StorageRejected("S3_OBJECT_DISPOSITION_PENDING")
        self._business_identity(reference)
        existing = self._row(reference.object_key, lock=True)
        if existing is not None and existing.status != S3StoredObject.Status.AVAILABLE:
            raise S3StorageRejected("S3_STORED_OBJECT_DISPOSED")
        if existing is not None and self._reference(existing) != reference:
            raise S3StorageRejected("S3_STORED_OBJECT_REFERENCE_MISMATCH")
        self.driver.validate_bucket()
        self.driver.verify_reference(reference)
        with open(os.devnull, "wb") as destination:
            self.driver.read_to(reference, destination)
        versions, markers, _ = self.driver._key_versions(reference.object_key)
        if (markers or len(versions) != 1 or versions[0]["VersionId"] != reference.version_id
                or versions[0].get("IsLatest") is not True):
            raise S3StorageRejected("S3_IMMUTABLE_KEY_VERSION_CONFLICT")
        return self._save_reference(reference)

    @transaction.atomic
    def put_immutable(self, key: str, data: bytes) -> None:
        canonical_key(key)
        self._lock(key)
        if self._fenced(key):
            raise S3StorageRejected("S3_OBJECT_DISPOSITION_PENDING")
        checksum = hashlib.sha256(data).hexdigest()
        existing = self._row(key, lock=True)
        if existing is not None:
            if existing.status != S3StoredObject.Status.AVAILABLE:
                raise S3StorageRejected("S3_STORED_OBJECT_DISPOSED")
            if existing.checksum != checksum or existing.size_bytes != len(data):
                raise ObjectConflict(key)
            self._business_identity(self._reference(existing))
        known = FileObject.objects.filter(object_key=key)
        if known.exclude(checksum=checksum, size_bytes=len(data)).exists():
            raise ObjectConflict(key)
        if known.filter(status="disposed").exists():
            raise S3StorageRejected("S3_BUSINESS_OBJECT_DISPOSED")
        reference = self.driver.put_immutable(key, io.BytesIO(data), checksum=checksum, size_bytes=len(data))
        self._save_reference(reference)

    @transaction.atomic
    def get(self, key: str) -> bytes:
        self._lock(key)
        reference = self.reference_for(key)
        destination = io.BytesIO()
        self.driver.read_to(reference, destination)
        return destination.getvalue()

    @transaction.atomic
    def read_to(self, key: str, destination):
        self._lock(key)
        reference = self.reference_for(key)
        return self.driver.read_to(reference, destination)

    @transaction.atomic
    def put_stream_immutable(self, key, stream, *, checksum, size_bytes):
        canonical_key(key)
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            raise S3StorageRejected('S3_STREAM_SIZE_INVALID')
        self._lock(key)
        if self._fenced(key):
            raise S3StorageRejected('S3_OBJECT_DISPOSITION_PENDING')
        existing = self._row(key, lock=True)
        if existing is not None:
            if existing.status != S3StoredObject.Status.AVAILABLE:
                raise S3StorageRejected('S3_STORED_OBJECT_DISPOSED')
            if existing.checksum != checksum or existing.size_bytes != size_bytes:
                raise ObjectConflict(key)
            self._business_identity(self._reference(existing))
        known = FileObject.objects.filter(object_key=key)
        if known.exclude(checksum=checksum, size_bytes=size_bytes).exists():
            raise ObjectConflict(key)
        if known.filter(status='disposed').exists():
            raise S3StorageRejected('S3_BUSINESS_OBJECT_DISPOSED')
        reference = self.driver.put_immutable(key, stream, checksum=checksum, size_bytes=size_bytes)
        self._save_reference(reference)
        return self.registration_for(key)

    @transaction.atomic
    def get_range(self, key: str, start: int, end: int) -> bytes:
        self._lock(key)
        return self.driver.get_range(self.reference_for(key), start, end)

    @transaction.atomic
    def exists(self, key: str) -> bool:
        self._lock(key)
        row = self._row(key)
        if row is None or row.status != S3StoredObject.Status.AVAILABLE or self._fenced(key):
            return False
        reference = self._reference(row)
        self._business_identity(reference)
        versions, _ = self.driver._version_inventory(reference)
        return len(versions) == 1
