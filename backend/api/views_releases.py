import json
from pathlib import Path

from django.conf import settings
from django.db import connection, transaction
from jsonschema import Draft202012Validator, FormatChecker
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_service, current_principal, handle
from audit.service import write_audit
from authorization.models import ServiceRoleGrant
from configurations.models import ConfigVersion
from contacts.models import ContactVersion
from family_content.models import EnvironmentGate, ReleaseBundle, ReleaseComponent
from family_content.service import add_release_component, approve_release, create_release_bundle, disable_release, enable_release
from identity.models import Principal
from requests.controlled import controlled_submit_intent, controlled_receipt
from requests.service import commit_intent


PROTOCOL = json.loads((Path(__file__).resolve().parents[2] / 'backend-design' / 'protocol.schema.json').read_text())


def checked_input(name, data):
    validator = Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'}, format_checker=FormatChecker())
    if next(validator.iter_errors(data), None) is not None:
        raise ApiError('RELEASE_INPUT_INVALID', 422)
    return data


def release_principal(request, *, writing=False):
    principal, _, _ = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    authorize_service(request, principal, 'publish_config')
    gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
    if writing:
        gate = EnvironmentGate.objects.select_for_update().get(environment=gate.environment)
        Principal.objects.select_for_update().get(id=principal.id)
        list(ServiceRoleGrant.objects.select_for_update().filter(principal=principal, environment=gate.environment).order_by('id'))
        principal, _, _ = current_principal(request)
        if principal is None:
            raise ApiError('AUTH_REQUIRED', 401)
        authorize_service(request, principal, 'publish_config')
    return principal, gate


def release_intent(request, principal, operation):
    return controlled_submit_intent(request, principal, operation)


def release_receipt(intent):
    return controlled_receipt(intent)


def save_release_receipt(intent, principal, release, action):
    commit_intent(intent=intent, action=action, resource_type='release', resource_id=release.id,
        expected_version=str(release.version))
    write_audit(actor=principal, action=action, object_type='release_bundle', object_id=release.id,
        object_version=str(release.version), request_id=intent.request_id, outcome='committed',
        retention_policy=intent.retention_policy, occurred_at=intent.completed_at)
    return release_receipt(intent)


def release_view(release):
    return {'release_id': str(release.id), 'version': str(release.version), 'status': release.status,
        'publication_epoch': str(release.publication_epoch), 'release': {
            'environment': release.environment, 'contact_version_id': str(release.contact_version_id),
            'validation_config_id': str(release.validation_config_id),
            'components': [{'kind': item.component_kind, 'config_id': str(item.config_id)}
                for item in ReleaseComponent.objects.filter(release=release).order_by('component_kind', 'config_id')]}}


@api_view(['GET'])
@handle
@transaction.atomic
def release_read(request, release_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    _, gate = release_principal(request)
    if request.query_params:
        raise ApiError('RELEASE_QUERY_INVALID', 422)
    release = ReleaseBundle.objects.get(id=release_id, environment=gate.environment)
    return Response(release_view(release))


@api_view(['POST'])
@handle
@transaction.atomic
def release_create(request):
    principal, gate = release_principal(request, writing=True)
    data = checked_input('ReleaseInput', request.data)
    if data['environment'] != gate.environment:
        raise ApiError('RELEASE_ENVIRONMENT_MISMATCH', 409)
    intent = release_intent(request, principal, 'release_create')
    if intent.status == 'committed':
        return Response(release_receipt(intent), status=201)
    contact = ContactVersion.objects.get(id=data['contact_version_id'])
    validation = ConfigVersion.objects.get(id=data['validation_config_id'])
    release = create_release_bundle(environment=gate.environment, contact_version=contact, validation_config=validation)
    for item in data['components']:
        config = ConfigVersion.objects.get(id=item['config_id'])
        add_release_component(release=release, component_kind=item['kind'], config=config)
    return Response(save_release_receipt(intent, principal, release, 'release_create'), status=201)


@api_view(['POST'])
@handle
@transaction.atomic
def release_approve(request, release_id):
    principal, gate = release_principal(request, writing=True)
    data = checked_input('StateAction', request.data)
    intent = release_intent(request, principal, 'release_approve')
    if intent.status == 'committed':
        return Response(release_receipt(intent))
    release = ReleaseBundle.objects.select_for_update().get(id=release_id, environment=gate.environment)
    if release.status != data['expected_state'] or (
            'expected_version' in data and str(release.version) != data['expected_version']):
        raise ApiError('VERSION_CONFLICT', 409)
    approve_release(release=release)
    return Response(save_release_receipt(intent, principal, release, 'release_approve'))


@api_view(['POST'])
@handle
@transaction.atomic
def release_enable(request, release_id):
    principal, gate = release_principal(request, writing=True)
    data = checked_input('GateAction', request.data)
    intent = release_intent(request, principal, 'release_enable')
    if intent.status == 'committed':
        return Response(release_receipt(intent))
    release = ReleaseBundle.objects.get(id=release_id, environment=gate.environment)
    enable_release(release=release, environment_gate=gate, expected_gate_version=int(data['expected_gate_version']))
    return Response(save_release_receipt(intent, principal, release, 'release_enable'))


@api_view(['POST'])
@handle
@transaction.atomic
def release_disable(request, release_id):
    principal, gate = release_principal(request, writing=True)
    data = checked_input('GateAction', request.data)
    intent = release_intent(request, principal, 'release_disable')
    if intent.status == 'committed':
        return Response(release_receipt(intent))
    release = ReleaseBundle.objects.get(id=release_id, environment=gate.environment)
    disable_release(release=release, environment_gate=gate, expected_gate_version=int(data['expected_gate_version']))
    return Response(save_release_receipt(intent, principal, release, 'release_disable'))
