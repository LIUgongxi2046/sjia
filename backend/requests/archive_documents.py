from typing import Literal
from uuid import UUID

from django.conf import settings
from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError, authorize_case, current_principal, get_patient
from archives.grouping import document_materials
from archives.batches import batch_runtime
from archives.documents import document_record_scope
from archives.inputs import document_input
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion
from archives.queries import fact_source
from audit.models import AuditEvent
from archives.models import ArchivePage
from family_content.models import EnvironmentGate
from files.models import FileGroup, FileObject
from files.purpose_schemas import PurposeWriteHeaders
from files.purpose import lock_use_authorization
from identity.models import Patient
from records.models import RecordVersion, RecordField
from relations.models import Relation
from requests.models import WriteIntent, WriteRequest
from requests.service import prepare_intent, compute_digest, fail_intent, retry_intent


OPERATIONS = {'archive_document_create': 'DocumentInput', 'archive_document_group': 'GroupingInput'}


class DocumentIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['archive_document_create', 'archive_document_group']
    target_key: str
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


class DocumentRetryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_generation: str = Field(min_length=1, max_length=19, strict=True)


class DocumentRequestLookup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    lookup: Literal['request_id']
    operation_id: Literal['archive_document_create', 'archive_document_group']
    target_key: str = Field(min_length=1, max_length=200)


def family_identity(request):
    actor, epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    return actor, epoch


def target_scope(request, actor, epoch, operation, target, payload, *, check_version=True, receipt_document_id=None):
    # An internal caller may supply the exact actor and current auth epoch
    # without fabricating or persisting a browser session. Both routes use the
    # same authorization policy and all following locked material checks.
    def authorize(action, **scope):
        if request is not None:
            return authorize_case(request, actor, patient, action, **scope)
        from authorization.policy import evaluate
        return evaluate(principal=actor, patient=patient, action=action,
            purpose='family_observation', environment=settings.SJIA_ENVIRONMENT,
            session_auth_epoch=epoch, **scope)

    document_input(OPERATIONS[operation], payload)
    if not target.startswith('/') or '?' in target or '#' in target or '://' in target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    document = None
    if operation == 'archive_document_create':
        patient = get_patient(route.kwargs['patient_id'])
        batch = ArchiveBatch.objects.get(id=payload['batch_id'], patient_id=patient.id)
        author = batch.submitted_by
    else:
        document = ArchiveDocument.objects.get(id=route.kwargs['document_id'])
        patient = get_patient(document.patient_id)
        initial = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id, version=1)
        author = ArchiveBatch.objects.get(id=initial.batch_id, patient_id=patient.id).submitted_by
    scope_version = None if document is None else ArchiveDocumentVersion.objects.get(document_id=document.id,
        patient_id=patient.id, version=document.current_version)
    record_id = None if document is None else document_record_scope(document, patient, scope_version)
    decision = authorize('write_record', record_id=record_id, author_id=author,
        require_relation=True, whole_resource=True)
    patient = Patient.objects.select_for_update().get(id=patient.id)
    if document is not None:
        document = ArchiveDocument.objects.select_for_update().get(id=document.id, patient_id=patient.id)
    proofs = [decision]
    proofs.append(authorize('read_raw', record_id=record_id,
        author_id=author, require_relation=True, whole_resource=True))
    group_ids = FileObject.objects.filter(patient_id=patient.id,
        id__in=ArchivePage.objects.filter(id__in=payload['page_ids'], patient_id=patient.id,
            batch_id=payload['batch_id']).values('file_id')).values('file_group_id')
    for group in FileGroup.objects.filter(id__in=group_ids, patient_id=patient.id).order_by('id'):
        proofs.append(authorize('read_file', record_id=record_id, author_id=group.owner_id,
            require_relation=True, whole_resource=True))
    batch, pages, files, groups, episodes, facts, previous = document_materials(
        patient, payload, document, check_version=check_version, ignore_document_id=receipt_document_id,
        check_facts=receipt_document_id is None)
    source_fields = {field.id: field for _, field, _ in episodes}
    fact_record_ids = {fact.record_version_id for fact in facts}
    for field in RecordField.objects.filter(record_version_id__in=fact_record_ids, patient_id=patient.id):
        source_fields[field.id] = field
    for fact in facts:
        field, _ = fact_source(fact, patient)
        source_fields[field.id] = field
    for field in source_fields.values():
        version = RecordVersion.objects.get(id=field.record_version_id, patient_id=patient.id)
        initial = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient_id=patient.id, version=1)
        proofs.append(authorize('read_raw', field_codes=[field.field_code],
            record_id=version.record_id, author_id=initial.author_id, require_relation=True))
        if field.record_version_id in fact_record_ids:
            proofs.append(authorize('write_record', field_codes=[field.field_code],
                record_id=version.record_id, author_id=initial.author_id, require_relation=True))
    relation_versions = {str(row.id): str(row.version) for row in Relation.objects.select_for_update().filter(
        id__in=[proof.grant.relation_id for proof in proofs], patient_id=patient.id, guardian_id=actor.id)}
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    runtime = batch_runtime()
    if check_version:
        for source in files:
            group = FileGroup.objects.get(id=source.file_group_id, patient_id=patient.id)
            lock_use_authorization(group.use_authorization_id, source, 'family_observation', gate.environment)
    context = {'audience': 'family', 'auth_epoch': str(epoch), 'environment': settings.SJIA_ENVIRONMENT,
        'gate_version': str(gate.version),
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum,
        'patient_id': str(patient.id), 'batch_id': str(batch.id), 'batch_version': str(batch.version),
        'page_refs': [{'id': str(page.id), 'version': str(page.version), 'file_id': str(page.file_id),
            'object_version': page.file_object_version, 'checksum': page.checksum} for page in pages],
        'retention_refs': [{'file_id': str(source.id), 'policy_id': str(source.retention_policy_id),
            'policy_version': str(source.retention_policy.version)} for source in files],
        'source_scopes': sorted([{'grant_id': str(proof.grant.id), 'relation_id': str(proof.grant.relation_id),
            'relation_version': relation_versions[str(proof.grant.relation_id)],
            'field_codes': sorted(proof.grant.field_codes)} for proof in proofs], key=lambda item: item['grant_id'])}
    return patient, document, files[0].retention_policy, context


