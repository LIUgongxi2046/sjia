from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, authorize_case, current_principal, get_patient, handle
from authorization.policy import matching_case_grants
from relations.models import Relation


SESSION_PATIENT_KEY = '_sjia_selected_patient_id'


class PatientSelectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    patient_id: UUID = Field(strict=False)


def current_selected_patient(request, principal):
    identifier = request.session.get(SESSION_PATIENT_KEY)
    if identifier is None:
        return None
    now = timezone.now()
    relation = Relation.objects.select_related('patient').filter(
        guardian=principal, patient_id=identifier, status='active', revoked_at__isnull=True,
        valid_from__lte=now).first()
    available = (relation is not None and relation.patient.status == 'active'
        and (relation.valid_until is None or relation.valid_until > now)
        and any(matching_case_grants(principal, relation.patient, 'read_raw', now,
            require_relation=True, purpose='family_observation')))
    if not available:
        request.session.pop(SESSION_PATIENT_KEY, None)
        return None
    return str(relation.patient_id)


@api_view(['POST'])
@handle
@transaction.atomic
def patient_selection_write(request):
    principal, _, audience = current_principal(request)
    if principal is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family' or settings.SJIA_ENVIRONMENT is None:
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    payload = PatientSelectionRequest.model_validate(request.data)
    patient = get_patient(payload.patient_id)
    if patient.status != 'active':
        raise ApiError('PATIENT_UNAVAILABLE', 403)
    authorize_case(request, principal, patient, 'read_raw', require_relation=True)
    request.session[SESSION_PATIENT_KEY] = str(patient.id)
    request.session.save()
    if current_selected_patient(request, principal) != str(patient.id):
        raise ApiError('PATIENT_SELECTION_UNAVAILABLE', 409)
    return Response({'selected_patient_id': str(patient.id)})
