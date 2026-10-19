from django.db import transaction

from files.deletion import delete_local_object, deletion_keys, object_path, read_deletion_receipt
from files.purpose_schemas import PurposeDispositionObjectProof, PurposeS3DispositionObjectProof
from files.s3_deletion import S3RegisteredDeletion
from files.s3_registered import S3RegisteredStore
from files.s3_storage import S3StorageRejected
from files.storage import LocalFileStore


DISPOSITION_EVENTS = {'local-delete-2': 'disposition.local_requested', 's3-delete-1': 'disposition.s3_requested'}


def disposition_object_proof(store, file):
    values = {'file_id': file.id, 'object_version': file.object_version, 'object_key': file.object_key,
        'checksum': file.checksum, 'size_bytes': file.size_bytes, 'retention_policy_id': file.retention_policy_id}
    if isinstance(store, S3RegisteredStore):
        return PurposeS3DispositionObjectProof(**values,
            storage_reference=store.registration_for(file.object_key).reference)
    if isinstance(store, LocalFileStore):
        object_path(store, file.object_key)
        return PurposeDispositionObjectProof(**values)
    raise S3StorageRejected('DISPOSITION_STORAGE_UNREGISTERED')


def _validate(store, proof):
    if isinstance(store, S3RegisteredStore):
        if not isinstance(proof, PurposeS3DispositionObjectProof):
            raise S3StorageRejected('S3_DISPOSITION_REFERENCE_REQUIRED')
        reference = store.registration_for(proof.object_key).reference
        if reference != proof.storage_reference:
            raise S3StorageRejected('S3_DISPOSITION_REFERENCE_MISMATCH')
        return 's3-delete-1'
    if isinstance(store, LocalFileStore):
        if type(proof) is not PurposeDispositionObjectProof:
            raise S3StorageRejected('LOCAL_DISPOSITION_REFERENCE_MISMATCH')
        object_path(store, proof.object_key)
        return 'local-delete-2'
    raise S3StorageRejected('DISPOSITION_STORAGE_UNREGISTERED')


def disposition_task_schema(store, proof):
    return _validate(store, proof)


@transaction.atomic
def deletion_evidence_state(store, *, proof, operation_id):
    schema = _validate(store, proof)
    if schema == 's3-delete-1':
        deletion = S3RegisteredDeletion(store)
        deletion._row(proof.storage_reference, operation_id)
        journal_key, receipt_key = deletion._keys(proof.storage_reference)
        return {'journal_present': deletion._path(journal_key, proof.storage_reference).exists(),
            'receipt_present': deletion._path(receipt_key, proof.storage_reference).exists()}
    journal_key, receipt_key = deletion_keys(operation_id)
    return {'journal_present': object_path(store, journal_key).exists(),
        'receipt_present': object_path(store, receipt_key).exists()}


def delete_disposition_object(store, *, proof, operation_id):
    schema = _validate(store, proof)
    if schema == 's3-delete-1':
        return S3RegisteredDeletion(store).delete(proof.storage_reference, operation_id=operation_id)
    return delete_local_object(store, operation_id=operation_id, object_key=proof.object_key,
        checksum=proof.checksum, size_bytes=proof.size_bytes)


def read_disposition_receipt(store, *, proof, operation_id):
    schema = _validate(store, proof)
    if schema == 's3-delete-1':
        return S3RegisteredDeletion(store).read_receipt(proof.storage_reference, operation_id=operation_id)
    return read_deletion_receipt(store, operation_id=operation_id, object_key=proof.object_key,
        checksum=proof.checksum, size_bytes=proof.size_bytes)
