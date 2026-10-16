from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_case, current_principal, handle
from files.originals import original_source_view
from records.models import RecordVersion


@api_view(['GET'])
@handle
@transaction.atomic
def record_originals_read(request, record_version_id):
    principal, _, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    if request.query_params:
        raise ApiError('ORIGINAL_QUERY_INVALID', 422)
    version = RecordVersion.all_objects.only('id','record_id','patient_id','author_id').get(id=record_version_id)
    original = RecordVersion.all_objects.only('id', 'patient_id', 'record_id', 'version', 'author_id').get(record_id=version.record_id, patient=version.patient, version=1)
    decision = authorize_case(request, principal, version.patient, 'read_history', record_id=version.record_id,
        author_id=original.author_id, require_relation=True)
    authorize_case(request, principal, version.patient, 'read_file', record_id=version.record_id,
        author_id=original.author_id, require_relation=True)
    from retention.content_fences import assert_content_available
    assert_content_available('records.recordversion',version.id)
    return Response(original_source_view(version, decision.grant.field_codes))
