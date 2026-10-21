from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import UUID, uuid5

from django.db import connection, transaction

from files.deletion import object_path
from files.models import FileObject, S3StoredObject
from files.s3_deletion import S3RegisteredDeletion
from files.s3_registered import S3RegisteredStore, S3StoredObjectRecord
from files.storage_factory import storage_selection
from retention.content_journal import canonical, checksum, durable_create, durable_replace, read_json, safe_path
from retention.models import ContentRestoreState, S3ContentRestoreState
from retention.s3_backup import S3RecoveryRejected, copy_payload, observe_absence, verify_package, file_checksum
from retention.s3_recovery_schemas import (AbsentLegacyFile, AbsentS3Object, BackupFileReference,
    CopiedS3Object, S3MappedAbsenceFence, S3PreparingTarget, S3RestoreFence, S3RestoreMapping, S3RestoreTarget)
from retention.s3_lineage_schemas import S3RestoreLineage

MAPPING_FILE = 'S3_RESTORE_MAPPING.json'
BLOCKED_FILE = 'ADOPTION_BLOCKED.json'


def reject(code):
    raise S3RecoveryRejected(code)


def database_identity():
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), system_identifier::text FROM pg_control_system()')
        return cursor.fetchone()


def selected_store():
    if storage_selection.kind != 's3' or not isinstance(storage_selection.store, S3RegisteredStore):
        reject('RESTORE_S3_STORAGE_REQUIRED')
    return storage_selection.store


def target_document(state):
    if state.state != 'mapped':
        reject('RESTORE_S3_MAPPING_NOT_READY')
    return S3RestoreTarget(restore_id=state.id, database=state.database_name, cluster_id=state.cluster_id,
        storage_binding_sha256=state.target_binding_sha256, control_root=str(safe_path(state.restore_root) / 's3-control'),
        mapping_sha256=state.mapping_sha256).model_dump(mode='json')


def check_physical_target(root, source_snapshot):
    root = safe_path(root)
    store = selected_store()
    database, cluster_id = database_identity()
    if (database == source_snapshot.database or cluster_id == source_snapshot.cluster_id
            or store.driver.configuration.binding_sha256 == source_snapshot.binding_sha256):
        reject('RESTORE_S3_DISTINCT_TARGET_REQUIRED')
    if (not root.is_dir() or root.stat().st_mode & 0o077 or root.stat().st_uid != os.getuid()
            or store.control_store.root != root / 's3-control'):
        reject('RESTORE_S3_CONTROL_ROOT_MISMATCH')
    store.driver.validate_bucket()
    return store, database, cluster_id


@transaction.atomic
def begin_restore(package_directory, root, *, restore_id):
    package = verify_package(package_directory)
    store, database, cluster_id = check_physical_target(root, package.source_snapshot)
    if ContentRestoreState.objects.exists():
        reject('RESTORE_STORAGE_STATE_CONFLICT')
    from retention.s3_lineage import LINEAGE_FILE, validate_database_lineage, validate_registration_history
    from retention.s3_lineage_schemas import S3LineageBackupPackage
    history = package.restore_lineage if isinstance(package, S3LineageBackupPackage) else None
    validate_database_lineage(history, list(S3ContentRestoreState.objects.order_by('generation')))
    marker = read_json(safe_path(root) / BLOCKED_FILE)
    expected_target = S3PreparingTarget(restore_id=restore_id, database=database, cluster_id=cluster_id,
        storage_binding_sha256=store.driver.configuration.binding_sha256,
        control_root=str(store.control_store.root)).model_dump(mode='json')
    if (marker.get('state') != 'ADOPTION_BLOCKED' or marker.get('target') != expected_target
            or marker.get('backup_manifest_sha256') != file_checksum(Path(package_directory) / 'manifest.json')
            or marker.get('content_checkpoint') != package.content_checkpoint.model_dump(mode='json')):
        reject('RESTORE_S3_PREPARING_MARKER_REQUIRED')
    actual_files = [BackupFileReference(id=row.id, patient_id=row.patient_id, object_key=row.object_key,
        object_version=row.object_version, checksum=row.checksum, size_bytes=row.size_bytes, status=row.status)
        for row in FileObject.objects.order_by('id')]
    if actual_files != package.source_snapshot.file_references:
        reject('RESTORE_S3_FILE_INVENTORY_MISMATCH')
    originals = [S3StoredObjectRecord(id=row.id, reference=store._reference(row), status=row.status,
        created_at=row.created_at, disposed_at=row.disposed_at) for row in S3StoredObject.objects.filter(
            binding_sha256=package.source_snapshot.binding_sha256).order_by('object_key')]
    if originals != package.source_snapshot.registrations:
        reject('RESTORE_S3_SOURCE_REGISTRATIONS_MISMATCH')
    validate_registration_history(store, history, package.source_snapshot)
    if (safe_path(root) / LINEAGE_FILE).exists():
        reject('RESTORE_S3_HISTORY_DESTINATION_EXISTS')
    parent = history.generations[-1] if history is not None else None
    state = S3ContentRestoreState.objects.create(id=restore_id,
        generation=parent.generation + 1 if parent is not None else 1,
        parent_id=parent.mapping.restore_id if parent is not None else None,
        parent_mapping_sha256=parent.mapping_sha256 if parent is not None else None,
        ancestor_sha256=checksum(history.model_dump(mode='json')) if history is not None else None,
        database_name=database, cluster_id=cluster_id,
        restore_root=str(safe_path(root)), source_binding_sha256=package.source_snapshot.binding_sha256,
        target_binding_sha256=store.driver.configuration.binding_sha256,
        source_snapshot_sha256=checksum(package.source_snapshot.model_dump(mode='json')),
        backup_manifest_sha256=file_checksum(Path(package_directory) / 'manifest.json'),
        content_checkpoint=package.content_checkpoint.model_dump(mode='json'), created_at=datetime.now(timezone.utc))
    if history is not None:
        durable_create(safe_path(root) / LINEAGE_FILE, history.model_dump(mode='json'))
    return state


