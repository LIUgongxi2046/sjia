from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import handle
from archives.grouping import save_document
from archives.inputs import document_input
from audit.service import write_audit
from requests.archive_documents import OPERATIONS, family_identity, submitted_document_intent, document_receipt
from requests.service import commit_intent


def document_write(request, operation):
    actor, epoch = family_identity(request)
    payload = document_input(OPERATIONS[operation], request.data)
    intent, patient, document = submitted_document_intent(request, actor, epoch, operation)
    if intent.status != 'committed':
        document, version = save_document(patient=patient, actor=actor, payload=payload, document=document)
        saved = commit_intent(intent=intent, action=operation, resource_type='archive_document',
            resource_id=document.id, expected_version=version.version)
        write_audit(actor=actor, patient=patient, action=operation, object_type='archive_document',
            object_id=document.id, object_version=str(version.version), request_id=intent.request_id,
            outcome='committed', retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(document_receipt(intent), status=201 if operation == 'archive_document_create' else 200)


@api_view(['POST'])
@handle
@transaction.atomic
def archive_document_create(request, patient_id):
    return document_write(request, 'archive_document_create')


@api_view(['POST'])
@handle
@transaction.atomic
def archive_document_group(request, document_id):
    return document_write(request, 'archive_document_group')
