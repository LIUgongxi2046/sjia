from datetime import timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, TypeAdapter, field_validator, model_validator

from retention.content_journal import checksum
from retention.s3_recovery_schemas import (BackupCheckpoint, Digest, RestoreDocument, S3BackupPackage,
    S3BackupSource, S3RestoreMapping, Text)


def validate_checkpoint_progress(previous, current):
    if (previous.journal_id != current.journal_id or previous.sequence > current.sequence
            or previous.sequence == current.sequence and previous.entry_sha256 != current.entry_sha256):
        raise ValueError('S3_LINEAGE_CHECKPOINT_DISCONTINUITY')


def validate_source_transition(parent, source):
    if (parent.database != source.database or parent.cluster_id != source.cluster_id
            or parent.target_binding_sha256 != source.binding_sha256):
        raise ValueError('S3_LINEAGE_SOURCE_IDENTITY_MISMATCH')
    files = {file.id: file for file in source.file_references}
    for previous in parent.source_snapshot.file_references:
        current = files.get(previous.id)
        if (current is None or current.model_dump(exclude={'status'}) != previous.model_dump(exclude={'status'})
                or previous.status == 'disposed' and current.status != 'disposed'):
            raise ValueError('S3_LINEAGE_FILE_HISTORY_MISMATCH')
    registrations = {item.reference.object_key: item for item in source.registrations}
    for item in parent.objects:
        current = registrations.get(item.source.reference.object_key)
        if item.kind == 'absent':
            if current is not None:
                raise ValueError('S3_LINEAGE_ABSENT_OBJECT_REGISTERED')
        elif (current is None or current.model_dump(exclude={'status', 'disposed_at'})
                != item.target.model_dump(exclude={'status', 'disposed_at'})):
            raise ValueError('S3_LINEAGE_TARGET_REGISTRATION_MISMATCH')
        elif current.status == 'disposed' and current.disposed_at < parent.completed_at:
            raise ValueError('S3_LINEAGE_DISPOSITION_TIME_INVALID')
    for item in parent.legacy_file_absences:
        if item.source.object_key in registrations:
            raise ValueError('S3_LINEAGE_ABSENT_OBJECT_REGISTERED')


class S3RestoreGeneration(RestoreDocument):
    schema_version: Literal['s3-restore-generation-1'] = 's3-restore-generation-1'
    generation: int = Field(ge=1)
    parent_restore_id: UUID | None
    parent_mapping_sha256: Digest | None
    ancestor_sha256: Digest | None
    restore_root: Text
    mapping: S3RestoreMapping
    mapping_sha256: Digest
    content_checkpoint: BackupCheckpoint
    created_at: AwareDatetime
    mapped_at: AwareDatetime

    @field_validator('created_at', 'mapped_at')
    @classmethod
    def utc_state_time(cls, value):
        return value.astimezone(timezone.utc)

    @field_validator('restore_root')
    @classmethod
    def absolute_restore_root(cls, value):
        path = Path(value)
        if not path.is_absolute() or path.as_posix() != value or '..' in path.parts:
            raise ValueError('S3_LINEAGE_RESTORE_ROOT_INVALID')
        return value

    @model_validator(mode='after')
    def generation_identity(self):
        if checksum(self.mapping.model_dump(mode='json')) != self.mapping_sha256:
            raise ValueError('S3_LINEAGE_MAPPING_DIGEST_MISMATCH')
        parent = (self.parent_restore_id, self.parent_mapping_sha256, self.ancestor_sha256)
        if (self.generation == 1 and any(value is not None for value in parent)
                or self.generation > 1 and any(value is None for value in parent)):
            raise ValueError('S3_LINEAGE_PARENT_REQUIRED')
        if self.parent_restore_id is not None and (self.parent_restore_id.int == 0 or self.parent_restore_id == self.mapping.restore_id):
            raise ValueError('S3_LINEAGE_SELF_PARENT_FORBIDDEN')
        if not self.mapping.source_snapshot.captured_at <= self.created_at <= self.mapping.completed_at <= self.mapped_at:
            raise ValueError('S3_LINEAGE_GENERATION_TIME_INVALID')
        for item in self.mapping.objects:
            observed_at = item.target.created_at if item.kind == 'copied' else item.target_absence.verified_at
            if observed_at < self.created_at:
                raise ValueError('S3_LINEAGE_OBJECT_TIME_INVALID')
        if any(item.target_absence.verified_at < self.created_at for item in self.mapping.legacy_file_absences):
            raise ValueError('S3_LINEAGE_OBJECT_TIME_INVALID')
        return self


class S3RestoreLineage(RestoreDocument):
    schema_version: Literal['s3-restore-lineage-1'] = 's3-restore-lineage-1'
    generations: list[S3RestoreGeneration] = Field(min_length=1)

    @model_validator(mode='after')
    def complete_history(self):
        identities = set()
        bindings = {self.generations[0].mapping.source_snapshot.binding_sha256}
        prefix = []
        previous = None
        for number, entry in enumerate(self.generations, start=1):
            if (entry.generation != number or entry.mapping.restore_id in identities
                    or entry.mapping.target_binding_sha256 in bindings):
                raise ValueError('S3_LINEAGE_GENERATION_ORDER_INVALID')
            identities.add(entry.mapping.restore_id)
            bindings.add(entry.mapping.target_binding_sha256)
            if previous is not None:
                if (entry.parent_restore_id != previous.mapping.restore_id
                        or entry.parent_mapping_sha256 != previous.mapping_sha256
                        or entry.ancestor_sha256 != checksum({'schema_version': self.schema_version, 'generations': prefix})):
                    raise ValueError('S3_LINEAGE_PARENT_DIGEST_MISMATCH')
                if entry.mapping.source_snapshot.captured_at < previous.mapped_at:
                    raise ValueError('S3_LINEAGE_CAPTURE_TIME_INVALID')
                validate_checkpoint_progress(previous.content_checkpoint, entry.content_checkpoint)
                validate_source_transition(previous.mapping, entry.mapping.source_snapshot)
            prefix.append(entry.model_dump(mode='json'))
            previous = entry
        return self

    def validate_source(self, source: S3BackupSource, checkpoint: BackupCheckpoint):
        previous = self.generations[-1]
        if source.binding_sha256 in {entry.mapping.source_snapshot.binding_sha256 for entry in self.generations}:
            raise ValueError('S3_LINEAGE_SOURCE_BINDING_REUSED')
        if source.captured_at < previous.mapped_at:
            raise ValueError('S3_LINEAGE_CAPTURE_TIME_INVALID')
        validate_source_transition(previous.mapping, source)
        validate_checkpoint_progress(previous.content_checkpoint, checkpoint)


class S3LineageBackupPackage(S3BackupPackage):
    schema_version: Literal['s3-consistent-backup-2'] = 's3-consistent-backup-2'
    restore_lineage: S3RestoreLineage

    @model_validator(mode='after')
    def complete_restore_history(self):
        self.restore_lineage.validate_source(self.source_snapshot, self.content_checkpoint)
        return self


BackupPackage = Annotated[S3BackupPackage | S3LineageBackupPackage, Field(discriminator='schema_version')]
BACKUP_PACKAGE = TypeAdapter(BackupPackage)