def _write_control_fence(store, fence):
    key = fence.target_absence.object_key
    with transaction.atomic():
        store._lock(key)
        store._fenced(key)
        path = object_path(store.control_store, store.fence_key(key))
        if path.exists():
            existing = type(fence).model_validate_json(canonical(read_json(path)))
            if existing.model_dump(exclude={'prepared_at'}) != fence.model_dump(exclude={'prepared_at'}):
                reject('RESTORE_S3_FENCE_IDENTITY_MISMATCH')
            return existing
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        durable_create(path, fence.model_dump(mode='json'))
        return fence


def _mapped_fences(mapping):
    mapping_sha = checksum(mapping.model_dump(mode='json'))
    for item in mapping.objects:
        if item.kind == 'absent':
            yield S3MappedAbsenceFence(restore_id=mapping.restore_id, mapping_sha256=mapping_sha,
                source_registration_id=item.source.id, source_reference=item.source.reference, source_file=None,
                target_absence=item.target_absence, prepared_at=datetime.now(timezone.utc))
    for item in mapping.legacy_file_absences:
        yield S3MappedAbsenceFence(restore_id=mapping.restore_id, mapping_sha256=mapping_sha,
            source_registration_id=None, source_reference=None, source_file=item.source,
            target_absence=item.target_absence, prepared_at=datetime.now(timezone.utc))


