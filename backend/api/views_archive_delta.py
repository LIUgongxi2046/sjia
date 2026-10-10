from django.db import connection, transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from api.views_archive_documents import authorize_document
from archives.models import ArchiveDocument, ArchiveDocumentVersion, DeltaSummary
from archives.documents import document_read
from archives.search import document_scope, verification_status
from archives.search_schemas import checked_search
from api.base import get_patient
from audit.service import write_audit
from requests.archive_delta import delta_receipt, submitted_delta_intent
from requests.archive_documents import family_identity
from requests.service import commit_intent
from summaries.delta import generate_delta, read_delta
from summaries.delta_schemas import checked_delta
from tasks.models import Task
from files.models import FileGroup
from files.purpose import lock_use_authorization
from django.conf import settings


@api_view(['POST'])
@handle
@transaction.atomic
def archive_delta_create(request, batch_id):
    actor, epoch = family_identity(request)
    intent, patient, context, materials, facts, relations = submitted_delta_intent(request, actor, epoch)
    if intent.status != 'committed':
        result, task = generate_delta(actor, epoch, patient, context, materials, facts, relations)
        saved = commit_intent(intent=intent, action='archive_delta_create', resource_type='archive_delta', resource_id=result.id, expected_version=1)
        task.request_row = saved
        task.save(update_fields=['request_row'])
        write_audit(actor=actor, patient=patient, action='archive_delta_create', object_type='archive_delta', object_id=result.id,
            object_version='1', request_id=intent.request_id, outcome='committed', retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(delta_receipt(intent), status=202)


@api_view(['GET'])
@handle
@transaction.atomic
def archive_delta_read(request, delta_id):
    if request.query_params:
        raise ApiError('ARCHIVE_DELTA_QUERY_INVALID', 422)
    actor, epoch = family_identity(request)
    return Response(read_delta(request, actor, epoch, DeltaSummary.objects.select_for_update().get(id=delta_id, owner_id=actor.id)))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_delta_task(request, task_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_DELTA_QUERY_INVALID', 422)
    task = Task.objects.get(id=task_id, actor=actor, schema_version='archive-delta-1')
    result = DeltaSummary.objects.select_for_update().get(task_id=task.id, owner_id=actor.id, patient_id=task.patient_id)
    view = read_delta(request, actor, epoch, result)
    task.refresh_from_db()
    return Response(checked_delta('Task', {'task_id': str(task.id), 'patient_id': str(task.patient_id), 'status': view['status'],
        'status_version': str(task.status_version), 'result_type': 'archive_delta',
        'result_ref': str(result.id), 'error_code': 'SOURCES_OR_PERMISSIONS_CHANGED' if view['validity'] == 'invalid' else None}))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_document_version_source(request, version_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    actor, _ = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_DELTA_QUERY_INVALID', 422)
    version = ArchiveDocumentVersion.objects.get(id=version_id)
    document = ArchiveDocument.objects.get(id=version.document_id, patient_id=version.patient_id)
    patient = get_patient(version.patient_id)
    authorize_document(request, actor, patient, document, version, 'read_raw')
    if document.current_version != version.version:
        authorize_document(request, actor, patient, document, version, 'read_history')
    scope = document_scope(actor, patient, document, version)
    if scope is None:
        raise ApiError('ARCHIVE_DELTA_SCOPE_DENIED', 403)
    files = {file.id: file for file in scope['files']}
    for file in files.values():
        group = FileGroup.objects.get(id=file.file_group_id, patient=patient)
        lock_use_authorization(group.use_authorization_id, file, 'family_observation', settings.SJIA_ENVIRONMENT)
    pages = [{'page_id': str(page.id), 'file_page_id': str(page.file_page_id), 'file_id': str(page.file_id),
        'page_order': index + 1, 'object_version': page.file_object_version, 'checksum': page.checksum,
        'status': files[page.file_id].status, 'media_type': files[page.file_id].media_type,
        'created_at': files[page.file_id].created_at.isoformat(), **scope['uses'][str(page.file_id)]} for index, page in enumerate(scope['pages'])]
    return Response(checked_search('Item', {'document': document_read(document=document, patient=patient, version=version),
        'pages': pages, 'episodes': scope['episodes'], 'snippet': None, 'matched_fields': [],
        'verification_status': verification_status(scope['fields'], patient),
        'limitations': ['DOCUMENT_HISTORICAL_VERSION'] if document.current_version != version.version else []}))
