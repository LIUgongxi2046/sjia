import hashlib
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.urls import resolve, reverse
from django.utils import timezone
from jsonschema import Draft202012Validator, FormatChecker

from api.base import ApiError, authorize_service, current_principal
from audit.models import AuditEvent
from authorization.models import ServiceRoleGrant
from authorization.policy import check_principal_session
from configurations.models import ConfigVersion
from configurations.lifecycle import CONFIG_OPERATIONS, configuration_target_ready, configuration_receipt_resource, configuration_request_scope
from contacts.approval import ContactApprovalPayload
from contacts.models import ContactVersion
from family_content.models import EnvironmentGate, ReleaseBundle
from identity.models import Principal
from requests.models import WriteIntent, WriteRequest
from requests.service import NORMALIZATION_VERSION, compute_digest, prepare_intent, validate_target_key
from retention.models import RetentionPolicy
from sources.operations import SOURCE_OPERATIONS, source_operation_ready, source_receipt_exists


PROTOCOL = json.loads((Path(__file__).resolve().parents[2] / 'backend-design' / 'protocol.schema.json').read_text())
CONTROLLED_OPERATIONS = {
    **CONFIG_OPERATIONS,
    **SOURCE_OPERATIONS,
    'release_create': ('publish_config', 'ReleaseInput', 'release'),
    'release_approve': ('publish_config', 'StateAction', 'release'),
    'release_enable': ('publish_config', 'GateAction', 'release'),
    'release_disable': ('publish_config', 'GateAction', 'release'),
    'contact_create': ('manage_contacts', 'ContactInput', 'contact_version'),
    'contact_activate': ('manage_contacts', 'GateAction', 'contact_version'),
    'contact_disable': ('manage_contacts', 'StateAction', 'contact_version'),
}


def checked_controlled_input(schema, data):
    validator = Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{schema}'}, format_checker=FormatChecker())
    if next(validator.iter_errors(data), None) is not None:
        raise ApiError('CONTROLLED_INPUT_INVALID', 422)
    return data


def controlled_target(operation, target):
    if operation not in CONTROLLED_OPERATIONS:
        raise ApiError('CONTROLLED_OPERATION_INVALID', 422)
    validate_target_key(target)
    route = resolve(target)
    if route.url_name != operation or target != reverse(operation, kwargs=route.kwargs):
        raise ApiError('CONTROLLED_TARGET_INVALID', 422)
    return route.kwargs


def controlled_identity(request):
    actor, auth_epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'support':
        raise ApiError('SUPPORT_AUDIENCE_REQUIRED', 403)
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    if not gate.enabled:
        raise ApiError('ENVIRONMENT_DISABLED', 403)
    return actor, gate


def controlled_context(request, operation):
    if operation not in CONTROLLED_OPERATIONS:
        raise ApiError('CONTROLLED_OPERATION_INVALID', 422)
    actor, gate = controlled_identity(request)
    grant = authorize_service(request, actor, CONTROLLED_OPERATIONS[operation][0]).service_grant
    return actor, gate, {'audience': 'support', 'auth_epoch': str(actor.auth_epoch),
        'environment': gate.environment, 'grant_id': str(grant.id), 'action': grant.action,
        'valid_from': grant.valid_from.isoformat(),
        'valid_until': grant.valid_until.isoformat() if grant.valid_until else None}


