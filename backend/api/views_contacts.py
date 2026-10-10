from django.conf import settings
from django.db import transaction
from jsonschema import Draft202012Validator, FormatChecker
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_service, current_principal, handle
from api.views_releases import PROTOCOL, release_intent, release_receipt
from audit.service import write_audit
from configurations.models import ConfigVersion
from contacts.models import ContactVersion
from contacts.service import activate_saved_contact, disable_saved_contact, save_contact
from family_content.models import EnvironmentGate, ReleaseBundle
from requests.service import commit_intent


def contact_input(name, data):
    validator = Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
    if next(validator.iter_errors(data), None) is not None:
        raise ApiError('CONTACT_INPUT_INVALID', 422)
    return data


def contact_principal(request):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    authorize_service(request, principal, 'manage_contacts')
    gate = EnvironmentGate.objects.select_for_update().get(environment=settings.SJIA_ENVIRONMENT)
    return principal, gate


def contact_for_change(contact_version_id, principal, gate):
    contact = ContactVersion.objects.select_for_update().get(id=contact_version_id)
    if contact.owner_id != principal.id:
        raise ApiError('CONTACT_OWNER_MISMATCH', 403)
    if ReleaseBundle.objects.filter(contact_version=contact).exclude(environment=gate.environment).exists():
        raise ApiError('CONTACT_ENVIRONMENT_MISMATCH', 409)
    if contact.approval_config.schema_version == 'contact-1' and (
            contact.approval_config.payload.get('environment') != gate.environment):
        raise ApiError('CONTACT_ENVIRONMENT_MISMATCH', 409)
    return contact


def contact_receipt(intent, principal, contact, action):
    commit_intent(intent=intent, action=action, resource_type='contact_version', resource_id=contact.id,
        expected_version=str(contact.version))
    write_audit(actor=principal, action=action, object_type='contact_version', object_id=contact.id,
        object_version=str(contact.version), request_id=intent.request_id, outcome='committed',
        retention_policy=intent.retention_policy, occurred_at=intent.completed_at)
    return release_receipt(intent)


@api_view(['POST'])
@handle
@transaction.atomic
def contact_create(request):
    principal, gate = contact_principal(request)
    data = contact_input('ContactInput', request.data)
    intent = release_intent(request, principal, 'contact_create')
    if intent.status == 'committed':
        return Response(release_receipt(intent), status=201)
    config = ConfigVersion.objects.get(id=data['approval_config_id'])
    payload = {key: value for key, value in data.items() if key != 'approval_config_id'}
    contact = save_contact(owner=principal, gate=gate, payload=payload, approval_config=config)
    return Response(contact_receipt(intent, principal, contact, 'contact_create'), status=201)


@api_view(['POST'])
@handle
@transaction.atomic
def contact_activate(request, contact_version_id):
    principal, gate = contact_principal(request)
    data = contact_input('GateAction', request.data)
    intent = release_intent(request, principal, 'contact_activate')
    if intent.status == 'committed':
        return Response(release_receipt(intent))
    contact = contact_for_change(contact_version_id, principal, gate)
    activate_saved_contact(contact=contact, gate=gate, expected_gate_version=int(data['expected_gate_version']))
    return Response(contact_receipt(intent, principal, contact, 'contact_activate'))


@api_view(['POST'])
@handle
@transaction.atomic
def contact_disable(request, contact_version_id):
    principal, gate = contact_principal(request)
    data = contact_input('StateAction', request.data)
    intent = release_intent(request, principal, 'contact_disable')
    if intent.status == 'committed':
        return Response(release_receipt(intent))
    contact = contact_for_change(contact_version_id, principal, gate)
    disable_saved_contact(contact=contact, gate=gate, expected_state=data['expected_state'],
        expected_version=data.get('expected_version'))
    return Response(contact_receipt(intent, principal, contact, 'contact_disable'))
