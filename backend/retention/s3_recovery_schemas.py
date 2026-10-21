from typing import Annotated, Literal
from pathlib import Path
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from files.s3_registered import S3StoredObjectRecord
from files.s3_storage import S3ObjectReference, canonical_key

Digest = Annotated[str, Field(pattern='^[0-9a-f]{64}$')]
Text = Annotated[str, Field(min_length=1)]


class RestoreDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class BackupFileReference(RestoreDocument):
    id: UUID
    patient_id: UUID
    object_key: Text
    object_version: Text
    checksum: Digest
    size_bytes: int = Field(ge=0)
    status: Literal['quarantined', 'available', 'failed', 'disposed']

    @field_validator('object_key')
    @classmethod
    def valid_key(cls, value):
        return canonical_key(value)


class S3BackupSource(RestoreDocument):
    schema_version: Literal['s3-backup-source-1'] = 's3-backup-source-1'
    binding_sha256: Digest
    database: Text
    cluster_id: Text
    access_configuration_sha256: Digest
    file_references: list[BackupFileReference]
    registrations: list[S3StoredObjectRecord]
    captured_at: AwareDatetime

    @model_validator(mode='after')
    def identities(self):
        if len({item.id for item in self.file_references}) != len(self.file_references):
            raise ValueError('S3_BACKUP_FILE_IDENTITIES_DUPLICATED')
        if (len({item.id for item in self.registrations}) != len(self.registrations)
                or len({item.reference.object_key for item in self.registrations}) != len(self.registrations)):
            raise ValueError('S3_BACKUP_REGISTRATION_IDENTITIES_DUPLICATED')
        registrations = {item.reference.object_key: item for item in self.registrations}
        for item in self.registrations:
            if item.reference.binding_sha256 != self.binding_sha256 or item.created_at > self.captured_at:
                raise ValueError('S3_BACKUP_REGISTRATION_SCOPE_MISMATCH')
            if item.disposed_at is not None and item.disposed_at > self.captured_at:
                raise ValueError('S3_BACKUP_REGISTRATION_TIME_INVALID')
        for file in self.file_references:
            known = registrations.get(file.object_key)
            if known is None:
                if file.status != 'disposed':
                    raise ValueError('S3_BACKUP_FILE_REGISTRATION_REQUIRED')
                continue
            if file.checksum != known.reference.checksum or file.size_bytes != known.reference.size_bytes:
                raise ValueError('S3_BACKUP_FILE_CONTENT_MISMATCH')
            if (file.status == 'disposed') != (known.status == 'disposed'):
                raise ValueError('S3_BACKUP_DISPOSITION_NOT_RECONCILED')
        return self


class S3AbsenceObservation(RestoreDocument):
    schema_version: Literal['s3-object-absence-1'] = 's3-object-absence-1'
    binding_sha256: Digest
    object_key: Text
    provider_request_id: Text
    verified_at: AwareDatetime
    versions: Literal[0]
    delete_markers: Literal[0]

    @field_validator('object_key')
    @classmethod
    def valid_key(cls, value):
        return canonical_key(value)


class CopiedS3Object(RestoreDocument):
    kind: Literal['copied'] = 'copied'
    source: S3StoredObjectRecord
    target: S3StoredObjectRecord

    @model_validator(mode='after')
    def matching_content(self):
        source, target = self.source.reference, self.target.reference
        if (self.source.id == self.target.id or self.source.status != 'available' or self.target.status != 'available'
                or source.binding_sha256 == target.binding_sha256
                or source.object_key != target.object_key or source.checksum != target.checksum
                or source.size_bytes != target.size_bytes):
            raise ValueError('S3_RESTORE_COPY_IDENTITY_MISMATCH')
        return self


class AbsentS3Object(RestoreDocument):
    kind: Literal['absent'] = 'absent'
    source: S3StoredObjectRecord
    target_absence: S3AbsenceObservation

    @model_validator(mode='after')
    def matching_absence(self):
        if (self.source.status != 'disposed'
                or self.source.reference.binding_sha256 == self.target_absence.binding_sha256
                or self.source.reference.object_key != self.target_absence.object_key):
            raise ValueError('S3_RESTORE_ABSENCE_IDENTITY_MISMATCH')
        return self


RestoredObject = Annotated[CopiedS3Object | AbsentS3Object, Field(discriminator='kind')]


class AbsentLegacyFile(RestoreDocument):
    source: BackupFileReference
    target_absence: S3AbsenceObservation

    @model_validator(mode='after')
    def disposed_file(self):
        if self.source.status != 'disposed' or self.source.object_key != self.target_absence.object_key:
            raise ValueError('S3_RESTORE_LEGACY_ABSENCE_MISMATCH')
        return self


