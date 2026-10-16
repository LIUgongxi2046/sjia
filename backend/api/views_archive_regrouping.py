from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, get_patient, handle
from api.views_archive_documents import authorize_document
from archives.documents import document_read
from archives.fact_candidates import validate_value
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion
from archives.regrouping import OPERATION, regroup_input, save_regrouping
from audit.service import write_audit
from requests.archive_documents import family_identity
from requests.archive_regrouping import submitted_regroup_intent, regroup_receipt
from requests.service import commit_intent


@api_view(['POST'])
@handle
@transaction.atomic
def archive_documents_regroup(request, patient_id):
    actor, epoch = family_identity(request)
    payload = regroup_input(request.data)
    intent, scope = submitted_regroup_intent(request, actor, epoch)
    if scope['patient'].id != patient_id:
        raise ApiError('REGROUP_PATIENT_MISMATCH', 409)
    if intent.status != 'committed':
        row = save_regrouping(actor=actor, payload=payload, scope=scope, request_id=intent.request_id,
            input_sha256=intent.payload_sha256)
        saved = commit_intent(intent=intent, action=OPERATION, resource_type='archive_regrouping',
            resource_id=row.id, expected_version=1)
        write_audit(actor=actor, patient=scope['patient'], action=OPERATION, object_type='archive_regrouping',
            object_id=row.id, object_version='1', request_id=intent.request_id, outcome='committed',
            retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(regroup_receipt(intent))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_regroup_options(request, batch_id):
    actor, _ = family_identity(request)
    if request.query_params:
        raise ApiError('REGROUP_QUERY_INVALID', 422)
    batch = ArchiveBatch.objects.get(id=batch_id)
    patient = get_patient(batch.patient_id)
    # The batch author scope is checked before directory enumeration.
    from api.base import authorize_case
    authorize_case(request, actor, patient, 'read_raw', author_id=batch.submitted_by,
        require_relation=True, whole_resource=True)
    identifiers = ArchiveDocumentVersion.objects.filter(batch_id=batch.id, patient_id=patient.id).values('document_id')
    result = []
    for document in ArchiveDocument.objects.filter(id__in=identifiers, patient_id=patient.id,
            retired_at__isnull=True).order_by('id'):
        version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
            version=document.current_version)
        if version.batch_id != batch.id:
            continue
        try:
            authorize_document(request, actor, patient, document, version, 'read_raw')
            authorize_document(request, actor, patient, document, version, 'write_record')
        except ApiError as error:
            if error.http_status == 403:
                continue
            raise
        value = document_read(document=document, patient=patient, version=version)
        value['allowed_actions'] = ['archive_document_group']
        result.append(value)
    return Response(validate_value('RegroupOptions', {'patient_id': str(patient.id), 'batch_id': str(batch.id), 'documents': result}))
