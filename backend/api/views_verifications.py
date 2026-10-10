from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from audit.service import write_audit
from requests.evaluation_datasets import evaluation_identity
from requests.service import commit_intent
from requests.verifications import committed_resource, submitted_verification_intent, verification_request_view, verification_target
from verification.workflow import apply_verification, read_verification_resource, verification_creation_scope, verification_options, verification_originals, verification_original_content, verification_query


def submit(request, operation):
    actor, epoch = evaluation_identity(request)
    intent = submitted_verification_intent(request, actor, epoch, operation)
    if intent.status != 'committed':
        row = apply_verification(operation, verification_target(operation, intent.target_key), request.data,
            actor, epoch, settings.SJIA_ENVIRONMENT)
        kind = 'verification_request' if operation == 'verification_v2_request_create' else 'verification'
        saved = commit_intent(intent=intent, action=operation, resource_type=kind, resource_id=row.id, expected_version=1)
        write_audit(actor=actor, patient=intent.patient, action=operation, object_type=kind, object_id=row.id,
            object_version='1', request_id=intent.request_id, outcome='committed', retention_policy=intent.retention_policy,
            occurred_at=saved.completed_at)
    committed_resource(intent)
    return Response(verification_request_view(intent, None)['receipt'], status=200 if operation == 'verification_v2_revoke' else 201)


@api_view(['POST'])
@handle
@transaction.atomic
def verification_request_create(request):
    return submit(request, 'verification_v2_request_create')


@api_view(['POST'])
@handle
@transaction.atomic
def verification_submit(request):
    return submit(request, 'verification_v2_submit')


@api_view(['POST'])
@handle
@transaction.atomic
def verification_revoke(request, verification_id):
    return submit(request, 'verification_v2_revoke')


def read(request, kind, resource_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('VERIFICATION_QUERY_INVALID', 422)
    return Response(read_verification_resource(kind, resource_id, actor, epoch, settings.SJIA_ENVIRONMENT))


@api_view(['GET'])
@handle
@transaction.atomic
def verification_request_read(request, verification_request_id):
    return read(request, 'verification_request', verification_request_id)


@api_view(['GET'])
@handle
@transaction.atomic
def verification_read(request, verification_id):
    return read(request, 'verification', verification_id)


@api_view(['GET'])
@handle
@transaction.atomic
def verification_options_read(request, verification_request_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('VERIFICATION_QUERY_INVALID', 422)
    return Response(verification_options(verification_request_id, actor, epoch, settings.SJIA_ENVIRONMENT))


@api_view(['GET'])
@handle
@transaction.atomic
def verification_creation_scope_read(request, record_version_id):
    actor, epoch = evaluation_identity(request)
    purpose = verification_query('VerificationCreationQuery', request.query_params)['purpose']
    return Response(verification_creation_scope(record_version_id, purpose, actor, epoch, settings.SJIA_ENVIRONMENT))


@api_view(['GET'])
@handle
@transaction.atomic
def verification_originals_read(request, resource_kind, resource_id):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('VERIFICATION_QUERY_INVALID', 422)
    return Response(verification_originals(resource_kind, resource_id, actor, epoch, settings.SJIA_ENVIRONMENT))


@api_view(['GET'])
@handle
@transaction.atomic
def verification_original_content_read(request, resource_kind, resource_id, file_id):
    actor, epoch = evaluation_identity(request)
    query = verification_query('VerificationOriginalQuery', request.query_params)
    media_type, data = verification_original_content(resource_kind, resource_id, file_id, actor, epoch,
        settings.SJIA_ENVIRONMENT, query['checksum'], query['object_version'])
    return HttpResponse(data, content_type=media_type, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
