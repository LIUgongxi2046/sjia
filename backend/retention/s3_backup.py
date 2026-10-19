from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
import stat

from django.db import connection

from core.errors import CodedRuntimeError
from files.models import FileObject, S3StoredObject
from files.s3_registered import S3RegisteredStore
from retention.content_journal import canonical, checksum, durable_create, read_json, safe_path
from retention.s3_recovery_schemas import (BackupCheckpoint, BackupEvidenceFile, BackupFileReference,
    BackupPayload, S3AbsenceObservation, S3BackupPackage, S3BackupSource)


class S3RecoveryRejected(CodedRuntimeError):
    pass


def capture_source(store):
    if not isinstance(store, S3RegisteredStore) or connection.vendor != 'postgresql' or not connection.in_atomic_block:
        raise S3RecoveryRejected('S3_BACKUP_DATABASE_FREEZE_REQUIRED')
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(DISTINCT relation) FROM pg_locks WHERE pid=pg_backend_pid() "
            "AND mode='ShareLock' AND granted AND relation IN ('sjia.file_object'::regclass,'sjia.s3_stored_object'::regclass)")
        if cursor.fetchone() != (2,):
            raise S3RecoveryRejected('S3_BACKUP_DATABASE_FREEZE_REQUIRED')
        cursor.execute('SELECT current_database(), system_identifier::text FROM pg_control_system()')
        database, cluster_id = cursor.fetchone()
    store.driver.validate_bucket()
    files = [BackupFileReference(id=row.id, patient_id=row.patient_id, object_key=row.object_key,
        object_version=row.object_version, checksum=row.checksum, size_bytes=row.size_bytes, status=row.status)
        for row in FileObject.objects.order_by('id')]
    rows = S3StoredObject.objects.filter(binding_sha256=store.driver.configuration.binding_sha256).order_by('object_key')
    registrations = [store.registration_for(row.object_key) for row in rows]
    source = S3BackupSource(binding_sha256=store.driver.configuration.binding_sha256, database=database, cluster_id=cluster_id,
        access_configuration_sha256=store.driver.configuration.access_configuration_sha256,
        file_references=files, registrations=registrations, captured_at=datetime.now(timezone.utc))
    from retention.models import S3ContentRestoreState
    from retention.s3_lineage import validate_registration_history
    if S3ContentRestoreState.objects.exists():
        from retention.s3_restore import load_context
        history = load_context().lineage
    else:
        history = None
    validate_registration_history(store, history, source)
    return source


def observe_absence(driver, key):
    driver.validate_bucket()
    versions, markers, request_id = driver._key_versions(key)
    if versions or markers:
        raise S3RecoveryRejected('S3_RESTORE_TARGET_NOT_ABSENT')
    return S3AbsenceObservation(binding_sha256=driver.configuration.binding_sha256, object_key=key,
        provider_request_id=request_id, verified_at=datetime.now(timezone.utc), versions=0, delete_markers=0)


def copy_payload(source, payload_path: Path, target):
    if (source.status != 'available' or not isinstance(target, S3RegisteredStore)
            or source.reference.binding_sha256 == target.driver.configuration.binding_sha256):
        raise S3RecoveryRejected('S3_RESTORE_DISTINCT_TARGET_REQUIRED')
    if not payload_path.is_absolute() or payload_path.is_symlink() or not payload_path.is_file():
        raise S3RecoveryRejected('S3_BACKUP_PAYLOAD_FILE_REQUIRED')
    with payload_path.open('rb') as stream:
        return target.put_stream_immutable(source.reference.object_key, stream,
            checksum=source.reference.checksum, size_bytes=source.reference.size_bytes)


def file_checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_evidence(root):
    root = safe_path(root)
    if not root.is_dir():
        raise S3RecoveryRejected('S3_BACKUP_EVIDENCE_ROOT_REQUIRED')
    result = []
    for path in sorted(root.rglob('*')):
        current = path.lstat()
        if stat.S_ISDIR(current.st_mode):
            continue
        if not stat.S_ISREG(current.st_mode):
            raise S3RecoveryRejected('S3_BACKUP_EVIDENCE_FILE_INVALID')
        result.append(BackupEvidenceFile(path=path.relative_to(root).as_posix(),
            checksum=file_checksum(path), size_bytes=current.st_size))
    return result


