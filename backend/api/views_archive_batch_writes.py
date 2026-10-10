from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from archives.batch_creation import batch_input, save_received_batch
from audit.service import write_audit
from files.models import FileObject
from files.purpose import checked_bytes
from requests.archive_batches import batch_receipt, submitted_batch_intent
from requests.archive_documents import family_identity
from requests.service import commit_intent


@api_view(['POST'])
@handle
def archive_batch_create(request, patient_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_BATCH_QUERY_INVALID', 422)
    batch_input(request.data)
    with transaction.atomic():
        intent, patient, pages, _ = submitted_batch_intent(request, actor, epoch)
        if intent.status == 'committed':
            return Response(batch_receipt(intent), status=201)
        sources = list(FileObject.objects.filter(patient=patient,
            id__in=[page.file_id for page in pages]).order_by('id'))
    for source in sources:
        checked_bytes(source)
    with transaction.atomic():
        intent, patient, pages, quota = submitted_batch_intent(request, actor, epoch)
        if intent.status == 'committed':
            return Response(batch_receipt(intent), status=201)
        batch = save_received_batch(actor=actor, patient=patient, request_id=intent.request_id, pages=pages, quota=quota)
        saved = commit_intent(intent=intent, action='archive_batch_create', resource_type='archive_batch',
            resource_id=batch.id, expected_version=1)
        write_audit(actor=actor, patient=patient, action='archive_batch_create', object_type='archive_batch',
            object_id=batch.id, object_version='1', request_id=intent.request_id, outcome='committed',
            retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
        return Response(batch_receipt(intent), status=201)