class S3RestoreMapping(RestoreDocument):
    schema_version: Literal['s3-restore-mapping-1'] = 's3-restore-mapping-1'
    restore_id: UUID
    database: Text
    cluster_id: Text
    backup_manifest_sha256: Digest
    source_snapshot: S3BackupSource
    target_binding_sha256: Digest
    target_control_directory_sha256: Digest
    objects: list[RestoredObject]
    legacy_file_absences: list[AbsentLegacyFile]
    completed_at: AwareDatetime

    @model_validator(mode='after')
    def mapping_scope(self):
        if (self.restore_id.int == 0 or self.source_snapshot.binding_sha256 == self.target_binding_sha256
                or self.database == self.source_snapshot.database or self.cluster_id == self.source_snapshot.cluster_id):
            raise ValueError('S3_RESTORE_DISTINCT_TARGET_REQUIRED')
        if self.source_snapshot.captured_at > self.completed_at:
            raise ValueError('S3_RESTORE_MAPPING_TIME_INVALID')
        expected = {item.id: item for item in self.source_snapshot.registrations}
        if len(self.objects) != len(expected) or len({item.source.id for item in self.objects}) != len(expected):
            raise ValueError('S3_RESTORE_MAPPING_SCOPE_MISMATCH')
        target_ids = []
        for item in self.objects:
            if expected.get(item.source.id) != item.source:
                raise ValueError('S3_RESTORE_SOURCE_REFERENCE_MISMATCH')
            binding = item.target.reference.binding_sha256 if item.kind == 'copied' else item.target_absence.binding_sha256
            if binding != self.target_binding_sha256:
                raise ValueError('S3_RESTORE_TARGET_BINDING_MISMATCH')
            if item.kind == 'copied':
                target_ids.append(item.target.id)
                if item.target.created_at > self.completed_at:
                    raise ValueError('S3_RESTORE_MAPPING_TIME_INVALID')
            elif item.target_absence.verified_at > self.completed_at:
                raise ValueError('S3_RESTORE_MAPPING_TIME_INVALID')
        if len(set(target_ids)) != len(target_ids):
            raise ValueError('S3_RESTORE_TARGET_IDENTITIES_DUPLICATED')
        registered_keys = {item.reference.object_key for item in self.source_snapshot.registrations}
        expected_legacy = {item.id: item for item in self.source_snapshot.file_references
            if item.status == 'disposed' and item.object_key not in registered_keys}
        if (len(self.legacy_file_absences) != len(expected_legacy)
                or {item.source.id for item in self.legacy_file_absences} != set(expected_legacy)):
            raise ValueError('S3_RESTORE_LEGACY_SCOPE_MISMATCH')
        for item in self.legacy_file_absences:
            if (item.source != expected_legacy[item.source.id]
                    or item.target_absence.binding_sha256 != self.target_binding_sha256
                    or item.target_absence.verified_at > self.completed_at):
                raise ValueError('S3_RESTORE_LEGACY_ABSENCE_MISMATCH')
        return self


class S3RestoreTarget(RestoreDocument):
    schema_version: Literal['s3-restore-target-1'] = 's3-restore-target-1'
    storage_kind: Literal['s3'] = 's3'
    restore_id: UUID
    database: Text
    cluster_id: Text
    storage_binding_sha256: Digest
    control_root: Text
    mapping_sha256: Digest

    @field_validator('control_root')
    @classmethod
    def absolute_control_root(cls, value):
        path = Path(value)
        if not path.is_absolute() or path.as_posix() != value or '..' in path.parts:
            raise ValueError('S3_RESTORE_CONTROL_PATH_INVALID')
        return value


class S3PreparingTarget(S3RestoreTarget):
    schema_version: Literal['s3-restore-target-preparing-1'] = 's3-restore-target-preparing-1'
    mapping_sha256: None = None


