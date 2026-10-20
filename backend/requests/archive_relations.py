from uuid import UUID

from django.conf import settings
from django.urls import resolve, reverse
from django.utils import timezone

from api.base import ApiError, current_principal, get_patient
from archives.batches import batch_runtime
from archives.models import ArchiveRelationVersion
from archives.relations import current_relation, relation_materials, relation_verification, relation_fields
from archives.relation_schemas import OPERATIONS, checked_relation
from assessment.provenance import entity_reference
from audit.models import AuditEvent
from authorization.models import AccessGrant
from files.purpose_schemas import PurposeWriteHeaders
from family_content.models import EnvironmentGate
from family_content.gate_locks import read_environment_gate_shared
from identity.models import Patient
from relations.models import Relation
from requests.models import WriteIntent, WriteRequest
from requests.service import prepare_intent, compute_digest, fail_intent, retry_intent


def relation_identity(request, *, writing=True):
    actor, epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience not in ('family', 'support'):
        raise ApiError('AUDIENCE_INVALID', 403)
    gate = (EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
        if writing else read_environment_gate_shared(settings.SJIA_ENVIRONMENT))
    if not gate.enabled:
        raise ApiError('ENVIRONMENT_DISABLED', 403)
    return actor, epoch, audience


def target_scope(request, actor, epoch, audience, operation, target, payload, *, check_version=True):
    if (operation == 'archive_relation_verify') != (audience == 'support'):
        raise ApiError('ARCHIVE_RELATION_AUDIENCE_DENIED', 403)
    checked_relation(OPERATIONS[operation], payload)
    if not target.startswith('/') or '?' in target or '#' in target or '://' in target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != operation or reverse(operation, kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    previous = None
    if operation == 'archive_relation_create':
        patient = get_patient(route.kwargs['patient_id'])
    else:
        initial = ArchiveRelationVersion.objects.get(relation_id=route.kwargs['relation_id'], version=1)
        patient = get_patient(initial.patient_id)
        previous = current_relation(patient, initial.relation_id)
        if check_version and str(previous.version) != payload['expected_version']:
            raise ApiError('VERSION_CONFLICT', 409)
    patient = Patient.objects.select_for_update().get(id=patient.id)
    runtime = batch_runtime()
    proof = None
    if operation == 'archive_relation_verify':
        proof = relation_verification(request, actor, patient, previous, payload)
        materials = relation_fields(previous)
    else:
        materials = payload
    kind, values, retention = relation_materials(request, actor, patient, materials,
        previous if operation != 'archive_relation_verify' else None, check_current=check_version)
    grants = AccessGrant.objects.select_for_update().filter(principal=actor, patient=patient).order_by('id')
    relation_ids = [grant.relation_id for grant in grants if grant.relation_id]
    context = {'audience': audience, 'environment': settings.SJIA_ENVIRONMENT, 'auth_epoch': str(epoch),
        'gate': entity_reference(EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)),
        'patient_id': str(patient.id), 'runtime': entity_reference(runtime), 'source_kind': kind,
        'source_refs': [[entity_reference(value) for value in group] for group in values],
        'grants': [entity_reference(grant) for grant in grants],
        'relations': [entity_reference(row) for row in Relation.objects.select_for_update().filter(id__in=relation_ids).order_by('id')],
        'previous': entity_reference(previous) if previous else None, 'verification': entity_reference(proof) if proof else None}
    return patient, previous, retention, context, proof


def relation_receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed'
            or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.action != intent.operation_id or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.request_id != intent.request_id
            or saved.resource_type != 'archive_relation' or saved.expected_version is None
            or saved.completed_at is None or saved.completed_at != intent.completed_at):
        raise ApiError('ARCHIVE_RELATION_RECEIPT_INVALID', 409)
    version = ArchiveRelationVersion.objects.get(relation_id=saved.resource_id, patient_id=intent.patient_id, version=saved.expected_version)
    if version.author_id != intent.actor_id or not AuditEvent.objects.filter(actor_id=intent.actor_id,
            patient_id=intent.patient_id, action=intent.operation_id, object_type='archive_relation',
            object_id=saved.resource_id, object_version=str(saved.expected_version), request_id=intent.request_id,
            occurred_at=saved.completed_at, outcome='committed').exists():
        raise ApiError('ARCHIVE_RELATION_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': saved.resource_type, 'resource_id': str(saved.resource_id),
        'version': str(saved.expected_version), 'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id), 'task_id': None}


def matching_request(intent):
    return WriteRequest.objects.filter(actor_id=intent.actor_id, action=intent.operation_id,
        target_key=intent.target_key, request_id=intent.request_id).exists()


def checked_payload(intent):
    if intent.recovery_payload is None:
        raise ApiError('ARCHIVE_RELATION_RECOVERY_UNAVAILABLE', 409)
    payload = checked_relation(OPERATIONS[intent.operation_id], intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', payload) != intent.payload_sha256:
        raise ApiError('ARCHIVE_RELATION_RECOVERY_INVALID', 409)
    return payload


def authorized_intent(request, actor, epoch, audience, intent):
    patient, previous, retention, context, proof = target_scope(request, actor, epoch, audience, intent.operation_id,
        intent.target_key, checked_payload(intent), check_version=intent.status != 'committed')
    if patient.id != intent.patient_id or retention.id != intent.retention_policy_id:
        raise ApiError('ARCHIVE_RELATION_INTENT_SCOPE_MISMATCH', 409)
    return patient, previous, context, proof


def intent_view(intent):
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation),
        'status': intent.status, 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256}


