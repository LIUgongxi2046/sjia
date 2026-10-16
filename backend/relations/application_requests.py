from uuid import UUID
from datetime import timedelta

from django.db import transaction
from django.urls import resolve, reverse
from django.utils import timezone

from authorization.policy import check_principal_session
from identity.models import Principal
from relations.inputs import ApplicationInput, ApplicationAction, ApplicationIntentInput, ApplicationWriteHeaders
from relations.models import RelationApplication
from relations.service import RelationRejected, create_application, withdraw_application
from requests.models import WriteIntent
from requests.service import prepare_intent, compute_digest, commit_intent


APPLICATION_OPERATIONS = ('application_create', 'application_withdraw')


def application_payload(operation, payload):
    if operation == 'application_create':
        # Omitting the additive declaration must retain old intent hashes exactly.
        return ApplicationInput.model_validate(payload).model_dump(exclude_unset=True)
    if operation == 'application_withdraw':
        return ApplicationAction.model_validate(payload).model_dump()
    raise RelationRejected('APPLICATION_OPERATION_INVALID')


def intent_view(intent):
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'generation': str(intent.generation),
        'status': intent.status, 'expires_at': intent.expires_at.isoformat(), 'payload_sha256': intent.payload_sha256}


def application_receipt(intent):
    saved = intent.write_request
    if (intent.status != 'committed' or saved is None or saved.status != 'committed' or saved.resource_type != 'relation_application'
        or saved.resource_id is None or saved.completed_at is None or saved.request_id != intent.request_id
        or saved.actor_id != intent.actor_id or saved.patient_id is not None or saved.target_key != intent.target_key or saved.action != intent.operation_id
        or saved.payload_sha256 != intent.payload_sha256):
        raise RelationRejected('APPLICATION_RECEIPT_INVALID')
    if intent.operation_id == 'application_withdraw' and saved.expected_version is None:
        raise RelationRejected('APPLICATION_RECEIPT_INVALID')
    if not RelationApplication.objects.filter(id=saved.resource_id, applicant_id=intent.actor_id).exists():
        raise RelationRejected('APPLICATION_NOT_FOUND')
    version = 1 if intent.operation_id == 'application_create' else saved.expected_version + 1
    return {'resource_type': saved.resource_type, 'resource_id': str(saved.resource_id), 'version': str(version),
        'saved_at': saved.completed_at.isoformat(), 'request_id': str(saved.request_id)}


def application_request_view(intent):
    if intent.operation_id not in APPLICATION_OPERATIONS or intent.patient_id is not None:
        raise RelationRejected('APPLICATION_REQUEST_INVALID')
    payload = None
    if intent.recovery_payload is not None:
        payload = application_payload(intent.operation_id, intent.recovery_payload)
        if intent.payload_sha256 != compute_digest(intent.operation_id, intent.target_key, '1.0.0', payload):
            raise RelationRejected('APPLICATION_REQUEST_PAYLOAD_INVALID')
    committed = intent.status == 'committed'
    authorization_changed = intent.authorization_context != {'audience': 'family', 'auth_epoch': str(intent.actor.auth_epoch)}
    may_retry = payload is not None and intent.write_request_id is None and (intent.status == 'failed'
        or (intent.status == 'prepared' and (intent.expires_at <= timezone.now() or authorization_changed)))
    if may_retry and intent.operation_id == 'application_withdraw':
        route = resolve(intent.target_key)
        if route.url_name != intent.operation_id or intent.target_key != reverse(intent.operation_id, kwargs=route.kwargs):
            raise RelationRejected('APPLICATION_TARGET_INVALID')
        may_retry = RelationApplication.objects.filter(id=route.kwargs['application_id'], applicant_id=intent.actor_id,
            status=payload['expected_state'], version=int(payload['expected_version'])).exists()
    return {'intent_id': str(intent.id), 'request_id': str(intent.request_id), 'operation_id': intent.operation_id,
        'target_key': intent.target_key, 'prepared_at': intent.prepared_at.isoformat(),
        'state': {'prepared': 'pending', 'committed': 'committed', 'failed': 'failed'}[intent.status],
        'may_retry': may_retry,
        'receipt': application_receipt(intent) if committed else None, 'error_code': intent.error_code,
        'generation': str(intent.generation), 'expires_at': intent.expires_at.isoformat(),
        'payload_sha256': intent.payload_sha256, 'application_input': payload}