def seal_mapping(state_id, package_directory):
    state = S3ContentRestoreState.objects.get(id=state_id, state='preparing')
    package = verify_package(package_directory)
    root = safe_path(state.restore_root)
    store, database, cluster_id = check_physical_target(root, package.source_snapshot)
    if (database != state.database_name or cluster_id != state.cluster_id
            or checksum(package.source_snapshot.model_dump(mode='json')) != state.source_snapshot_sha256
            or file_checksum(Path(package_directory) / 'manifest.json') != state.backup_manifest_sha256):
        reject('RESTORE_S3_BACKUP_IDENTITY_MISMATCH')
    from retention.s3_lineage import ancestor_lineage, validate_database_lineage
    from retention.s3_lineage_schemas import S3LineageBackupPackage
    history = ancestor_lineage(state)
    expected_history = package.restore_lineage if isinstance(package, S3LineageBackupPackage) else None
    if history != expected_history:
        reject('RESTORE_S3_BACKUP_HISTORY_MISMATCH')
    validate_database_lineage(history, list(S3ContentRestoreState.objects.exclude(id=state.id).order_by('generation')))
    path = root / MAPPING_FILE
    if path.exists():
        mapping = S3RestoreMapping.model_validate_json(canonical(read_json(path)))
        if (mapping.restore_id != state.id or mapping.source_snapshot != package.source_snapshot
                or mapping.target_binding_sha256 != state.target_binding_sha256):
            reject('RESTORE_S3_MAPPING_IDENTITY_MISMATCH')
    else:
        payloads = {item.registration_id: item for item in package.payloads}
        objects = []
        for source in package.source_snapshot.registrations:
            if source.status == 'available':
                target = copy_payload(source, Path(package_directory) / 'objects' / payloads[source.id].filename, store)
                objects.append(CopiedS3Object(source=source, target=target))
            else:
                objects.append(AbsentS3Object(source=source,
                    target_absence=observe_absence(store.driver, source.reference.object_key)))
        registered_keys = {item.reference.object_key for item in package.source_snapshot.registrations}
        legacy = [AbsentLegacyFile(source=file, target_absence=observe_absence(store.driver, file.object_key))
            for file in package.source_snapshot.file_references if file.status == 'disposed' and file.object_key not in registered_keys]
        mapping = S3RestoreMapping(restore_id=state.id, database=database, cluster_id=cluster_id,
            backup_manifest_sha256=state.backup_manifest_sha256, source_snapshot=package.source_snapshot,
            target_binding_sha256=state.target_binding_sha256,
            target_control_directory_sha256=storage_selection.declaration()['control_directory_sha256'],
            objects=objects, legacy_file_absences=legacy, completed_at=datetime.now(timezone.utc))
        durable_create(path, mapping.model_dump(mode='json'))
    for fence in _mapped_fences(mapping):
        _write_control_fence(store, fence)
    mapping_sha = checksum(mapping.model_dump(mode='json'))
    target = S3RestoreTarget(restore_id=state.id, database=database, cluster_id=cluster_id,
        storage_binding_sha256=state.target_binding_sha256, control_root=str(store.control_store.root),
        mapping_sha256=mapping_sha).model_dump(mode='json')
    marker = {'state': 'ADOPTION_BLOCKED', 'target': target,
        'backup_manifest_sha256': state.backup_manifest_sha256, 'content_checkpoint': state.content_checkpoint,
        'replay_deletion_revocation_disabled_configuration_required': True,
        'external_effect_reconciliation_required': True, 'automatic_restart': False}
    durable_replace(root / BLOCKED_FILE, marker)
    with transaction.atomic():
        row = S3ContentRestoreState.objects.select_for_update().get(id=state.id, state='preparing')
        row.state = 'mapped'
        row.mapping_sha256 = mapping_sha
        row.mapped_at = datetime.now(timezone.utc)
        row.save(update_fields=['state', 'mapping_sha256', 'mapped_at'])
    return mapping


