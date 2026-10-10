from django.db import connection, transaction
from django.utils import timezone
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_case, current_principal, get_patient, handle
from api.views_archive_facts import fact_history_limit
from archives.documents import document_read, document_record_scope, document_sources, document_versions
from archives.models import ArchiveBatch, ArchiveDocument, ArchiveDocumentVersion
from archives.queries import fact_source
from case_details.models import ClinicalEpisode
from records.models import RecordField
from records.models import RecordVersion
from authorization.policy import matching_case_grants


def authorize_document(request, principal, patient, document, version, action):
    original_version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id, version=1)
    original_batch = ArchiveBatch.objects.get(id=original_version.batch_id, patient_id=patient.id)
    record_id = document_record_scope(document, patient, version)
    decision = authorize_case(request, principal, patient, action, record_id=record_id,
        author_id=original_batch.submitted_by, require_relation=True, whole_resource=True)
    proofs = [decision]
    _, _, _, groups, episodes, facts = document_sources(document=document, patient=patient, version=version)
    # 整份原文和页序需要原件范围，逐项出处继续核验记录及字段范围。
    for group in groups:
        proofs.append(authorize_case(request, principal, patient, 'read_file', record_id=record_id,
            author_id=group.owner_id, require_relation=True, whole_resource=True))
    for fact in facts:
        field, record_version = fact_source(fact, patient)
        original = RecordVersion.objects.get(record_id=record_version.record_id, patient=patient, version=1)
        proofs.append(authorize_case(request, principal, patient, action, field_codes=[field.field_code],
            record_id=record_version.record_id, author_id=original.author_id, require_relation=True))
    for relation in episodes:
        episode = ClinicalEpisode.objects.get(id=relation.episode_id, patient_id=patient.id)
        field = RecordField.objects.get(id=episode.source_field_id, record_version_id=episode.record_version_id,
            patient_id=patient.id)
        record_version = RecordVersion.objects.get(id=episode.record_version_id, patient_id=patient.id)
        original = RecordVersion.objects.get(record_id=record_version.record_id, patient_id=patient.id, version=1)
        proofs.append(authorize_case(request, principal, patient, action, field_codes=[field.field_code],
            record_id=record_version.record_id, author_id=original.author_id, require_relation=True))
    scopes = [{'grant_id': str(proof.grant.id), 'field_codes': sorted(proof.grant.field_codes)} for proof in proofs]
    return decision, scopes


def document_access(request, document_id, action):
    principal, epoch, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    document = ArchiveDocument.objects.get(id=document_id)
    patient = get_patient(document.patient_id)
    version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
        version=document.current_version)
    authorize_document(request, principal, patient, document, version, action)
    patient.refresh_from_db()
    document = ArchiveDocument.objects.select_for_update().get(id=document.id, patient_id=patient.id)
    version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
        version=document.current_version)
    decision, scopes = authorize_document(request, principal, patient, document, version, action)
    return principal, epoch, patient, document, decision, scopes


@api_view(['GET'])
@handle
@transaction.atomic
def archive_document_read(request, document_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    principal, _, patient, document, _, _ = document_access(request, document_id, 'read_raw')
    if request.query_params:
        raise ApiError('DOCUMENT_QUERY_INVALID', 422)
    result = document_read(document=document, patient=patient)
    version = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id,
        version=document.current_version)
    initial = ArchiveDocumentVersion.objects.get(document_id=document.id, patient_id=patient.id, version=1)
    author = ArchiveBatch.objects.get(id=initial.batch_id, patient_id=patient.id).submitted_by
    if any(matching_case_grants(principal, patient, 'write_record', timezone.now(),
            record_id=document_record_scope(document, patient, version), author_id=author,
            require_relation=True, whole_resource=True)):
        result['allowed_actions'] = ['archive_document_group']
    return Response(result)


@api_view(['GET'])
@handle
@transaction.atomic
def archive_document_history(request, document_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    principal, epoch, patient, document, decision, scopes = document_access(request, document_id, 'read_history')
    limit, config = fact_history_limit(request, query_code='DOCUMENT_QUERY_INVALID')
    cursor = request.query_params.get('cursor')
    if cursor is not None and (not cursor or len(cursor) > 65536):
        raise ApiError('DOCUMENT_QUERY_INVALID', 422)
    scope = {'principal_id': str(principal.id), 'auth_epoch': str(epoch), 'grant_id': str(decision.grant.id),
        'field_codes': sorted(decision.grant.field_codes), 'source_scopes': scopes,
        'runtime_id': str(config.id), 'runtime_checksum': config.checksum}
    result = document_versions(document=document, patient=patient, scope=scope, limit=limit,
        expires_at=request.session.get_expiry_date(), cursor=cursor)
    for item in result['items']:
        version = ArchiveDocumentVersion.objects.get(id=item['version_id'], document_id=document.id, patient_id=patient.id)
        authorize_document(request, principal, patient, document, version, 'read_history')
    return Response(result)