@transaction.atomic
def prepare_application_intent(*, actor, auth_epoch, audience, data, retention_policy):
    if audience != 'family':
        raise RelationRejected('FAMILY_AUDIENCE_REQUIRED')
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    data = ApplicationIntentInput.model_validate(data)
    route = resolve(data.target_key)
    if route.url_name != data.operation_id or data.target_key != reverse(data.operation_id, kwargs=route.kwargs):
        raise RelationRejected('APPLICATION_TARGET_INVALID')
    payload = application_payload(data.operation_id, data.payload)
    if data.operation_id == 'application_withdraw':
        application = RelationApplication.objects.select_for_update().filter(id=route.kwargs['application_id'], applicant=actor).first()
        if application is None:
            raise RelationRejected('APPLICATION_NOT_FOUND')
        if application.status != payload['expected_state'] or str(application.version) != payload['expected_version']:
            raise RelationRejected('VERSION_CONFLICT')
    return prepare_intent(actor=actor, operation_id=data.operation_id, target_key=data.target_key,
        request_id=UUID(data.request_id), payload=payload, schema_version='1.0.0', retention_policy=retention_policy,
        authorization_context={'audience': audience, 'auth_epoch': str(auth_epoch)}, recovery_payload=payload)


@transaction.atomic
def submit_application(*, actor, auth_epoch, audience, operation, target, data, headers, application_id=None):
    if audience != 'family':
        raise RelationRejected('FAMILY_AUDIENCE_REQUIRED')
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    data = application_payload(operation, data)
    headers = ApplicationWriteHeaders.model_validate(headers)
    intent = WriteIntent.objects.select_for_update().filter(id=headers.intent_id,
        actor=actor, patient__isnull=True, operation_id=operation, target_key=target).first()
    if intent is None:
        raise RelationRejected('APPLICATION_INTENT_NOT_FOUND')
    if str(intent.request_id) != headers.request_id or str(intent.generation) != headers.generation:
        raise RelationRejected('APPLICATION_INTENT_MISMATCH')
    if intent.authorization_context != {'audience': audience, 'auth_epoch': str(auth_epoch)}:
        raise RelationRejected('APPLICATION_AUTHORIZATION_CHANGED')
    if intent.recovery_payload != data or intent.payload_sha256 != compute_digest(operation, target, '1.0.0', data):
        raise RelationRejected('APPLICATION_INTENT_MISMATCH')
    if intent.status == 'committed':
        if not RelationApplication.objects.filter(id=intent.write_request.resource_id, applicant=actor).exists():
            raise RelationRejected('APPLICATION_NOT_FOUND')
        return application_receipt(intent)
    if intent.status != 'prepared' or intent.expires_at <= timezone.now():
        raise RelationRejected('APPLICATION_INTENT_EXPIRED')
    if operation == 'application_create':
        application = create_application(applicant=actor, **data)
        expected_version = None
    else:
        application = RelationApplication.objects.select_for_update().filter(id=application_id, applicant=actor).first()
        if application is None:
            raise RelationRejected('APPLICATION_NOT_FOUND')
        expected_version = int(data['expected_version'])
        application = withdraw_application(application=application, expected_version=expected_version)
        application.reason = data['reason']
        application.save(update_fields=['reason'])
    commit_intent(intent=intent, action=operation, resource_type='relation_application', resource_id=application.id,
        expected_version=expected_version)
    return application_receipt(intent)


@transaction.atomic
def retry_application_intent(*, actor, auth_epoch, audience, intent_id, expected_generation):
    actor = Principal.objects.select_for_update().get(id=actor.id)
    check_principal_session(actor, auth_epoch)
    if audience != 'family':
        raise RelationRejected('FAMILY_AUDIENCE_REQUIRED')
    intent = WriteIntent.objects.select_for_update().filter(id=intent_id, actor=actor, patient__isnull=True,
        operation_id__in=APPLICATION_OPERATIONS).first()
    if intent is None:
        raise RelationRejected('APPLICATION_INTENT_NOT_FOUND')
    if str(intent.generation) != expected_generation or not application_request_view(intent)['may_retry'] or intent.write_request_id is not None:
        raise RelationRejected('APPLICATION_RETRY_NOT_AVAILABLE')
    if intent.operation_id == 'application_withdraw':
        payload = application_payload(intent.operation_id, intent.recovery_payload)
        route = resolve(intent.target_key)
        application = RelationApplication.objects.select_for_update().filter(id=route.kwargs['application_id'], applicant=actor).first()
        if application is None:
            raise RelationRejected('APPLICATION_NOT_FOUND')
        if application.status != payload['expected_state'] or str(application.version) != payload['expected_version']:
            raise RelationRejected('VERSION_CONFLICT')
    intent.generation += 1
    if intent.generation > 9_223_372_036_854_775_807:
        raise RelationRejected('GENERATION_RANGE_EXCEEDED')
    intent.prepared_at = timezone.now()
    intent.expires_at = intent.prepared_at + timedelta(seconds=300)
    intent.status, intent.error_code, intent.completed_at = 'prepared', None, None
    intent.authorization_context = {'audience': audience, 'auth_epoch': str(auth_epoch)}
    intent.save(update_fields=['generation', 'prepared_at', 'expires_at', 'status', 'error_code', 'completed_at', 'authorization_context'])
    return intent
