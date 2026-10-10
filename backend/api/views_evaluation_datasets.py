from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from audit.service import write_audit
from case_details.evaluation_datasets import create_dataset, mutate_dataset, operation_input
from case_details.evaluation_views import read_dataset, search_datasets
from requests.evaluation_datasets import dataset_receipt, evaluation_identity, submitted_evaluation_intent
from requests.service import commit_intent


@api_view(['POST'])
@handle
@transaction.atomic
def dataset_search(request):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DATASET_QUERY_INVALID', 422)
    return Response(search_datasets(actor=actor, auth_epoch=epoch, payload=request.data,
        expires_at=request.session.get_expiry_date()))


@api_view(['GET'])
@handle
@transaction.atomic
def dataset_read(request, dataset_id):
    actor, epoch = evaluation_identity(request)
    version = None
    if request.query_params:
        if set(request.query_params) != {'version'} or len(request.query_params.getlist('version')) != 1:
            raise ApiError('DATASET_QUERY_INVALID', 422)
        version = request.query_params['version']
        if (not version.isascii() or not version.isdecimal() or version.startswith('0')
                or len(version) > 19 or int(version) > 9223372036854775807):
            raise ApiError('DATASET_QUERY_INVALID', 422)
        version = int(version)
    return Response(read_dataset(dataset_id=dataset_id, actor=actor, auth_epoch=epoch, version=version))


def dataset_write(request, operation, dataset_id=None):
    actor, epoch = evaluation_identity(request)
    if request.query_params:
        raise ApiError('DATASET_QUERY_INVALID', 422)
    payload = operation_input(operation, request.data)
    intent = submitted_evaluation_intent(request, actor, epoch, operation)
    if intent.status != 'committed':
        if operation == 'dataset_create':
            row = create_dataset(payload=payload, actor=actor, auth_epoch=epoch)
        else:
            row = mutate_dataset(operation=operation, dataset_id=dataset_id, payload=payload,
                actor=actor, auth_epoch=epoch)
        saved = commit_intent(intent=intent, action=operation, resource_type='evaluation_dataset',
            resource_id=row.dataset_id, expected_version=row.version)
        write_audit(actor=actor, action=operation, object_type='evaluation_dataset', object_id=row.dataset_id,
            object_version=str(row.version), request_id=intent.request_id, outcome='committed',
            retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(dataset_receipt(intent), status=200 if operation == 'dataset_freeze' else 201)


@api_view(['POST'])
@handle
@transaction.atomic
def dataset_create(request):
    return dataset_write(request, 'dataset_create')


@api_view(['POST'])
@handle
@transaction.atomic
def dataset_partition(request, dataset_id):
    return dataset_write(request, 'dataset_partition', dataset_id)


@api_view(['POST'])
@handle
@transaction.atomic
def dataset_label(request, dataset_id):
    return dataset_write(request, 'dataset_label', dataset_id)


@api_view(['POST'])
@handle
@transaction.atomic
def dataset_freeze(request, dataset_id):
    return dataset_write(request, 'dataset_freeze', dataset_id)