def request_view(intent, context):
    unlinked = intent.write_request_id is None and matching_request(intent)
    state = 'unknown' if unlinked else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status]
    retryable = not unlinked and intent.write_request_id is None and (intent.status == 'failed'
        or intent.status == 'prepared' and (intent.expires_at <= timezone.now() or intent.authorization_context != context))
    return checked_relation('RelationRequestView', {'intent_id': str(intent.id), 'request_id': str(intent.request_id),
        'operation_id': intent.operation_id, 'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': state, 'may_retry': retryable, 'receipt': relation_receipt(intent) if intent.status == 'committed' else None,
        'error_code': intent.error_code, 'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'relation_input': checked_payload(intent)})


def prepare_relation_intent(request):
    actor, epoch, audience = relation_identity(request)
    data = checked_relation('RelationIntentInput', request.data)
    checked_relation(OPERATIONS[data['operation_id']], data['payload'])
    existing = WriteIntent.objects.select_for_update().filter(actor=actor, operation_id=data['operation_id'],
        target_key=data['target_key'], request_id=data['request_id']).first()
    if existing is not None and existing.status == 'committed':
        if compute_digest(data['operation_id'], data['target_key'], '2.0.0', data['payload']) != existing.payload_sha256:
            raise ApiError('IDEMPOTENCY_CONFLICT', 409)
        authorized_intent(request, actor, epoch, audience, existing)
        relation_receipt(existing)
        return intent_view(existing)
    patient, _, retention, context, _ = target_scope(request, actor, epoch, audience,
        data['operation_id'], data['target_key'], data['payload'])
    intent = prepare_intent(actor=actor, operation_id=data['operation_id'], target_key=data['target_key'],
        request_id=UUID(data['request_id']), payload=data['payload'], schema_version='2.0.0', retention_policy=retention,
        patient=patient, authorization_context=context, recovery_payload=data['payload'])
    return intent_view(intent)


def read_relation_request(request, intent_id):
    actor, epoch, audience = relation_identity(request)
    if request.query_params:
        if any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
            raise ApiError('ARCHIVE_RELATION_QUERY_INVALID', 422)
        lookup = checked_relation('RelationRequestLookup', dict(request.query_params.items()))
        intent = WriteIntent.objects.select_for_update().filter(request_id=intent_id, actor=actor,
            operation_id=lookup['operation_id'], target_key=lookup['target_key']).first()
    else:
        intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=actor, operation_id__in=OPERATIONS).first()
    if intent is None:
        raise ApiError('ARCHIVE_RELATION_REQUEST_NOT_FOUND', 404)
    _, _, context, _ = authorized_intent(request, actor, epoch, audience, intent)
    return request_view(intent, context)


def retry_relation_intent(request, intent_id):
    actor, epoch, audience = relation_identity(request)
    if not isinstance(request.data, dict) or set(request.data) != {'expected_generation'}:
        raise ApiError('ARCHIVE_RELATION_INPUT_INVALID', 422)
    generation = checked_relation('Version', request.data['expected_generation'])
    if int(generation) > 9223372036854775807:
        raise ApiError('ARCHIVE_RELATION_INPUT_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id__in=OPERATIONS)
    _, _, context, _ = authorized_intent(request, actor, epoch, audience, intent)
    if not request_view(intent, context)['may_retry'] or int(generation) != intent.generation:
        raise ApiError('ARCHIVE_RELATION_RETRY_NOT_AVAILABLE', 409)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='ARCHIVE_RELATION_INTENT_EXPIRED' if intent.expires_at <= timezone.now()
            else 'ARCHIVE_RELATION_AUTHORIZATION_CHANGED')
    retry_intent(intent=intent, expected_generation=int(generation))
    intent.authorization_context = context
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_relation_intent(request, operation):
    actor, epoch, audience = relation_identity(request)
    checked_relation(OPERATIONS[operation], request.data)
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    target = request.path.removeprefix('/api/v2')
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=operation)
    if (intent.target_key != target or intent.request_id != headers.request_id or str(intent.generation) != headers.generation
            or compute_digest(operation, target, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('ARCHIVE_RELATION_INTENT_MISMATCH', 409)
    patient, previous, context, proof = authorized_intent(request, actor, epoch, audience, intent)
    if intent.status != 'committed':
        if intent.authorization_context != context:
            raise ApiError('ARCHIVE_RELATION_AUTHORIZATION_CHANGED', 403)
        if intent.status != 'prepared' or intent.expires_at <= timezone.now():
            raise ApiError('ARCHIVE_RELATION_INTENT_EXPIRED', 409)
        if matching_request(intent):
            raise ApiError('ARCHIVE_RELATION_RESULT_UNKNOWN', 409)
    return actor, intent, patient, previous, proof
