from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_case, authorize_service, get_patient, handle
from archives.models import ArchiveRelationVersion
from archives.relations import current_relation, relation_fields, relation_view, relation_actions, source_scope, save_relation
from archives.relation_queries import relation_directory
from audit.service import write_audit
from requests.archive_relations import relation_identity, submitted_relation_intent, relation_receipt
from requests.service import commit_intent


@api_view(['GET'])
@handle
@transaction.atomic
def archive_relation_search(request, patient_id):
    actor, epoch, audience = relation_identity(request)
    patient = get_patient(patient_id)
    if audience == 'support':
        authorize_service(request, actor, 'verify_field')
    authorize_case(request, actor, patient, 'read_raw' if audience == 'family' else 'read_case',
        require_relation=audience == 'family')
    patient.refresh_from_db()
    return Response(relation_directory(request, actor, epoch, audience, patient))


@api_view(['GET'])
@handle
@transaction.atomic
def archive_relation_read(request, relation_id):
    actor, _, audience = relation_identity(request)
    initial = ArchiveRelationVersion.objects.get(relation_id=relation_id, version=1)
    patient = get_patient(initial.patient_id)
    if audience == 'support':
        authorize_service(request, actor, 'verify_field')
    current = current_relation(patient, relation_id)
    if set(request.query_params) - {'version'} or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise ApiError('ARCHIVE_RELATION_QUERY_INVALID', 422)
    raw = request.query_params.get('version')
    if raw is not None:
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 19 or str(int(raw)) != raw or not 1 <= int(raw) <= current.version:
            raise ApiError('ARCHIVE_RELATION_QUERY_INVALID', 422)
        row = ArchiveRelationVersion.objects.get(relation_id=relation_id, patient_id=patient.id, version=int(raw))
    else:
        row = current
    kind = 'fact' if row.left_fact_version_id else 'file'
    identities = (row.left_fact_version_id, row.right_fact_version_id) if kind == 'fact' else (row.left_file_id, row.right_file_id)
    for identity in identities:
        source_scope(request, actor, patient, kind, identity, 'read_history' if raw is not None else 'read_raw')
    return Response(relation_view(row, allowed_actions=relation_actions(request, actor, patient, row, audience) if row.id == current.id else ()))


def relation_write(request, operation):
    actor, intent, patient, previous, proof = submitted_relation_intent(request, operation)
    if intent.status != 'committed':
        version = save_relation(request=request, actor=actor, patient=patient, payload=request.data, previous=previous, verification=proof)
        saved = commit_intent(intent=intent, action=operation, resource_type='archive_relation',
            resource_id=version.relation_id, expected_version=version.version)
        write_audit(actor=actor, patient=patient, action=operation, object_type='archive_relation',
            object_id=version.relation_id, object_version=str(version.version), request_id=intent.request_id,
            outcome='committed', retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
    return Response(relation_receipt(intent), status=201 if operation == 'archive_relation_create' else 200)


@api_view(['POST'])
@handle
@transaction.atomic
def archive_relation_create(request, patient_id):
    return relation_write(request, 'archive_relation_create')


@api_view(['POST'])
@handle
@transaction.atomic
def archive_relation_update(request, relation_id):
    return relation_write(request, 'archive_relation_update')


@api_view(['POST'])
@handle
@transaction.atomic
def archive_relation_verify(request, relation_id):
    return relation_write(request, 'archive_relation_verify')
