from django.db import connection, transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, get_patient, handle
from archives.batches import directory_grants
from archives.search import archive_search
from family_content.models import EnvironmentGate
from django.conf import settings


@api_view(['POST'])
@handle
@transaction.atomic
def archive_directory_search(request, patient_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
    if request.query_params:
        raise ApiError('ARCHIVE_SEARCH_QUERY_INVALID', 422)
    actor, epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    if not EnvironmentGate.objects.filter(environment=settings.SJIA_ENVIRONMENT, enabled=True).exists():
        raise ApiError('ENVIRONMENT_DISABLED', 403)
    patient = get_patient(patient_id)
    if patient.status != 'active':
        raise ApiError('PATIENT_INACTIVE', 403)
    if not directory_grants(actor, patient):
        raise ApiError('CASE_ACTION_DENIED', 403)
    return Response(archive_search(request, actor, epoch, patient))