def controlled_target_ready(actor, gate, operation, target, payload):
    keys = controlled_target(operation, target)
    checked_controlled_input(CONTROLLED_OPERATIONS[operation][1], payload)
    if operation in CONFIG_OPERATIONS:
        return configuration_target_ready(actor, gate, operation, keys, payload)
    if operation in SOURCE_OPERATIONS:
        return source_operation_ready(actor, gate, operation, keys, payload)
    if 'expected_gate_version' in payload and str(gate.version) != payload['expected_gate_version']:
        return False
    if operation == 'release_create':
        if payload['environment'] != gate.environment:
            return False
        from family_content.publication_groups import group_input_reason, GROUP_KINDS
        if group_input_reason(payload):
            return False
        if any(not ConfigVersion.objects.filter(id=group[kind + '_id'], kind=kind).exists()
                for group in payload.get('publication_groups', []) for kind in GROUP_KINDS):
            return False
        return (ContactVersion.objects.filter(id=payload['contact_version_id']).exists()
            and ConfigVersion.objects.filter(id=payload['validation_config_id'], kind='validation').exists()
            and all(ConfigVersion.objects.filter(id=item['config_id'], kind=item['kind']).exists()
                for item in payload['components']))
    if operation == 'contact_create':
        config = ConfigVersion.objects.filter(id=payload['approval_config_id'], kind='content', schema_version='contact-1').first()
        if config is None:
            return False
        expected = ContactApprovalPayload.model_validate({**{key: value for key, value in payload.items()
            if key != 'approval_config_id'}, 'owner_id': str(actor.id), 'environment': gate.environment})
        approved = ContactApprovalPayload.model_validate(config.payload)
        return (approved == expected and expected.valid_until > timezone.now()
            and not ContactVersion.objects.filter(contact_id=expected.contact_id).exclude(owner=actor).exists())
    if operation.startswith('release_'):
        resource = ReleaseBundle.objects.select_for_update().filter(id=keys['release_id'], environment=gate.environment).first()
        expected = {'release_approve': 'draft', 'release_enable': 'approved', 'release_disable': 'enabled'}[operation]
        if resource is None or resource.status != expected:
            return False
        if operation == 'release_disable' and gate.active_release_id != resource.id:
            return False
    else:
        resource = ContactVersion.objects.select_for_update().select_related('approval_config').filter(
            id=keys['contact_version_id'], owner=actor).first()
        if resource is None or ReleaseBundle.objects.filter(contact_version=resource).exclude(environment=gate.environment).exists():
            return False
        if (resource.approval_config.schema_version == 'contact-1'
                and resource.approval_config.payload.get('environment') != gate.environment):
            return False
        if operation == 'contact_activate' and resource.status not in ('draft', 'approved'):
            return False
        if operation == 'contact_disable' and resource.status == 'disabled':
            return False
    return ('expected_state' not in payload or resource.status == payload['expected_state']) and (
        'expected_version' not in payload or str(resource.version) == payload['expected_version'])


def controlled_payload(intent):
    if intent.operation_id not in CONTROLLED_OPERATIONS or intent.patient_id is not None:
        raise ApiError('CONTROLLED_REQUEST_INVALID', 409)
    controlled_target(intent.operation_id, intent.target_key)
    payload = checked_controlled_input(CONTROLLED_OPERATIONS[intent.operation_id][1], intent.recovery_payload)
    if (intent.normalization_version != NORMALIZATION_VERSION or not isinstance(intent.authorization_context, dict)
            or intent.authorization_context.get('environment') != settings.SJIA_ENVIRONMENT
            or intent.payload_sha256 != compute_digest(intent.operation_id, intent.target_key, '1.0.0', payload)):
        raise ApiError('CONTROLLED_REQUEST_INVALID', 409)
    return payload