@dataclass(frozen=True)
class S3RestoreContext:
    state: S3ContentRestoreState
    mapping: S3RestoreMapping
    store: S3RegisteredStore
    lineage: S3RestoreLineage

    def origin_snapshot(self, item):
        if item.kind == 'local_object':
            return self.lineage.generations[0].mapping.source_snapshot
        source = next((entry.mapping.source_snapshot for entry in self.lineage.generations
            if entry.mapping.source_snapshot.binding_sha256 == item.storage_reference.binding_sha256), None)
        if source is None:
            reject('RESTORE_S3_SOURCE_BINDING_MISMATCH')
        return source

    def resolve(self, item):
        origin = self.origin_snapshot(item)
        original = next((file for file in origin.file_references if file.id == item.file_id), None)
        if original is None:
            if (any(file.id == item.file_id or file.object_key == item.object_key
                    for entry in self.lineage.generations for file in entry.mapping.source_snapshot.file_references)
                    or any(row.reference.object_key == item.object_key for entry in self.lineage.generations
                        for row in entry.mapping.source_snapshot.registrations)
                    or FileObject.objects.filter(id=item.file_id).exists()
                    or FileObject.objects.filter(object_key=item.object_key).exists()
                    or self.store._row(item.object_key) is not None):
                reject('RESTORE_S3_UNSEEN_OBJECT_SCOPE_CONFLICT')
            return None
        if (original.object_key != item.object_key or original.object_version != item.object_version
                or original.checksum != item.checksum or original.size_bytes != item.size_bytes):
            reject('RESTORE_S3_FILE_REFERENCE_MISMATCH')
        if item.kind == 's3_object':
            registration = next((row for row in origin.registrations if row.reference.object_key == item.object_key), None)
            if registration is None:
                reject('RESTORE_S3_SOURCE_REFERENCE_MISSING')
            if registration.reference != item.storage_reference:
                reject('RESTORE_S3_SOURCE_REFERENCE_MISMATCH')
        known = next((file for file in self.mapping.source_snapshot.file_references if file.id == item.file_id), None)
        if known is None or known.model_dump(exclude={'status'}) != original.model_dump(exclude={'status'}):
            reject('RESTORE_S3_FILE_HISTORY_MISMATCH')
        if (known.object_key != item.object_key or known.object_version != item.object_version
                or known.checksum != item.checksum or known.size_bytes != item.size_bytes):
            reject('RESTORE_S3_FILE_REFERENCE_MISMATCH')
        current = FileObject.objects.filter(id=known.id).first()
        if current is None:
            reject('RESTORE_S3_FILE_METADATA_MISSING')
        if (current.patient_id != known.patient_id or current.object_key != known.object_key
                or current.object_version != known.object_version or current.checksum != known.checksum
                or current.size_bytes != known.size_bytes or known.status == 'disposed' and current.status != 'disposed'):
            reject('RESTORE_S3_FILE_REFERENCE_MISMATCH')
        native = next((entry for entry in self.mapping.objects if entry.source.reference.object_key == item.object_key), None)
        if native is not None:
            return native
        legacy = next((entry for entry in self.mapping.legacy_file_absences if entry.source.id == item.file_id), None)
        if legacy is None:
            reject('RESTORE_S3_LEGACY_REFERENCE_MISSING')
        return legacy

    def validate_manifest(self, manifest):
        if (manifest.schema_version == 'content-disposition-manifest-2'
                and manifest.storage_binding_sha256 not in {entry.mapping.source_snapshot.binding_sha256
                    for entry in self.lineage.generations}):
            reject('RESTORE_S3_SOURCE_BINDING_MISMATCH')
        for item in manifest.items:
            if item.kind != 'database':
                known = next((file for file in self.origin_snapshot(item).file_references if file.id == item.file_id), None)
                if known is not None and known.patient_id != manifest.patient_id:
                    reject('RESTORE_S3_PATIENT_SCOPE_MISMATCH')
                self.resolve(item)

    def operation_id(self, item):
        return uuid5(item.operation_id, f'restore:{self.state.id}')

    def _mapped_fence(self, entry):
        if isinstance(entry, AbsentS3Object):
            return S3MappedAbsenceFence(restore_id=self.state.id, mapping_sha256=self.state.mapping_sha256,
                source_registration_id=entry.source.id, source_reference=entry.source.reference, source_file=None,
                target_absence=entry.target_absence, prepared_at=datetime.now(timezone.utc))
        return S3MappedAbsenceFence(restore_id=self.state.id, mapping_sha256=self.state.mapping_sha256,
            source_registration_id=None, source_reference=None, source_file=entry.source,
            target_absence=entry.target_absence, prepared_at=datetime.now(timezone.utc))

    def _read_fence(self, item, model):
        self.store._fenced(item.object_key)
        path = object_path(self.store.control_store, self.store.fence_key(item.object_key))
        return model.model_validate_json(canonical(read_json(path)))

    def _check_unseen_fence(self, item, manifest_sha256):
        fence = self._read_fence(item, S3RestoreFence)
        if (fence.restore_id != self.state.id or fence.operation_id != self.operation_id(item)
                or fence.manifest_sha256 != manifest_sha256 or fence.mapping_sha256 != self.state.mapping_sha256
                or fence.file_id != item.file_id or fence.source_kind != item.kind
                or fence.source_reference != (item.storage_reference if item.kind == 's3_object' else None)
                or fence.target_absence.object_key != item.object_key
                or fence.target_absence.binding_sha256 != self.state.target_binding_sha256):
            reject('RESTORE_S3_FENCE_IDENTITY_MISMATCH')
        return fence

    def apply_item(self, item, manifest_sha256):
        entry = self.resolve(item)
        if isinstance(entry, CopiedS3Object):
            current = self.store.registration_for(item.object_key)
            if (current.id != entry.target.id or current.reference != entry.target.reference
                    or current.created_at != entry.target.created_at):
                reject('RESTORE_S3_TARGET_REGISTRATION_MISMATCH')
            return S3RegisteredDeletion(self.store).delete(entry.target.reference, operation_id=self.operation_id(item))
        if entry is not None:
            expected = self._mapped_fence(entry)
            actual = self._read_fence(item, S3MappedAbsenceFence)
            if actual.model_dump(exclude={'prepared_at'}) != expected.model_dump(exclude={'prepared_at'}):
                reject('RESTORE_S3_FENCE_IDENTITY_MISMATCH')
            observe_absence(self.store.driver, item.object_key)
            return actual
        with transaction.atomic():
            self.store._lock(item.object_key)
            path = object_path(self.store.control_store, self.store.fence_key(item.object_key))
            if path.exists():
                fence = self._check_unseen_fence(item, manifest_sha256)
                observe_absence(self.store.driver, item.object_key)
                return fence
            absence = observe_absence(self.store.driver, item.object_key)
            fence = S3RestoreFence(restore_id=self.state.id, operation_id=self.operation_id(item),
                manifest_sha256=manifest_sha256, mapping_sha256=self.state.mapping_sha256,
                file_id=item.file_id, source_kind=item.kind,
                source_reference=item.storage_reference if item.kind == 's3_object' else None,
                target_absence=absence, prepared_at=datetime.now(timezone.utc))
            return _write_control_fence(self.store, fence)

    def verify_item(self, item, manifest_sha256):
        entry = self.resolve(item)
        if isinstance(entry, CopiedS3Object):
            current = self.store.registration_for(item.object_key)
            if (current.id != entry.target.id or current.reference != entry.target.reference
                    or current.status != 'disposed' or current.created_at != entry.target.created_at):
                return False
            S3RegisteredDeletion(self.store).read_receipt(entry.target.reference, operation_id=self.operation_id(item))
            return True
        if entry is None:
            self._check_unseen_fence(item, manifest_sha256)
        else:
            actual = self._read_fence(item, S3MappedAbsenceFence)
            if actual.model_dump(exclude={'prepared_at'}) != self._mapped_fence(entry).model_dump(exclude={'prepared_at'}):
                reject('RESTORE_S3_FENCE_IDENTITY_MISMATCH')
        observe_absence(self.store.driver, item.object_key)
        return True


