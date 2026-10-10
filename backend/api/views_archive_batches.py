from typing import Annotated

from django.conf import settings
from django.core import signing
from django.db import connection, transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, get_patient, handle
from archives.batches import batch_grants, batch_read, batch_runtime, directory_grants, page_grants, page_read
from archives.models import ArchiveBatch, ArchivePage
from configurations.service import compute_checksum
from family_content.models import EnvironmentGate
from files.models import FileGroup, FilePage
from archives.batches import file_page_source, file_page_record_scope, source_grants


class BatchQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cursor: Annotated[str, Field(min_length=1, max_length=65536)] | None = None
    limit: Annotated[int, Field(ge=1)] | None = None


def batch_reader(request):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    if not settings.SJIA_ENVIRONMENT:
        raise ApiError('ENVIRONMENT_CONFIGURATION_REQUIRED', 503)
    if not EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT, enabled=True).exists():
        raise ApiError('ENVIRONMENT_DISABLED', 403)
    return principal, epoch


def read_snapshot():
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')


@api_view(['GET'])
@handle
@transaction.atomic
def archive_batch_read(request, batch_id):
    read_snapshot()
    principal, _ = batch_reader(request)
    if request.query_params:
        raise ApiError('ARCHIVE_BATCH_QUERY_INVALID', 422)
    batch = ArchiveBatch.objects.get(id=batch_id)
    patient = get_patient(batch.patient_id)
    pages = list(ArchivePage.objects.filter(batch_id=batch.id, patient_id=patient.id).order_by('ordinal'))
    if not batch_grants(principal, patient, batch, pages):
        raise ApiError('CASE_ACTION_DENIED', 403)
    return Response(batch_read(batch, patient, pages, principal))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_page_read(request, page_id):
    read_snapshot()
    principal, _ = batch_reader(request)
    if request.query_params:
        raise ApiError('ARCHIVE_BATCH_QUERY_INVALID', 422)
    page = ArchivePage.objects.get(id=page_id)
    patient = get_patient(page.patient_id)
    if not page_grants(principal, patient, page):
        raise ApiError('CASE_ACTION_DENIED', 403)
    return Response(page_read(page, patient, principal))


@api_view(['POST'])
@handle
@transaction.atomic
def archive_batch_search(request, patient_id):
    read_snapshot()
    principal, epoch = batch_reader(request)
    if request.query_params:
        raise ApiError('ARCHIVE_BATCH_QUERY_INVALID', 422)
    query = BatchQuery.model_validate(request.data)
    patient = get_patient(patient_id)
    grants = directory_grants(principal, patient)
    if not grants:
        raise ApiError('CASE_ACTION_DENIED', 403)
    runtime = batch_runtime()
    maximum = runtime.payload['api']['max_page_size']
    limit = query.limit if query.limit is not None else maximum
    if limit > maximum:
        raise ApiError('ARCHIVE_BATCH_QUERY_INVALID', 422)
    readable, proofs = [], {}
    for batch in ArchiveBatch.objects.filter(patient_id=patient.id).order_by('-received_at', '-id'):
        pages = list(ArchivePage.objects.filter(batch_id=batch.id, patient_id=patient.id).order_by('ordinal'))
        current_grants = batch_grants(principal, patient, batch, pages)
        if current_grants:
            readable.append((batch, pages))
            proofs[str(batch.id)] = {'version': str(batch.version),
                'grants': sorted(str(grant.id) for grant in current_grants),
                'pages': [{'id': str(page.id), 'version': str(page.version), 'status_version': str(page.status_version),
                    'status': page.status, 'current_task_id': str(page.current_task_id),
                    'checksum': page.checksum, 'file_object_version': page.file_object_version} for page in pages]}
    bound = {'principal_id': str(principal.id), 'auth_epoch': str(epoch), 'patient_id': str(patient.id),
        'data_epoch': str(patient.data_epoch), 'verification_epoch': str(patient.verification_epoch),
        'directory_grants': sorted(str(grant.id) for grant in grants), 'sources': proofs,
        'runtime_id': str(runtime.id), 'runtime_checksum': runtime.checksum, 'limit': limit}
    now = timezone.now()
    expiry = request.session.get_expiry_date().timestamp()
    if expiry <= now.timestamp():
        raise ApiError('AUTH_REQUIRED', 401)
    offset = 0
    snapshot_version = compute_checksum(bound)
    if query.cursor is not None:
        saved = signing.loads(query.cursor, salt='archive-batch-directory')
        if (not isinstance(saved, dict) or set(saved) != {'snapshot_version', 'offset', 'expires_at'}
                or saved['snapshot_version'] != snapshot_version
                or type(saved['offset']) is not int or not 0 <= saved['offset'] < len(readable)
                or type(saved['expires_at']) not in (int, float) or saved['expires_at'] <= now.timestamp()
                or saved['expires_at'] > expiry):
            raise ApiError('CURSOR_STALE', 409)
        offset, expiry = saved['offset'], saved['expires_at']
    selected = readable[offset:offset + limit]
    next_offset = offset + len(selected)
    cursor = signing.dumps({'snapshot_version': snapshot_version, 'offset': next_offset, 'expires_at': expiry},
        salt='archive-batch-directory') if next_offset < len(readable) else None
    return Response({'items': [batch_read(batch, patient, pages, principal) for batch, pages in selected],
        'snapshot_version': snapshot_version, 'next_cursor': cursor})


@api_view(['GET'])
@handle
@transaction.atomic
def archive_batch_inputs(request, patient_id):
    read_snapshot()
    principal, _ = batch_reader(request)
    if request.query_params:
        raise ApiError('ARCHIVE_BATCH_QUERY_INVALID', 422)
    patient = get_patient(patient_id)
    if not source_grants(principal, patient, 'write_record', author_id=principal.id):
        raise ApiError('CASE_ACTION_DENIED', 403)
    batch_runtime()
    received = ArchivePage.objects.filter(patient_id=patient.id).values('file_page_id')
    items = []
    for group in FileGroup.objects.filter(patient=patient, status='available').order_by('-created_at', '-id'):
        pages = list(FilePage.objects.filter(patient=patient, file_group_id=group.id).order_by('ordinal'))
        if not pages or FilePage.objects.filter(id__in=[page.id for page in pages]).filter(id__in=received).exists():
            continue
        files = []
        for page in pages:
            source, _ = file_page_source(page, patient)
            if source.status != 'available' or not source_grants(principal, patient, 'read_file',
                    author_id=group.owner_id, record_id=file_page_record_scope(page.id, patient)):
                files = []
                break
            files.append(source)
        if files:
            items.append({'file_group_id': str(group.id), 'version': str(group.version), 'page_count': len(pages),
                'file_ids': list(dict.fromkeys(str(source.id) for source in files)), 'created_at': group.created_at.isoformat()})
    return Response({'patient_id': str(patient.id), 'items': items})
