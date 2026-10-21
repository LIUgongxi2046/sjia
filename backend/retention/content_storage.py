from uuid import uuid4

from files.s3_deletion import S3RegisteredDeletion
from files.s3_registered import S3RegisteredStore
from files.s3_storage import canonical_key
from files.storage import LocalFileStore
from retention.content_schemas import LocalObjectProof, S3ObjectProof
from retention.disposition import DispositionRejected


def reject(code):
    raise DispositionRejected(code)


def storage_kind():
    from files.service import object_store
    if isinstance(object_store, S3RegisteredStore):
        return 's3'
    if isinstance(object_store, LocalFileStore):
        return 'local'
    reject('CONTENT_STORAGE_UNREGISTERED')


def file_target(file):
    return {'kind': 's3_object' if storage_kind() == 's3' else 'local_object',
        'file_id': str(file.id), 'object_version': file.object_version}


def assert_target_storage(targets):
    expected = 's3_object' if storage_kind() == 's3' else 'local_object'
    if any(target.kind not in ('database', expected) for target in targets):
        reject('CONTENT_TARGET_STORAGE_MISMATCH')


def assert_s3_exclusive_keys(items):
    from files.models import FileObject
    selected = {key for _, key in items}
    if len(selected) != len(items):
        reject('CONTENT_SHARED_S3_KEY_SCOPE')
    ids = {identity for identity, _ in items}
    for row in FileObject.objects.only('id', 'object_key').iterator(chunk_size=1000):
        canonical_key(row.object_key)
        if row.object_key in selected and row.id not in ids:
            reject('CONTENT_SHARED_S3_KEY_SCOPE')


def file_proof(file):
    from files.service import object_store
    values = {**file_target(file), 'object_key': file.object_key, 'checksum': file.checksum,
        'size_bytes': file.size_bytes, 'operation_id': uuid4()}
    if storage_kind() == 'local':
        return LocalObjectProof(**values)
    reference = object_store.reference_for(file.object_key)
    object_store.driver.validate_bucket()
    object_store.driver.verify_reference(reference)
    versions, markers, _ = object_store.driver._key_versions(file.object_key)
    if (markers or len(versions) != 1 or versions[0]['VersionId'] != reference.version_id
            or versions[0].get('IsLatest') is not True):
        reject('CONTENT_S3_OBJECT_VERSION_CONFLICT')
    return S3ObjectProof(**values, storage_reference=reference)


def assert_manifest_storage(manifest, *, replay=False):
    from files.service import object_store
    kind = storage_kind()
    if manifest.schema_version == 'content-disposition-manifest-2':
        if kind != 's3':
            reject('CONTENT_MANIFEST_STORAGE_MISMATCH')
        if manifest.storage_binding_sha256 != object_store.driver.configuration.binding_sha256:
            from retention.s3_restore import load_context
            context = load_context()
            context.validate_manifest(manifest)
            return context
        if replay:
            from retention.s3_restore import load_context
            load_context()
    elif any(item.kind == 'local_object' for item in manifest.items) and kind != 'local':
        from retention.s3_restore import load_context
        context = load_context()
        context.validate_manifest(manifest)
        return context
    return None


def assert_current_s3_proof(item):
    from files.service import object_store
    if storage_kind() != 's3' or object_store.reference_for(item.object_key) != item.storage_reference:
        reject('CONTENT_S3_REFERENCE_CHANGED')
    object_store.driver.validate_bucket()
    object_store.driver.verify_reference(item.storage_reference)
    versions, markers, _ = object_store.driver._key_versions(item.object_key)
    if markers or len(versions) != 1 or versions[0]['VersionId'] != item.storage_reference.version_id:
        reject('CONTENT_S3_OBJECT_VERSION_CONFLICT')


def delete_s3_proof(item):
    from files.service import object_store
    if storage_kind() != 's3':
        reject('CONTENT_MANIFEST_STORAGE_MISMATCH')
    return S3RegisteredDeletion(object_store).delete(item.storage_reference, operation_id=item.operation_id)


def verify_s3_proof(item):
    from files.service import object_store
    if storage_kind() != 's3':
        reject('CONTENT_MANIFEST_STORAGE_MISMATCH')
    registration = object_store.registration_for(item.object_key)
    if registration.status != 'disposed' or registration.reference != item.storage_reference:
        return False
    S3RegisteredDeletion(object_store).read_receipt(item.storage_reference, operation_id=item.operation_id)
    return True