def controlled_receipt(intent):
    saved = intent.write_request
    if (intent.operation_id not in CONTROLLED_OPERATIONS or intent.status != 'committed' or saved is None
            or saved.status != 'committed' or saved.resource_type != CONTROLLED_OPERATIONS[intent.operation_id][2]
            or saved.resource_id is None or saved.completed_at is None or saved.completed_at != intent.completed_at
            or saved.actor_id != intent.actor_id or intent.patient_id is not None or saved.patient_id is not None
            or saved.request_id != intent.request_id or saved.action != intent.operation_id
            or saved.target_key != intent.target_key or saved.payload_sha256 != intent.payload_sha256
            or saved.normalization_version != intent.normalization_version
            or saved.expected_version is None or int(saved.expected_version) < 1):
        raise ApiError('CONTROLLED_RECEIPT_INVALID', 409)
    controlled_payload(intent)
    keys = controlled_target(intent.operation_id, intent.target_key)
    if intent.operation_id not in CONFIG_OPERATIONS and intent.operation_id not in SOURCE_OPERATIONS and keys and saved.resource_id not in keys.values():
        raise ApiError('CONTROLLED_RECEIPT_INVALID', 409)
    if intent.operation_id in CONFIG_OPERATIONS:
        exists = configuration_receipt_resource(saved, intent.operation_id, keys, settings.SJIA_ENVIRONMENT)
        object_type = 'config_version' if saved.resource_type == 'config' else saved.resource_type
    elif intent.operation_id in SOURCE_OPERATIONS:
        exists = source_receipt_exists(saved, intent.operation_id, keys)
        object_type = saved.resource_type
    elif saved.resource_type == 'release':
        exists = ReleaseBundle.objects.filter(id=saved.resource_id, environment=settings.SJIA_ENVIRONMENT).exists()
        object_type = 'release_bundle'
    else:
        exists = ContactVersion.objects.filter(id=saved.resource_id, owner_id=intent.actor_id).exists()
        object_type = 'contact_version'
    audited = AuditEvent.objects.filter(actor_id=intent.actor_id, patient__isnull=True,
        action=intent.operation_id, object_type=object_type, object_id=saved.resource_id,
        object_version=str(saved.expected_version), request_id=intent.request_id, outcome='committed',
        retention_policy_id=intent.retention_policy_id, occurred_at=saved.completed_at).exists()
    if not exists or not audited:
        raise ApiError('CONTROLLED_RECEIPT_INVALID', 409)
    return {'resource_type': saved.resource_type, 'resource_id': str(saved.resource_id),
        'version': str(saved.expected_version), 'saved_at': saved.completed_at.isoformat(),
        'request_id': str(saved.request_id)}


def controlled_retry_available(intent, actor, gate, context):
    if intent.error_code == 'CONTROLLED_INTENT_CANCELLED':
        return False
    if intent.retention_policy.status != 'active' or intent.write_request_id is not None:
        return False
    if WriteRequest.objects.filter(actor=actor, action=intent.operation_id, target_key=intent.target_key,
            request_id=intent.request_id).exists():
        return False
    eligible = intent.status == 'failed' or (intent.status == 'prepared'
        and (intent.expires_at <= timezone.now() or intent.authorization_context != context))
    return eligible and controlled_target_ready(actor, gate, intent.operation_id, intent.target_key, intent.recovery_payload)


@transaction.atomic
def cancel_controlled_intent(request, intent):
    from audit.service import write_audit
    from pydantic import BaseModel, ConfigDict, Field

    class CancelInput(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        expected_generation: str = Field(pattern=r'^[1-9][0-9]*$')
        reason: str = Field(min_length=1, max_length=4096, pattern=r'\S')

    data = CancelInput.model_validate(request.data)
    actor, gate, _ = controlled_context(request, intent.operation_id)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor)
    controlled_payload(intent)
    if (str(intent.generation) != data.expected_generation or intent.write_request_id is not None
            or WriteRequest.objects.filter(actor=actor, action=intent.operation_id, target_key=intent.target_key, request_id=intent.request_id).exists()):
        raise ApiError('CONTROLLED_CANCEL_UNAVAILABLE', 409)
    if intent.status == 'failed' and intent.error_code == 'CONTROLLED_INTENT_CANCELLED':
        return intent
    if intent.status != 'prepared':
        raise ApiError('CONTROLLED_CANCEL_UNAVAILABLE', 409)
    if intent.operation_id in CONFIG_OPERATIONS:
        configuration_request_scope(actor, gate, intent.operation_id, controlled_target(intent.operation_id, intent.target_key), intent.recovery_payload)
    intent.status, intent.error_code, intent.completed_at = 'failed', 'CONTROLLED_INTENT_CANCELLED', timezone.now()
    intent.save(update_fields=['status', 'error_code', 'completed_at'])
    write_audit(actor=actor, action='controlled_intent_cancel', object_type='write_intent', object_id=intent.id,
        object_version=str(intent.generation), request_id=intent.request_id, outcome='cancelled',
        retention_policy=intent.retention_policy, occurred_at=intent.completed_at, reason_code=data.reason)
    return intent