class S3MappedAbsenceFence(RestoreDocument):
    schema_version: Literal['s3-mapped-absence-fence-1'] = 's3-mapped-absence-fence-1'
    restore_id: UUID
    mapping_sha256: Digest
    source_registration_id: UUID | None
    source_reference: S3ObjectReference | None
    source_file: BackupFileReference | None
    target_absence: S3AbsenceObservation
    prepared_at: AwareDatetime

    @model_validator(mode='after')
    def reference_scope(self):
        if self.restore_id.int == 0 or (self.source_reference is None) != (self.source_registration_id is None):
            raise ValueError('S3_MAPPED_ABSENCE_IDENTITY_INVALID')
        if (self.source_reference is None) == (self.source_file is None):
            raise ValueError('S3_MAPPED_ABSENCE_SOURCE_REQUIRED')
        key = self.source_reference.object_key if self.source_reference is not None else self.source_file.object_key
        if key != self.target_absence.object_key or self.target_absence.verified_at > self.prepared_at:
            raise ValueError('S3_MAPPED_ABSENCE_TARGET_MISMATCH')
        if self.source_reference is not None and self.source_reference.binding_sha256 == self.target_absence.binding_sha256:
            raise ValueError('S3_MAPPED_ABSENCE_TARGET_MISMATCH')
        if self.source_file is not None and self.source_file.status != 'disposed':
            raise ValueError('S3_MAPPED_ABSENCE_FILE_NOT_DISPOSED')
        return self


class S3RestoreFence(RestoreDocument):
    schema_version: Literal['s3-restore-fence-1'] = 's3-restore-fence-1'
    restore_id: UUID
    operation_id: UUID
    manifest_sha256: Digest
    mapping_sha256: Digest
    file_id: UUID
    source_kind: Literal['s3_object', 'local_object']
    source_reference: S3ObjectReference | None
    target_absence: S3AbsenceObservation
    prepared_at: AwareDatetime

    @model_validator(mode='after')
    def source_identity(self):
        if self.restore_id.int == 0 or self.operation_id.int == 0:
            raise ValueError('S3_RESTORE_FENCE_IDENTITY_INVALID')
        if (self.source_kind == 's3_object') != (self.source_reference is not None):
            raise ValueError('S3_RESTORE_FENCE_REFERENCE_REQUIRED')
        if self.source_reference is not None and (
                self.source_reference.binding_sha256 == self.target_absence.binding_sha256
                or self.source_reference.object_key != self.target_absence.object_key):
            raise ValueError('S3_RESTORE_FENCE_TARGET_MISMATCH')
        if self.target_absence.verified_at > self.prepared_at:
            raise ValueError('S3_RESTORE_FENCE_TIME_INVALID')
        return self


class BackupPayload(RestoreDocument):
    registration_id: UUID
    filename: Text
    checksum: Digest
    size_bytes: int = Field(ge=0)

    @model_validator(mode='after')
    def registered_filename(self):
        if self.filename != str(self.registration_id):
            raise ValueError('S3_BACKUP_PAYLOAD_NAME_INVALID')
        return self


class BackupEvidenceFile(RestoreDocument):
    path: Text
    checksum: Digest
    size_bytes: int = Field(ge=0)

    @field_validator('path')
    @classmethod
    def valid_path(cls, value):
        return canonical_key(value)


class BackupCheckpoint(RestoreDocument):
    schema_version: Literal['content-journal-checkpoint-1']
    journal_id: UUID
    sequence: int = Field(ge=0)
    entry_sha256: Digest

    @model_validator(mode='after')
    def initial_checkpoint(self):
        if self.sequence == 0 and self.entry_sha256 != '0' * 64:
            raise ValueError('S3_BACKUP_CHECKPOINT_INVALID')
        return self


class S3BackupPackage(RestoreDocument):
    schema_version: Literal['s3-consistent-backup-1'] = 's3-consistent-backup-1'
    state: Literal['COMPLETE'] = 'COMPLETE'
    environment: Text
    source_snapshot: S3BackupSource
    database_sha256: Digest
    payloads: list[BackupPayload]
    evidence_source_root: Text
    config_evidence: list[BackupEvidenceFile]
    content_checkpoint: BackupCheckpoint
    completed_at: AwareDatetime
    automatic_restart: Literal[False] = False

    @model_validator(mode='after')
    def payload_scope(self):
        expected = {item.id: item for item in self.source_snapshot.registrations if item.status == 'available'}
        if len(self.payloads) != len(expected) or {item.registration_id for item in self.payloads} != set(expected):
            raise ValueError('S3_BACKUP_PAYLOAD_SCOPE_MISMATCH')
        for item in self.payloads:
            reference = expected[item.registration_id].reference
            if item.checksum != reference.checksum or item.size_bytes != reference.size_bytes:
                raise ValueError('S3_BACKUP_PAYLOAD_CONTENT_MISMATCH')
        if len({item.path for item in self.config_evidence}) != len(self.config_evidence):
            raise ValueError('S3_BACKUP_EVIDENCE_PATHS_DUPLICATED')
        if self.source_snapshot.captured_at > self.completed_at:
            raise ValueError('S3_BACKUP_COMPLETION_TIME_INVALID')
        return self
