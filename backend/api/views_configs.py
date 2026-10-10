from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_service, handle
from audit.service import write_audit
from configurations.lifecycle import CONFIG_OPERATIONS, configuration_scope, perform_configuration_operation
from configurations.models import ConfigVersion
from requests.controlled import checked_controlled_input, controlled_identity, controlled_submit_intent, controlled_receipt
from requests.service import commit_intent


def configuration_view(config):
    return {'id': str(config.id), 'version': str(config.version), 'checksum': config.checksum,
        'status': config.status, 'config': {'kind': config.kind, 'config_key': config.config_key,
            'schema_version': config.schema_version, 'payload': config.payload,
            'source_revision_id': str(config.source_revision_id) if config.source_revision_id else None}}


@api_view(['GET'])
@handle
@transaction.atomic
def config_read(request, config_id):
    actor, gate = controlled_identity(request)
    authorize_service(request, actor, 'manage_configs')
    if request.query_params:
        raise ApiError('CONFIG_QUERY_INVALID', 422)
    config = ConfigVersion.objects.select_for_update().get(id=config_id)
    if 'environment' in config.payload and config.payload['environment'] != gate.environment:
        raise ApiError('RESOURCE_NOT_FOUND', 404)
    configuration_scope(config, gate, actor)
    return Response(configuration_view(config))


def configuration_write(request, operation, keys):
    actor, gate = controlled_identity(request)
    action, schema, resource_type = CONFIG_OPERATIONS[operation]
    authorize_service(request, actor, action)
    payload = checked_controlled_input(schema, request.data)
    intent = controlled_submit_intent(request, actor, operation)
    http_status = 201 if operation in ('config_create', 'config_approve', 'config_validate') else 200
    if intent.status == 'committed':
        return Response(controlled_receipt(intent), status=http_status)
    resource, config = perform_configuration_operation(actor=actor, gate=gate,
        operation=operation, keys=keys, payload=payload)
    commit_intent(intent=intent, action=operation, resource_type=resource_type, resource_id=resource.id,
        expected_version=config.version)
    object_type = 'config_version' if resource_type == 'config' else resource_type
    write_audit(actor=actor, action=operation, object_type=object_type, object_id=resource.id,
        object_version=str(config.version), request_id=intent.request_id, outcome='committed',
        retention_policy=intent.retention_policy, occurred_at=intent.completed_at)
    return Response(controlled_receipt(intent), status=http_status)


@api_view(['POST'])
@handle
@transaction.atomic
def config_create(request):
    return configuration_write(request, 'config_create', {})


@api_view(['POST'])
@handle
@transaction.atomic
def config_submit(request, config_id):
    return configuration_write(request, 'config_submit', {'config_id': config_id})


@api_view(['POST'])
@handle
@transaction.atomic
def config_approve(request, config_id):
    return configuration_write(request, 'config_approve', {'config_id': config_id})


@api_view(['POST'])
@handle
@transaction.atomic
def config_approval_revoke(request, approval_id):
    return configuration_write(request, 'config_approval_revoke', {'approval_id': approval_id})


@api_view(['POST'])
@handle
@transaction.atomic
def config_validate(request, config_id):
    return configuration_write(request, 'config_validate', {'config_id': config_id})


@api_view(['POST'])
@handle
@transaction.atomic
def config_enable(request, config_id):
    return configuration_write(request, 'config_enable', {'config_id': config_id})


@api_view(['POST'])
@handle
@transaction.atomic
def config_disable(request, config_id):
    return configuration_write(request, 'config_disable', {'config_id': config_id})