@transaction.atomic
def prepare_controlled_intent(request, data, retention_policy):
    checked_controlled_input('IntentInput', data)
    actor, gate, context = controlled_context(request, data['operation_id'])
    checked_controlled_input(CONTROLLED_OPERATIONS[data['operation_id']][1], data['payload'])
    controlled_target(data['operation_id'], data['target_key'])
    if data['operation_id'] in CONFIG_OPERATIONS:
        configuration_request_scope(actor, gate, data['operation_id'],
            controlled_target(data['operation_id'], data['target_key']), data['payload'])
    retention_policy = RetentionPolicy.objects.select_for_update().get(id=retention_policy.id)
    if retention_policy.status != 'active':
        raise ApiError('RETENTION_POLICY_UNAVAILABLE', 409)
    existing = WriteIntent.objects.filter(actor=actor, operation_id=data['operation_id'],
        target_key=data['target_key'], request_id=data['request_id']).exists()
    if not existing and not controlled_target_ready(actor, gate, data['operation_id'], data['target_key'], data['payload']):
        raise ApiError('CONTROLLED_TARGET_CHANGED', 409)
    return prepare_intent(actor=actor, operation_id=data['operation_id'], target_key=data['target_key'],
        request_id=data['request_id'], payload=data['payload'], schema_version='1.0.0',
        retention_policy=retention_policy, authorization_context=context, recovery_payload=data['payload'])


def controlled_submit_intent(request, principal, operation):
    actor, gate, context = controlled_context(request, operation)
    if principal.id != actor.id:
        raise ApiError('CONTROLLED_ACTOR_MISMATCH', 403)
    headers = {'intent_id': request.headers.get('X-Write-Intent'), 'request_id': request.headers.get('Idempotency-Key'),
        'generation': request.headers.get('X-Intent-Generation')}
    schema = {'type': 'object', 'properties': {'intent_id': {'type': 'string', 'format': 'uuid'},
        'request_id': {'type': 'string', 'format': 'uuid'},
        'generation': {'type': 'string', 'pattern': '^[1-9][0-9]*$', 'maxLength': 19}}}
    if next(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(headers), None) is not None:
        raise ApiError('WRITE_INTENT_REQUIRED', 422)
    intent = WriteIntent.objects.select_for_update().filter(
        id=headers['intent_id'], actor=actor, operation_id=operation, target_key=request.path, patient__isnull=True).first()
    if (intent is None or str(intent.request_id) != headers['request_id'] or str(intent.generation) != headers['generation']
            or intent.authorization_context != context or controlled_payload(intent) != request.data):
        raise ApiError('WRITE_INTENT_MISMATCH', 409)
    policy = RetentionPolicy.objects.select_for_update().get(id=intent.retention_policy_id)
    if policy.status != 'active':
        raise ApiError('RETENTION_POLICY_UNAVAILABLE', 409)
    if intent.status == 'committed':
        if operation in CONFIG_OPERATIONS:
            configuration_request_scope(actor, gate, operation, controlled_target(operation, request.path), request.data)
        controlled_receipt(intent)
        return intent
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise ApiError('WRITE_INTENT_EXPIRED', 409)
    if WriteRequest.objects.filter(actor=actor, action=operation, target_key=request.path, request_id=intent.request_id).exists():
        raise ApiError('CONTROLLED_RESULT_UNCERTAIN', 409)
    if not controlled_target_ready(actor, gate, operation, request.path, request.data):
        raise ApiError('CONTROLLED_TARGET_CHANGED', 409)
    return intent


