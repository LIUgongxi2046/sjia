import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from django.conf import settings
from django.utils import timezone
from django.db import transaction
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from identity.models import Principal
from retention.models import DispositionItem, DispositionRequest, SimulatedProviderCopy
from files.deletion import digest
from files.service import object_store
from tasks.models import Task, TaskAttempt
from tasks.service import acquire_lease, create_attempt, finish_attempt, input_digest, register_task


SIMULATION_DELETE_TASK_SCHEMA = 'simulation-provider-deletion-1'


class ExternalDeletionRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


class ExternalDeletionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: str = Field(pattern=r'^provider-delete-request-1$')
    request_id: UUID
    provider_id: str = Field(pattern=r'^SIMULATION_PROVIDER$')
    object_id: UUID
    object_version: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._-]+$')
    checksum: str = Field(pattern=r'^[0-9a-f]{64}$')
    size_bytes: int = Field(ge=0)
    requested_at: AwareDatetime

    @model_validator(mode='after')
    def validate_version(self):
        if self.object_version in ('.', '..'):
            raise ValueError('外部对象版本无效')
        return self


class ExternalDeletionReceipt(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    schema_version: str = Field(pattern=r'^provider-delete-receipt-1$')
    request_id: UUID
    provider_id: str = Field(pattern=r'^SIMULATION_PROVIDER$')
    object_id: UUID
    object_version: str
    checksum: str = Field(pattern=r'^[0-9a-f]{64}$')
    size_bytes: int = Field(ge=0)
    outcome: str = Field(pattern=r'^(deleted|not_found|unknown)$')
    observed_at: AwareDatetime
    evidence_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class ExternalDispositionRef(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    disposition_id: UUID = Field(strict=False)
    item_id: UUID = Field(strict=False)
    target_type: str = Field(pattern=r'^(provider_copy|external_copy)$')
    target_id: str = Field(min_length=1)
    target_version: str = Field(min_length=1)
    auth_epoch: int | None = Field(default=None, ge=0)


def _validate_disposition_ref(value, request, *, require_open=True):
    if value is None:
        return None
    ref = ExternalDispositionRef.model_validate(value)
    if (ref.target_id != str(request.object_id) or ref.target_version != request.object_version):
        raise ExternalDeletionRejected('EXTERNAL_DELETE_DISPOSITION_TARGET_MISMATCH')
    disposition = DispositionRequest.objects.select_for_update().get(id=ref.disposition_id)
    item = DispositionItem.objects.select_for_update().get(id=ref.item_id, request=disposition)
    scope_targets = disposition.scope.get('targets', [])
    expected = {'target_type': ref.target_type, 'target_id': ref.target_id, 'target_version': ref.target_version}
    if ((require_open and disposition.status not in ('approved', 'running'))
            or (require_open and item.status not in ('pending', 'failed', 'blocked', 'awaiting_provider'))
            or (item.target_type, item.target_id, item.target_version)
                != (ref.target_type, ref.target_id, ref.target_version)
            or expected not in scope_targets):
        raise ExternalDeletionRejected('EXTERNAL_DELETE_DISPOSITION_UNAVAILABLE')
    return ref


def _require_simulation():
    if (not settings.DEBUG or settings.SJIA_ENVIRONMENT != 'simulation'
            or settings.DATABASES['default']['NAME'] != 'sjia_simulation'
            or settings.FILE_OBJECT_ROOT != settings.SIMULATION_ROOT / 'object-store'):
        raise ExternalDeletionRejected('EXTERNAL_DELETE_SIMULATION_ONLY')


def _root():
    _require_simulation()
    root = settings.SIMULATION_ROOT / 'external-provider-store'
    if root.is_symlink():
        raise ExternalDeletionRejected('EXTERNAL_DELETE_PATH_INVALID')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    resolved = root.resolve(strict=True)
    if not resolved.is_relative_to(settings.SIMULATION_ROOT.resolve(strict=True)):
        raise ExternalDeletionRejected('EXTERNAL_DELETE_PATH_INVALID')
    return resolved


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _object_identity(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _key(request):
    identity = str(request.object_id)
    version = request.object_version
    if version in ('.', '..') or re.fullmatch(r'[A-Za-z0-9._-]{1,128}', version) is None:
        raise ExternalDeletionRejected('EXTERNAL_DELETE_OBJECT_VERSION_INVALID')
    return identity, version


def _paths(request):
    root = _root()
    identity, version = _key(request)
    objects = root / 'objects' / identity
    records = root / 'requests'
    receipts = root / 'receipts'
    locks = root / 'locks'
    for directory in (objects, records, receipts, locks):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink() or not directory.resolve(strict=True).is_relative_to(root):
            raise ExternalDeletionRejected('EXTERNAL_DELETE_PATH_INVALID')
    object_lock = hashlib.sha256(f'{identity}:{version}'.encode()).hexdigest()
    paths = (objects / f'{version}.bin', records / f'{request.request_id}.json',
        receipts / f'{request.request_id}.json', locks / f'{object_lock}.lock')
    if any(path.is_symlink() or path.exists() and not path.resolve(strict=True).is_relative_to(root) for path in paths):
        raise ExternalDeletionRejected('EXTERNAL_DELETE_PATH_INVALID')
    return root, *paths


def _save_new(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def register_simulated_external_object(*, object_id, object_version, data):
    _require_simulation()
    payload = bytes(data)
    request = ExternalDeletionRequest(schema_version='provider-delete-request-1', request_id=UUID(int=0),
        provider_id='SIMULATION_PROVIDER', object_id=UUID(str(object_id)), object_version=object_version,
        checksum=_sha256(payload), size_bytes=len(payload), requested_at=timezone.now())
    _, object_path, _, _, lock_path = _paths(request)
    with lock_path.open('a+b') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if object_path.exists():
            if object_path.is_symlink() or object_path.read_bytes() != payload:
                raise ExternalDeletionRejected('EXTERNAL_DELETE_OBJECT_MISMATCH')
        else:
            _save_new(object_path, payload)
    return {'object_id': str(request.object_id), 'object_version': request.object_version,
        'checksum': request.checksum, 'size_bytes': request.size_bytes}


@transaction.atomic
def create_simulated_provider_copy(*, purpose_copy_id, actor, auth_epoch, review_id):
    _require_simulation()
    from files.purpose import current_review, lock_copy_context

    copy, original, derived, actor, patient, gate, group = lock_copy_context(
        purpose_copy_id, actor, auth_epoch, service_action='manage_data_use')
    if gate.environment != 'simulation' or copy.environment != 'simulation' or copy.status != 'approved':
        raise ExternalDeletionRejected('EXTERNAL_COPY_SOURCE_UNAVAILABLE')
    review = current_review(copy, original, derived)
    if review is None or str(review.id) != str(review_id):
        raise ExternalDeletionRejected('EXTERNAL_COPY_REVIEW_REQUIRED')
    data = object_store.get(derived.object_key)
    if digest(data) != derived.checksum or len(data) != derived.size_bytes:
        raise ExternalDeletionRejected('EXTERNAL_COPY_SOURCE_MISMATCH')
    object_id = uuid5(NAMESPACE_URL, f'simulation-provider:{copy.id}:{copy.version}:{derived.checksum}')
    object_version = f'sim-{copy.version}-{derived.checksum[:24]}'
    registered = register_simulated_external_object(object_id=object_id, object_version=object_version, data=data)
    item, created = SimulatedProviderCopy.objects.select_for_update().get_or_create(
        purpose_copy=copy, purpose_copy_version=copy.version, provider_id='SIMULATION_PROVIDER',
        defaults={'patient_id': patient.id, 'object_id': object_id, 'object_version': object_version,
            'checksum': registered['checksum'], 'size_bytes': registered['size_bytes'],
            'environment': 'simulation', 'status': 'available', 'created_by': actor,
            'created_at': timezone.now(), 'processing_evidence': {
                'simulation': True, 'purpose_copy_version': str(copy.version),
                'derived_file_id': str(derived.id), 'derived_file_version': derived.object_version,
                'derived_checksum': derived.checksum, 'review_id': str(review.id),
                'reviewer_id': str(review.reviewer_id), 'review_evidence_ref': review.evidence_ref,
                'use_authorization_id': str(copy.use_authorization_id),
                'configuration_id': copy.processing_evidence['config_id'],
                'configuration_checksum': copy.processing_evidence['config_checksum']}})
    if (item.patient_id != patient.id or item.object_id != object_id or item.object_version != object_version
            or item.checksum != registered['checksum'] or item.size_bytes != registered['size_bytes']):
        raise ExternalDeletionRejected('EXTERNAL_COPY_IDENTITY_MISMATCH')
    if not created and item.status != 'available':
        raise ExternalDeletionRejected('EXTERNAL_COPY_NOT_AVAILABLE')
    return item


def _read_receipt(path, request):
    if not path.is_file():
        return None
    receipt = ExternalDeletionReceipt.model_validate_json(path.read_bytes())
    if (receipt.request_id != request.request_id or receipt.provider_id != request.provider_id
            or receipt.object_id != request.object_id or receipt.object_version != request.object_version
            or receipt.checksum != request.checksum or receipt.size_bytes != request.size_bytes):
        raise ExternalDeletionRejected('EXTERNAL_DELETE_RECEIPT_MISMATCH')
    value = receipt.model_dump(mode='json', exclude={'evidence_sha256'})
    if _sha256(_encode(value)) != receipt.evidence_sha256:
        raise ExternalDeletionRejected('EXTERNAL_DELETE_RECEIPT_DIGEST_MISMATCH')
    return receipt


def _save_receipt(path, request, outcome):
    value = {'schema_version': 'provider-delete-receipt-1', 'request_id': request.request_id,
        'provider_id': request.provider_id, 'object_id': request.object_id, 'object_version': request.object_version,
        'checksum': request.checksum, 'size_bytes': request.size_bytes, 'outcome': outcome, 'observed_at': timezone.now()}
    unsigned = ExternalDeletionReceipt(**value, evidence_sha256='0' * 64).model_dump(mode='json', exclude={'evidence_sha256'})
    content = ExternalDeletionReceipt(**value, evidence_sha256=_sha256(_encode(unsigned)))
    _save_new(path, _encode(content.model_dump(mode='json')))
    return content


def query_simulated_external_deletion(request):
    parsed = ExternalDeletionRequest.model_validate(request)
    _, _, request_path, receipt_path, lock_path = _paths(parsed)
    with lock_path.open('a+b') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if not request_path.is_file():
            return {'state': 'not_requested', 'receipt': None}
        saved = ExternalDeletionRequest.model_validate_json(request_path.read_bytes())
        if saved != parsed:
            raise ExternalDeletionRejected('EXTERNAL_DELETE_REQUEST_MISMATCH')
        receipt = _read_receipt(receipt_path, parsed)
        if receipt is None:
            return {'state': 'unknown', 'receipt': None}
        return {'state': receipt.outcome, 'receipt': receipt.model_dump(mode='json')}


def prepare_simulated_external_deletion(request):
    parsed = ExternalDeletionRequest.model_validate(request)
    _, _, request_path, receipt_path, lock_path = _paths(parsed)
    with lock_path.open('a+b') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        request_data = _encode(parsed.model_dump(mode='json'))
        if request_path.exists():
            saved = ExternalDeletionRequest.model_validate_json(request_path.read_bytes())
            if saved != parsed:
                raise ExternalDeletionRejected('EXTERNAL_DELETE_REQUEST_MISMATCH')
        else:
            _save_new(request_path, request_data)
        current_receipt = _read_receipt(receipt_path, parsed)
        if current_receipt is not None:
            return {'state': current_receipt.outcome, 'receipt': current_receipt.model_dump(mode='json')}
        return {'state': 'unknown', 'receipt': None}


def execute_simulated_external_deletion(request):
    parsed = ExternalDeletionRequest.model_validate(request)
    root, object_path, request_path, receipt_path, lock_path = _paths(parsed)
    with lock_path.open('a+b') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if not request_path.is_file():
            raise ExternalDeletionRejected('EXTERNAL_DELETE_REQUEST_REQUIRED')
        saved = ExternalDeletionRequest.model_validate_json(request_path.read_bytes())
        if saved != parsed:
            raise ExternalDeletionRejected('EXTERNAL_DELETE_REQUEST_MISMATCH')
        current_receipt = _read_receipt(receipt_path, parsed)
        if current_receipt is not None:
            return {'state': current_receipt.outcome, 'receipt': current_receipt.model_dump(mode='json')}
        if object_path.exists():
            if object_path.is_symlink() or not object_path.resolve(strict=True).is_relative_to(root):
                raise ExternalDeletionRejected('EXTERNAL_DELETE_PATH_INVALID')
            before = object_path.stat()
            data = object_path.read_bytes()
            if (_object_identity(before) != _object_identity(object_path.stat())
                    or _sha256(data) != parsed.checksum or len(data) != parsed.size_bytes):
                raise ExternalDeletionRejected('EXTERNAL_DELETE_OBJECT_MISMATCH')
            object_path.unlink()
            directory = os.open(object_path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            if object_path.exists():
                raise ExternalDeletionRejected('EXTERNAL_DELETE_NOT_VERIFIED')
            receipt = _save_receipt(receipt_path, parsed, 'deleted')
            return {'state': receipt.outcome, 'receipt': receipt.model_dump(mode='json')}
        return {'state': 'unknown', 'receipt': None}


def submit_simulated_external_deletion(request):
    parsed = ExternalDeletionRequest.model_validate(request)
    prepared = prepare_simulated_external_deletion(parsed)
    if prepared['state'] != 'unknown':
        return prepared
    return execute_simulated_external_deletion(parsed)


@transaction.atomic
def register_simulated_external_deletion_task(*, actor, request, disposition_ref=None):
    _require_simulation()
    parsed = ExternalDeletionRequest.model_validate(request)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    linked = _validate_disposition_ref(disposition_ref, parsed)
    refs = {'mode': 'SIMULATION', 'request': parsed.model_dump(mode='json'),
        'disposition_ref': linked.model_dump(mode='json') if linked is not None else None}
    existing = Task.objects.select_for_update().filter(actor=actor, task_type=Task.TaskType.DISPOSITION,
        schema_version=SIMULATION_DELETE_TASK_SCHEMA,
        input_refs__request__request_id=str(parsed.request_id)).first()
    if existing is not None:
        if existing.input_refs != refs or input_digest(existing.input_refs) != existing.input_sha256:
            raise ExternalDeletionRejected('EXTERNAL_DELETE_TASK_REQUEST_MISMATCH')
        return existing
    patient = None if linked is None else DispositionRequest.objects.get(id=linked.disposition_id).patient
    return register_task(actor=actor, task_type=Task.TaskType.DISPOSITION,
        schema_version=SIMULATION_DELETE_TASK_SCHEMA, input_refs=refs, patient=patient)


@transaction.atomic
def read_simulated_external_deletion_task(task_id):
    _require_simulation()
    task = Task.objects.get(id=task_id, task_type=Task.TaskType.DISPOSITION,
        schema_version=SIMULATION_DELETE_TASK_SCHEMA)
    if task.input_refs.get('mode') != 'SIMULATION' or input_digest(task.input_refs) != task.input_sha256:
        raise ExternalDeletionRejected('EXTERNAL_DELETE_TASK_INPUT_MISMATCH')
    request = ExternalDeletionRequest.model_validate_json(_encode(task.input_refs['request']))
    linked = _validate_disposition_ref(task.input_refs.get('disposition_ref'), request, require_open=False)
    if linked is not None and task.patient_id != DispositionRequest.objects.get(id=linked.disposition_id).patient_id:
        raise ExternalDeletionRejected('EXTERNAL_DELETE_DISPOSITION_PATIENT_MISMATCH')
    provider_result = query_simulated_external_deletion(request)
    attempt = TaskAttempt.objects.filter(task=task).order_by('-attempt_number').first()
    if task.status == Task.Status.COMPLETED:
        if (attempt is None or attempt.status != TaskAttempt.Status.COMPLETED
                or provider_result['state'] != 'deleted'
                or attempt.usage_evidence.get('receipt') != provider_result['receipt']):
            raise ExternalDeletionRejected('EXTERNAL_DELETE_TASK_RESULT_MISMATCH')
    return {'mode': 'SIMULATION', 'task_id': str(task.id), 'task_status': task.status,
        'task_status_version': str(task.status_version), 'provider_state': provider_result['state'],
        'receipt': provider_result['receipt'],
        'disposition_ref': None if linked is None else linked.model_dump(mode='json'),
        'attempt': None if attempt is None else {
            'attempt_id': str(attempt.id), 'attempt_number': attempt.attempt_number,
            'status': attempt.status, 'provider_request_id': attempt.provider_request_id,
            'usage_evidence': attempt.usage_evidence}}


def execute_simulated_external_deletion_task(task_id, *, lease_owner):
    _require_simulation()
    with transaction.atomic():
        task = Task.objects.select_for_update().get(id=task_id, task_type=Task.TaskType.DISPOSITION,
            schema_version=SIMULATION_DELETE_TASK_SCHEMA)
        if task.input_refs.get('mode') != 'SIMULATION' or input_digest(task.input_refs) != task.input_sha256:
            raise ExternalDeletionRejected('EXTERNAL_DELETE_TASK_INPUT_MISMATCH')
        if task.status == Task.Status.COMPLETED:
            return read_simulated_external_deletion_task(task.id)
        if task.status not in (Task.Status.ACCEPTED, Task.Status.QUEUED) or task.deadline_at <= timezone.now():
            raise ExternalDeletionRejected('EXTERNAL_DELETE_TASK_NOT_EXECUTABLE')
        request = ExternalDeletionRequest.model_validate_json(_encode(task.input_refs['request']))
        linked = _validate_disposition_ref(task.input_refs.get('disposition_ref'), request)
        if linked is not None and task.patient_id != DispositionRequest.objects.get(id=linked.disposition_id).patient_id:
            raise ExternalDeletionRejected('EXTERNAL_DELETE_DISPOSITION_PATIENT_MISMATCH')
        if acquire_lease(task.id, lease_owner, task.deadline_at) != 1:
            raise ExternalDeletionRejected('EXTERNAL_DELETE_TASK_LEASE_UNAVAILABLE')
        task.refresh_from_db()
        attempt = create_attempt(task, task.lease_generation, provider_request_id=str(request.request_id))

    prepared = prepare_simulated_external_deletion(request)
    result = prepared if prepared['state'] != 'unknown' else execute_simulated_external_deletion(request)

    with transaction.atomic():
        task = Task.objects.select_for_update().get(id=task_id, task_type=Task.TaskType.DISPOSITION,
            schema_version=SIMULATION_DELETE_TASK_SCHEMA)
        if linked is not None:
            _validate_disposition_ref(linked, request, require_open=False)
        attempt = TaskAttempt.objects.select_for_update().get(id=attempt.id, task=task)
        if (task.status != Task.Status.RUNNING or task.lease_owner != lease_owner
                or task.lease_generation != attempt.lease_generation or attempt.status != TaskAttempt.Status.RUNNING):
            raise ExternalDeletionRejected('EXTERNAL_DELETE_TASK_LEASE_CHANGED')
        finished = timezone.now()
        task.status = Task.Status.COMPLETED if result['state'] == 'deleted' else Task.Status.UNCERTAIN
        task.status_version += 1
        task.lease_until = finished
        task.save(update_fields=['status', 'status_version', 'lease_until'])
        finish_attempt(attempt, TaskAttempt.Status.COMPLETED if result['state'] == 'deleted' else TaskAttempt.Status.UNCERTAIN,
            usage_evidence={'request_id': str(request.request_id), 'provider_state': result['state'],
                'receipt': result['receipt']})
    return read_simulated_external_deletion_task(task_id)
