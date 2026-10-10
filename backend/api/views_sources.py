from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_service, handle
from audit.service import write_audit
from requests.controlled import controlled_identity, controlled_receipt, controlled_submit_intent
from requests.service import commit_intent
from sources.models import SourceRegistry, SourceRevision
from sources.operations import SOURCE_OPERATIONS, perform_source_operation
from retention.models import RetentionPolicy


def source_read_view(request, collection):
    actor, _ = controlled_identity(request)
    authorize_service(request, actor, 'manage_config')
    if request.query_params:
        raise ApiError('SOURCE_QUERY_INVALID', 422)
    if collection == 'source':
        return Response({'items': [{'id': str(source.id), 'source_key': source.source_key, 'owner': source.owner,
            'location': source.location, 'purpose': source.purpose, 'sensitivity': source.sensitivity,
            'update_frequency': source.update_frequency, 'status': source.status,
            'revisions': [{'id': str(value.id), 'version': value.version, 'status': value.status, 'checksum': value.checksum,
                'license': value.license, 'allowed_use': value.allowed_use, 'rights_evidence_ref': value.rights_evidence_ref,
                'retention_policy_id': str(value.retention_policy_id) if value.retention_policy_id else None,
                'captured_at': value.captured_at.isoformat()} for value in source.revisions.order_by('-captured_at', 'id')]}
            for source in SourceRegistry.objects.order_by('source_key', 'id')]})
    return Response({'items': [{'id': str(value.id), 'policy_key': value.policy_key, 'version': str(value.version),
        'data_class': value.data_class, 'schema_version': value.schema_version, 'rules': value.rules,
        'status': value.status, 'evidence_ref': value.evidence_ref,
        'approved_at': value.approved_at.isoformat() if value.approved_at else None}
        for value in RetentionPolicy.objects.order_by('policy_key', '-version', 'id')]})


def source_write(request, operation, keys):
    actor, gate = controlled_identity(request)
    authorize_service(request, actor, SOURCE_OPERATIONS[operation][0])
    intent = controlled_submit_intent(request, actor, operation)
    code = 201 if operation in ('source_register', 'source_revision_register', 'retention_policy_create') else 200
    if intent.status == 'committed':
        return Response(controlled_receipt(intent), status=code)
    resource, version = perform_source_operation(actor, gate, operation, keys, request.data)
    resource_type = SOURCE_OPERATIONS[operation][2]
    commit_intent(intent=intent, action=operation, resource_type=resource_type, resource_id=resource.id, expected_version=version)
    write_audit(actor=actor, action=operation, object_type=resource_type, object_id=resource.id,
        object_version=str(version), request_id=intent.request_id, outcome='committed', retention_policy=intent.retention_policy,
        occurred_at=intent.completed_at)
    return Response(controlled_receipt(intent), status=code)


@api_view(['GET', 'POST'])
@handle
@transaction.atomic
def source_register(request):
    return source_read_view(request, 'source') if request.method == 'GET' else source_write(request, 'source_register', {})


@api_view(['POST'])
@handle
@transaction.atomic
def source_revision_register(request, source_id):
    return source_write(request, 'source_revision_register', {'source_id': source_id})


@api_view(['POST'])
@handle
@transaction.atomic
def source_revision_approve(request, source_revision_id):
    return source_write(request, 'source_revision_approve', {'source_revision_id': source_revision_id})


@api_view(['POST'])
@handle
@transaction.atomic
def source_revision_revoke(request, source_revision_id):
    return source_write(request, 'source_revision_revoke', {'source_revision_id': source_revision_id})


@api_view(['GET', 'POST'])
@handle
@transaction.atomic
def retention_policy_create(request):
    return source_read_view(request, 'retention') if request.method == 'GET' else source_write(request, 'retention_policy_create', {})


@api_view(['POST'])
@handle
@transaction.atomic
def retention_policy_activate(request, policy_id):
    return source_write(request, 'retention_policy_activate', {'policy_id': policy_id})