def load_context():
    states = list(S3ContentRestoreState.objects.order_by('generation'))
    if not states or ContentRestoreState.objects.exists():
        reject('RESTORE_S3_STATE_REQUIRED')
    state = states[-1]
    if state.state != 'mapped':
        reject('RESTORE_S3_MAPPING_NOT_READY')
    root = safe_path(state.restore_root)
    raw = read_json(root / MAPPING_FILE)
    if checksum(raw) != state.mapping_sha256:
        reject('RESTORE_S3_MAPPING_CHECKSUM_MISMATCH')
    mapping = S3RestoreMapping.model_validate_json(canonical(raw))
    store, database, cluster_id = check_physical_target(root, mapping.source_snapshot)
    if (mapping.restore_id != state.id or mapping.database != database or mapping.cluster_id != cluster_id
            or state.database_name != database or state.cluster_id != cluster_id
            or mapping.source_snapshot.binding_sha256 != state.source_binding_sha256
            or mapping.target_binding_sha256 != state.target_binding_sha256
            or mapping.backup_manifest_sha256 != state.backup_manifest_sha256
            or checksum(mapping.source_snapshot.model_dump(mode='json')) != state.source_snapshot_sha256
            or mapping.target_binding_sha256 != store.driver.configuration.binding_sha256
            or mapping.target_control_directory_sha256 != storage_selection.declaration()['control_directory_sha256']):
        reject('RESTORE_S3_MAPPING_IDENTITY_MISMATCH')
    marker = read_json(root / BLOCKED_FILE)
    if (marker.get('state') != 'ADOPTION_BLOCKED' or marker.get('target') != target_document(state)
            or marker.get('backup_manifest_sha256') != state.backup_manifest_sha256
            or marker.get('content_checkpoint') != state.content_checkpoint):
        reject('RESTORE_S3_DATABASE_STATE_MISMATCH')
    from retention.s3_lineage import complete_lineage, validate_registration_history
    history = complete_lineage(state, mapping, states)
    validate_registration_history(store, history)
    return S3RestoreContext(state, mapping, store, history)
