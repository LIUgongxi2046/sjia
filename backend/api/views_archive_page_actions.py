from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from archives.page_actions import save_action
from audit.service import write_audit
from requests.archive_documents import family_identity
from requests.archive_page_actions import receipt, submitted_action
from requests.service import commit_intent


def save(request, operation):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_PAGE_ACTION_QUERY_INVALID', 422)
    intent, result = submitted_action(request, actor, epoch, operation)
    if intent.status == 'committed':
        return Response(receipt(intent), status=200 if operation == 'archive_page_cancel' else 202)
    actor, patient, batch, pages, quota, _, context = result
    kind = 'archive_batch' if operation == 'archive_batch_process' else 'archive_page'
    resource = batch if kind == 'archive_batch' else pages[0]
    version = batch.version + 1 if kind == 'archive_batch' else pages[0].status_version + 1
    saved = commit_intent(intent=intent, action=operation, resource_type=kind,
        resource_id=resource.id, expected_version=version)
    save_action(actor=actor, patient=patient, batch=batch, pages=pages, quota=quota,
        context=context, request_row=saved, operation=operation)
    write_audit(actor=actor, patient=patient, action=operation, object_type=kind, object_id=resource.id,
        object_version=str(version), request_id=intent.request_id, outcome='committed',
        retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(receipt(intent), status=200 if operation == 'archive_page_cancel' else 202)


@api_view(['POST'])
@handle
@transaction.atomic
def archive_batch_process(request, batch_id):
    return save(request, 'archive_batch_process')


@api_view(['POST'])
@handle
@transaction.atomic
def archive_page_retry(request, page_id):
    return save(request, 'archive_page_retry')


@api_view(['POST'])
@handle
@transaction.atomic
def archive_page_cancel(request, page_id):
    return save(request, 'archive_page_cancel')