def document_receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.request_id != intent.request_id
            or saved.resource_type != 'archive_document' or saved.expected_version is None
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('DOCUMENT_RECEIPT_INVALID', 409)
    version = ArchiveDocumentVersion.objects.get(document_id=saved.resource_id, patient_id=intent.patient_id,
        version=saved.expected_version)
    if version.confirmed_by != intent.actor_id or not AuditEvent.objects.filter(actor_id=intent.actor_id,
            patient_id=intent.patient_id, action=intent.operation_id, object_type='archive_document',
            object_id=saved.resource_id, object_version=str(saved.expected_version), request_id=intent.request_id,
            occurred_at=saved.completed_at, outcome='committed').exists():
        raise ApiError('DOCUMENT_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': saved.resource_type,
        'resource_id': str(saved.resource_id), 'version': str(saved.expected_version),
        'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id), 'task_id': None}


def matching_request(intent):
    return WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
        target_key=intent.target_key, request_id=intent.request_id).exists()


def checked_payload(intent):
    if intent.recovery_payload is None:
        raise ApiError('DOCUMENT_RECOVERY_UNAVAILABLE', 409)
    payload = document_input(OPERATIONS[intent.operation_id], intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256:
        raise ApiError('DOCUMENT_RECOVERY_INVALID', 409)
    return payload


def authorized_intent(request, actor, epoch, intent):
    payload = checked_payload(intent)
    if intent.status == 'committed':
        # A later split/merge may retire this identity or assign its old pages
        # elsewhere. Receipt recovery authorizes the exact saved historical
        # version instead of replaying current write/page-uniqueness checks.
        from api.views_archive_documents import authorize_document
        from archives.documents import document_sources
        document_receipt(intent)
        saved = intent.write_request
        document = ArchiveDocument.objects.get(id=saved.resource_id, patient_id=intent.patient_id)
        patient = get_patient(intent.patient_id)
        route = resolve('/api/v2' + intent.target_key)
        if (route.url_name != intent.operation_id or reverse(intent.operation_id, kwargs=route.kwargs) != '/api/v2' + intent.target_key
                or intent.operation_id == 'archive_document_create' and route.kwargs['patient_id'] != patient.id
                or intent.operation_id == 'archive_document_group' and route.kwargs['document_id'] != document.id):
            raise ApiError('DOCUMENT_INTENT_SCOPE_MISMATCH', 409)
        version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
            version=saved.expected_version)
        actions = ('read_raw', 'read_history') if document.retired_at is not None or version.version != document.current_version else ('read_raw',)
        for action in actions:
            authorize_document(request, actor, patient, document, version, action)
        batch, pages, files, groups, _, _ = document_sources(document=document, patient=patient, version=version)
        if (str(batch.id) != payload['batch_id'] or [str(page.id) for page in pages] != payload['page_ids']
                or files[0].retention_policy_id != intent.retention_policy_id):
            raise ApiError('DOCUMENT_INTENT_SCOPE_MISMATCH', 409)
        for file in files:
            group = next(value for value in groups if value.id == file.file_group_id)
            lock_use_authorization(group.use_authorization_id, file, 'family_observation', settings.SJIA_ENVIRONMENT)
        return patient, document, None
    patient, document, retention, context = target_scope(request, actor, epoch, intent.operation_id,
        intent.target_key, payload, check_version=intent.status != 'committed',
        receipt_document_id=intent.write_request.resource_id if intent.status == 'committed' and intent.write_request_id else None)
    if patient.id != intent.patient_id or retention.id != intent.retention_policy_id:
        raise ApiError('DOCUMENT_INTENT_SCOPE_MISMATCH', 409)
    return patient, document, context


