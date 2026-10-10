from typing import Literal
from uuid import UUID

from django.urls import resolve, reverse
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field

from api.base import ApiError
from authorization.policy import evaluate
from configurations.service import compute_checksum
from files.purpose import lock_file_access, runtime_environment
from files.purpose_schemas import PurposeDispositionInput, PurposeWriteHeaders, PurposeRetryInput
from identity.models import Patient
from requests.archive_documents import family_identity, intent_view, matching_request
from requests.models import WriteIntent
from requests.service import NORMALIZATION_VERSION, compute_digest, fail_intent, prepare_intent, retry_intent
from retention.disposition import approved_policy, local_rule, target_file
from retention.models import DispositionRequest, RetentionPolicy


OPERATIONS = ('disposition_create',)


class DispositionIntentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_id: Literal['disposition_create']
    target_key: str = Field(min_length=1, max_length=256, strict=True)
    request_id: UUID
    schema_version: Literal['2.0.0']
    payload: dict


def disposition_patient(target):
    if not target.startswith('/') or any(part in target for part in ('?', '#', '://')):
        raise ApiError('TARGET_KEY_INVALID', 422)
    route = resolve('/api/v2' + target)
    if route.url_name != OPERATIONS[0] or reverse(OPERATIONS[0], kwargs=route.kwargs) != '/api/v2' + target:
        raise ApiError('TARGET_KEY_INVALID', 422)
    return route.kwargs['patient_id']


def family_scope(actor, epoch, patient_id, payload):
    patient = Patient.objects.select_for_update().get(id=patient_id, status='active')
    decision = evaluate(principal=actor, patient=patient, action='request_disposition', purpose='family_observation',
        environment=runtime_environment(), session_auth_epoch=epoch, require_relation=True, whole_resource=True, author_id=actor.id)
    if decision.grant.purpose != 'family_observation' or not decision.grant.evidence_ref.strip():
        raise ApiError('DISPOSITION_SCOPE_DENIED', 403)
    policy = approved_policy(RetentionPolicy.objects.get(id=payload.policy_id))
    rule = local_rule(policy)
    files = []
    for target in payload.targets:
        if target.target_type not in rule.target_types:
            raise ApiError('DISPOSITION_POLICY_SCOPE_DENIED', 422)
        file, original, copy = target_file(target, patient.id)
        lock_file_access(original, actor, epoch, purpose='family_observation', require_available=False)
        if file.retention_policy_id != policy.id or original.retention_policy_id != policy.id:
            raise ApiError('DISPOSITION_POLICY_SCOPE_DENIED', 422)
        files.append({'file_id': str(file.id), 'object_version': file.object_version, 'checksum': file.checksum,
            'size_bytes': file.size_bytes, 'original_file_id': str(original.id)})
    if len({row['file_id'] for row in files}) != len(files):
        raise ApiError('DISPOSITION_TARGET_ALIAS', 422)
    return patient, policy, {'patient_id': str(patient.id), 'environment': runtime_environment(),
        'policy_id': str(policy.id), 'policy_version': str(policy.version), 'policy_sha256': compute_checksum(policy.rules), 'files': files}


def recovery_input(intent):
    if intent.normalization_version != NORMALIZATION_VERSION or not isinstance(intent.recovery_payload, dict):
        raise ApiError('DISPOSITION_RECOVERY_UNAVAILABLE', 409)
    payload = PurposeDispositionInput.model_validate(intent.recovery_payload)
    if compute_digest(intent.operation_id, intent.target_key, '2.0.0', intent.recovery_payload) != intent.payload_sha256:
        raise ApiError('DISPOSITION_RECOVERY_INVALID', 409)
    return payload


def authorized_intent(actor, epoch, intent):
    patient, policy, context = family_scope(actor, epoch, disposition_patient(intent.target_key), recovery_input(intent))
    if intent.patient_id != patient.id or intent.retention_policy_id != policy.id:
        raise ApiError('DISPOSITION_INTENT_SCOPE_MISMATCH', 409)
    return patient, policy, context


