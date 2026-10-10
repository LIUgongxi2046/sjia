from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, handle
from api.views_archive_facts import fact_actions
from api.views_purpose import content_response
from archives.fact_candidates import candidate_view, validate_value
from archives.fact_writes import confirm_facts, correct_fact, field_grants, page_scope
from archives.models import ArchiveFact, ArchiveFactVersion
from archives.queries import fact_read, fact_source
from archives.batches import page_read, page_record_scope
from audit.service import write_audit
from configurations.service import compute_checksum
from files.originals import original_use_view
from files.purpose import checked_bytes
from requests.archive_documents import family_identity
from requests.archive_facts import fact_receipt, submitted_fact
from requests.service import commit_intent


@api_view(['GET'])
@handle
@transaction.atomic
def archive_candidates_read(request, page_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_FACT_QUERY_INVALID', 422)
    result = page_scope(actor, epoch, page_id)
    value = candidate_view(result[4], result[1])
    field_grants(actor, result[1], 'read_raw', [item['field']['field_code'] for item in value['items']],
        result[3].submitted_by, page_record_scope(result[4], result[1]))
    return Response(value)


@api_view(['GET'])
@handle
@transaction.atomic
def archive_page_facts_read(request, page_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_FACT_QUERY_INVALID', 422)
    result = page_scope(actor, epoch, page_id)
    patient, page = result[1], result[4]
    ids = ArchiveFactVersion.objects.filter(page_id=page.id, patient_id=patient.id).values('fact_id')
    items = []
    for fact in ArchiveFact.objects.filter(id__in=ids, patient_id=patient.id).order_by('id'):
        current = ArchiveFactVersion.objects.get(fact_id=fact.id, patient_id=patient.id, version=fact.current_version)
        if current.page_id != page.id:
            continue
        field, version = fact_source(current, patient)
        field_grants(actor, patient, 'read_raw', [field.field_code], result[3].submitted_by, version.record_id)
        value = fact_read(fact=fact, patient=patient, allowed_actions=fact_actions(actor, patient, fact))
        validate_value('FactView', value)
        items.append(value)
    value = {'items': items, 'snapshot_version': compute_checksum({'page_id': str(page.id),
        'page_version': str(page.version), 'data_epoch': str(patient.data_epoch), 'versions': [item['version_id'] for item in items]}),
        'next_cursor': None}
    return Response(validate_value('FactList', value))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_page_original_read(request, page_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_FACT_QUERY_INVALID', 422)
    result = page_scope(actor, epoch, page_id)
    patient, page, source, group = result[1], result[4], result[5], result[6]
    value = {'patient_id': str(patient.id), 'page': page_read(page, patient, actor), 'ordinal': page.ordinal,
        'created_at': source.created_at.isoformat(), 'file': {'file_id': str(source.id), 'status': source.status,
            'checksum': source.checksum, 'size_bytes': source.size_bytes, 'media_type': source.media_type,
            'content_path': f'/api/v2/archive-pages/{page.id}/original/content'}, **original_use_view(source, group, patient)}
    return Response(validate_value('PageOriginalView', value))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_page_original_content(request, page_id):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_FACT_QUERY_INVALID', 422)
    result = page_scope(actor, epoch, page_id)
    return content_response(request, checked_bytes(result[5]), result[5])


def save(request, operation):
    actor, epoch = family_identity(request)
    if request.query_params:
        raise ApiError('ARCHIVE_FACT_QUERY_INVALID', 422)
    intent, result = submitted_fact(request, actor, epoch, operation)
    if intent.status == 'committed':
        return Response(fact_receipt(intent), status=201)
    function = confirm_facts if operation == 'archive_candidates_confirm' else correct_fact
    resource_id, version = function(actor, result[1], request.data, result[5])
    kind = 'archive_page' if operation == 'archive_candidates_confirm' else 'archive_fact'
    saved = commit_intent(intent=intent, action=operation, resource_type=kind, resource_id=resource_id, expected_version=version)
    write_audit(actor=actor, patient=result[1], action=operation, object_type=kind, object_id=resource_id,
        object_version=str(version), request_id=intent.request_id, outcome='committed',
        retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(fact_receipt(intent), status=201)


@api_view(['POST'])
@handle
@transaction.atomic
def archive_candidates_confirm(request, page_id):
    return save(request, 'archive_candidates_confirm')


@api_view(['POST'])
@handle
@transaction.atomic
def archive_fact_correct(request, fact_id):
    return save(request, 'archive_fact_correct')