def intent_view(intent):
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation),
        'status': intent.status, 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256}


def request_view(intent, context):
    unlinked = intent.write_request_id is None and matching_request(intent)
    state = 'unknown' if unlinked else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status]
    retryable = (not unlinked and intent.write_request_id is None and (intent.status == 'failed'
        or (intent.status == 'prepared' and (intent.expires_at <= timezone.now()
            or intent.authorization_context != context))))
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(), 'state': state,
        'may_retry': retryable, 'receipt': document_receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'document_input': checked_payload(intent)}


def prepare_document_intent(request):
    actor, epoch = family_identity(request)
    data = DocumentIntentInput.model_validate(request.data)
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data.operation_id,
        target_key=data.target_key, request_id=data.request_id).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest(data.operation_id, data.target_key, '2.0.0', data.payload) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorized_intent(request, actor, epoch, existing)
        document_receipt(existing)
        return intent_view(existing)
    patient, _, retention, context = target_scope(request, actor, epoch, data.operation_id, data.target_key, data.payload)
    intent = prepare_intent(actor=actor, operation_id=data.operation_id, target_key=data.target_key,
        request_id=data.request_id, payload=data.payload, schema_version='2.0.0', retention_policy=retention,
        patient=patient, authorization_context=context, recovery_payload=data.payload)
    return intent_view(intent)


def read_document_request(request, intent_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('DOCUMENT_QUERY_INVALID', 422)
        lookup = DocumentRequestLookup.model_validate(dict(request.query_params.items()))
        intent = WriteIntent.objects.select_for_update().filter(request_id=intent_id, actor=actor,
            operation_id=lookup.operation_id, target_key=lookup.target_key).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=actor,
            operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('DOCUMENT_REQUEST_NOT_FOUND', 404)
    _, _, context = authorized_intent(request, actor, epoch, intent)
    return request_view(intent, context)


def retry_document_intent(request, intent_id):
    actor, epoch = family_identity(request)
    data = DocumentRetryInput.model_validate(request.data)
    if not data.expected_generation.isascii() or not data.expected_generation.isdecimal() or data.expected_generation.startswith('0'):
        raise ApiError('DOCUMENT_INPUT_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    _, _, context = authorized_intent(request, actor, epoch, intent)
    if not request_view(intent, context)['may_retry'] or int(data.expected_generation) != intent.generation:
        raise ApiError('DOCUMENT_RETRY_NOT_AVAILABLE', 409)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='DOCUMENT_INTENT_EXPIRED' if intent.expires_at <= timezone.now()
            else 'DOCUMENT_AUTHORIZATION_CHANGED')
    retry_intent(intent=intent, expected_generation=int(data.expected_generation))
    intent.authorization_context = context
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_document_intent(request, actor, epoch, operation):
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=operation)
    if (intent.target_key != target or intent.request_id != headers.request_id
            or str(intent.generation) != headers.generation
            or compute_digest(operation, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('DOCUMENT_INTENT_MISMATCH', 409)
    patient, document, context = authorized_intent(request, actor, epoch, intent)
    if intent.status != 'committed':
        if intent.authorization_context != context:
            raise ApiError('DOCUMENT_AUTHORIZATION_CHANGED', 403)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('DOCUMENT_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('DOCUMENT_RESULT_UNKNOWN', 409)
    return intent, patient, document