def create_package(store, destination, evidence_root, snapshot, content_checkpoint, *, environment):
    destination, evidence_root = safe_path(destination), safe_path(evidence_root)
    if (not destination.is_dir() or destination.stat().st_mode & 0o077
            or destination.stat().st_uid != os.getuid()
            or set(path.name for path in destination.iterdir()) != {'database.dump'}):
        raise S3RecoveryRejected('S3_BACKUP_PRIVATE_DESTINATION_REQUIRED')
    current = capture_source(store)
    if current.model_dump(exclude={'captured_at'}) != snapshot.model_dump(exclude={'captured_at'}):
        raise S3RecoveryRejected('S3_BACKUP_SOURCE_CHANGED')
    from retention.s3_lineage import capture_current_lineage
    from retention.s3_lineage_schemas import S3LineageBackupPackage
    history = capture_current_lineage(store, snapshot, content_checkpoint)
    if any(left.is_relative_to(right) or right.is_relative_to(left)
            for left, right in ((destination, evidence_root), (destination, store.control_store.root))):
        raise S3RecoveryRejected('S3_BACKUP_PATHS_OVERLAP')
    objects = destination / 'objects'
    objects.mkdir(mode=0o700)
    payloads = []
    for registration in snapshot.registrations:
        if registration.status == 'disposed':
            observe_absence(store.driver, registration.reference.object_key)
            continue
        path = objects / str(registration.id)
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
            store.driver.read_to(registration.reference, stream)
            stream.flush()
            os.fsync(stream.fileno())
        payloads.append(BackupPayload(registration_id=registration.id, filename=path.name,
            checksum=registration.reference.checksum, size_bytes=registration.reference.size_bytes))
    before = inventory_evidence(evidence_root)
    shutil.copytree(evidence_root, destination / 'config-evidence', symlinks=True)
    if inventory_evidence(evidence_root) != before or inventory_evidence(destination / 'config-evidence') != before:
        raise S3RecoveryRejected('S3_BACKUP_EVIDENCE_CHANGED')
    after = capture_source(store)
    if after.model_dump(exclude={'captured_at'}) != snapshot.model_dump(exclude={'captured_at'}):
        raise S3RecoveryRejected('S3_BACKUP_SOURCE_CHANGED')
    if capture_current_lineage(store, after, content_checkpoint) != history:
        raise S3RecoveryRejected('S3_BACKUP_HISTORY_CHANGED')
    package_type = S3LineageBackupPackage if history is not None else S3BackupPackage
    history_fields = {'restore_lineage': history} if history is not None else {}
    package = package_type(environment=environment, source_snapshot=snapshot,
        database_sha256=file_checksum(destination / 'database.dump'), payloads=payloads,
        evidence_source_root=str(evidence_root), config_evidence=before,
        content_checkpoint=BackupCheckpoint.model_validate_json(canonical(content_checkpoint)),
        completed_at=datetime.now(timezone.utc), **history_fields)
    durable_create(destination / 'manifest.json', package.model_dump(mode='json'))
    durable_create(destination / 'COMPLETE.json', {'schema_version': 's3-backup-completion-1',
        'manifest_sha256': file_checksum(destination / 'manifest.json')})
    return package


def verify_package(directory):
    directory = safe_path(directory)
    completion = read_json(directory / 'COMPLETE.json')
    if (set(completion) != {'schema_version', 'manifest_sha256'}
            or completion['schema_version'] != 's3-backup-completion-1'
            or file_checksum(directory / 'manifest.json') != completion['manifest_sha256']):
        raise S3RecoveryRejected('S3_BACKUP_COMPLETION_MISMATCH')
    from retention.s3_lineage_schemas import BACKUP_PACKAGE
    package = BACKUP_PACKAGE.validate_json(canonical(read_json(directory / 'manifest.json')))
    if file_checksum(directory / 'database.dump') != package.database_sha256:
        raise S3RecoveryRejected('S3_BACKUP_DATABASE_CHECKSUM_MISMATCH')
    objects = directory / 'objects'
    if not objects.is_dir() or objects.is_symlink() or {path.name for path in objects.iterdir()} != {item.filename for item in package.payloads}:
        raise S3RecoveryRejected('S3_BACKUP_PAYLOAD_SCOPE_MISMATCH')
    for item in package.payloads:
        path = objects / item.filename
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != item.size_bytes
                or file_checksum(path) != item.checksum):
            raise S3RecoveryRejected('S3_BACKUP_PAYLOAD_CHECKSUM_MISMATCH')
    if inventory_evidence(directory / 'config-evidence') != package.config_evidence:
        raise S3RecoveryRejected('S3_BACKUP_EVIDENCE_CHECKSUM_MISMATCH')
    return package