@transaction.atomic
def controlled_request_view(request, intent):
    actor, gate, context = controlled_context(request, intent.operation_id)
    intent = WriteIntent.objects.select_for_update().get(id=intent.id, actor=actor)
    payload = controlled_payload(intent)
    if intent.operation_id in CONFIG_OPERATIONS:
        configuration_request_scope(actor, gate, intent.operation_id,
            controlled_target(intent.operation_id, intent.target_key), payload)
    uncertain = intent.status != 'committed' and (intent.write_request_id is not None
        or WriteRequest.objects.filter(actor=actor, action=intent.operation_id, target_key=intent.target_key,
            request_id=intent.request_id).exists())
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': 'unknown' if uncertain else {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': controlled_retry_available(intent, actor, gate, context),
        'receipt': controlled_receipt(intent) if intent.status == 'committed' else None,
        'error_code': 'CONTROLLED_RESULT_UNCERTAIN' if uncertain else intent.error_code,
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'controlled_input': payload}


@transaction.atomic
def retry_controlled_intent(request, intent):
    data = checked_controlled_input('RetryInput', request.data)
    actor, gate, context = controlled_context(request, intent.operation_id)
    intent = WriteIntent.objects.select_for_update().select_related('retention_policy').get(id=intent.id, actor=actor)
    controlled_payload(intent)
    intent.retention_policy = RetentionPolicy.objects.select_for_update().get(id=intent.retention_policy_id)
    if str(intent.generation) != data['expected_generation'] or not controlled_retry_available(intent, actor, gate, context):
        raise ApiError('CONTROLLED_RETRY_UNAVAILABLE', 409)
    if intent.generation >= 9_223_372_036_854_775_807:
        raise ApiError('GENERATION_RANGE_EXCEEDED', 409)
    intent.generation += 1
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=300)
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.authorization_context = context
    intent.save(update_fields=['generation', 'prepared_at', 'expires_at', 'status', 'error_code', 'completed_at', 'authorization_context'])
    return intent


@transaction.atomic
def recent_controlled_requests(request):
    from api.views_archive_facts import fact_history_limit

    actor, gate = controlled_identity(request)
    limit, config = fact_history_limit(request, query_code='CONTROLLED_QUERY_INVALID')
    now = timezone.now()
    contexts = {}
    for action in sorted({item[0] for item in CONTROLLED_OPERATIONS.values()}):
        grant = ServiceRoleGrant.objects.select_for_update().filter(principal=actor, environment=gate.environment,
            action=action).order_by('-valid_from', '-id').first()
        if (grant is not None and grant.valid_from <= now and grant.evidence_ref.strip()
                and (grant.revoked_at is None or grant.revoked_at > now)
                and (grant.valid_until is None or grant.valid_until > now)):
            operation = next(key for key, value in CONTROLLED_OPERATIONS.items() if value[0] == action)
            contexts[action] = controlled_context(request, operation)[2]
    if not contexts:
        raise ApiError('SERVICE_ACTION_DENIED', 403)
    operations = [key for key, value in CONTROLLED_OPERATIONS.items() if value[0] in contexts]
    intents = WriteIntent.objects.filter(actor=actor, patient__isnull=True, operation_id__in=operations)
    aggregate = intents.aggregate(count=Count('id'), prepared=Max('prepared_at'), completed=Max('completed_at'), generation=Sum('generation'))
    snapshot = hashlib.sha256(json.dumps({'aggregate': aggregate, 'contexts': contexts, 'config': config.checksum,
        'actor': str(actor.id), 'environment': gate.environment, 'limit': limit}, sort_keys=True, default=str).encode()).hexdigest()
    cursor = request.query_params.get('cursor')
    if cursor:
        page = signing.loads(cursor, salt='controlled-requests', max_age=request.session.get_expiry_age())
        if not isinstance(page, dict) or set(page) != {'snapshot', 'prepared', 'id'} or page['snapshot'] != snapshot:
            raise ApiError('CURSOR_STALE', 409)
        intents = intents.filter(Q(prepared_at__lt=page['prepared']) | Q(prepared_at=page['prepared'], id__lt=page['id']))
    rows = list(intents.order_by('-prepared_at', '-id')[:limit + 1])
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = signing.dumps({'snapshot': snapshot, 'prepared': last.prepared_at.isoformat(), 'id': str(last.id)},
            salt='controlled-requests')
    return {'items': [controlled_request_view(request, item) for item in rows[:limit]],
        'next_cursor': next_cursor, 'snapshot_version': snapshot}