def disposition_receipt(intent):
    saved = intent.write_request
    payload = recovery_input(intent)
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.resource_type != 'disposition'
            or saved.action != OPERATIONS[0] or saved.actor_id != intent.actor_id or saved.patient_id != intent.patient_id
            or saved.request_id != intent.request_id or saved.target_key != intent.target_key
            or saved.payload_sha256 != intent.payload_sha256 or saved.completed_at != intent.completed_at):
        raise ApiError('DISPOSITION_RECEIPT_INVALID', 409)
    row = DispositionRequest.objects.get(id=saved.resource_id, patient_id=intent.patient_id, requested_by_id=intent.actor_id)
    if row.policy_id != payload.policy_id or row.evidence_ref != payload.evidence_ref or row.scope['targets'] != [target.model_dump(mode='json') for target in payload.targets]:
        raise ApiError('DISPOSITION_RECEIPT_INVALID', 409)
    return {'schema_version': '2.0.0', 'resource_type': 'disposition', 'resource_id': str(row.id),
        'version': str(saved.expected_version), 'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id), 'task_id': None}


def request_view(intent, context):
    unknown = intent.write_request_id is None and matching_request(intent)
    expired = intent.status == 'prepared' and intent.expires_at <= timezone.now()
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if unknown else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': not unknown and intent.write_request_id is None and (intent.status == 'failed' or expired or context != intent.authorization_context),
        'receipt': disposition_receipt(intent) if intent.status == 'committed' else None, 'error_code': intent.error_code,
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256,
        'disposition_input': recovery_input(intent).model_dump(mode='json')}


def prepare_disposition_intent(request):
    actor, epoch = family_identity(request)
    data = DispositionIntentInput.model_validate(request.data)
    payload = PurposeDispositionInput.model_validate(data.payload)
    patient, policy, context = family_scope(actor, epoch, disposition_patient(data.target_key), payload)
    return intent_view(prepare_intent(actor=actor, patient=patient, operation_id=OPERATIONS[0], target_key=data.target_key,
        request_id=data.request_id, payload=data.payload, schema_version='2.0.0', retention_policy=policy,
        authorization_context=context, recovery_payload=data.payload))


def read_disposition_request(request, intent_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('DISPOSITION_QUERY_INVALID', 422)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id=OPERATIONS[0])
    _, _, context = authorized_intent(actor, epoch, intent)
    return request_view(intent, context)


def retry_disposition_intent(request, intent_id):
    actor, epoch = family_identity(request)
    value = PurposeRetryInput.model_validate(request.data)
    intent = WriteIntent.objects.select_for_update().get(id=intent_id, actor=actor, operation_id=OPERATIONS[0])
    _, _, context = authorized_intent(actor, epoch, intent)
    if not request_view(intent, context)['may_retry'] or str(intent.generation) != value.expected_generation:
        raise ApiError('DISPOSITION_RETRY_UNAVAILABLE', 409)
    if intent.status == 'prepared':
        fail_intent(intent=intent, error_code='DISPOSITION_INTENT_EXPIRED' if intent.expires_at <= timezone.now() else 'DISPOSITION_CONTEXT_CHANGED')
    retry_intent(intent=intent, expected_generation=int(value.expected_generation))
    intent.authorization_context = context
    intent.save(update_fields=['authorization_context'])
    return intent_view(intent)


def submitted_disposition_intent(request):
    actor, epoch = family_identity(request)
    headers = PurposeWriteHeaders.model_validate({'intent_id': request.headers.get('X-Write-Intent'),
        'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')})
    intent = WriteIntent.objects.select_for_update().get(id=headers.intent_id, actor=actor, operation_id=OPERATIONS[0])
    if (intent.target_key != request.path.removeprefix('/api/v2') or intent.request_id != headers.request_id
            or str(intent.generation) != headers.generation or compute_digest(OPERATIONS[0], intent.target_key, '2.0.0', request.data) != intent.payload_sha256):
        raise ApiError('DISPOSITION_INTENT_MISMATCH', 409)
    patient, policy, context = authorized_intent(actor, epoch, intent)
    if intent.authorization_context != context:
        raise ApiError('DISPOSITION_CONTEXT_CHANGED', 409)
    if intent.status != 'committed' and (intent.status != 'prepared' or intent.expires_at <= timezone.now() or matching_request(intent)):
        raise ApiError('DISPOSITION_RESULT_UNCONFIRMED', 409)
    return actor, patient, policy, intent, recovery_input(intent)
