from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError
from archives.models import ObservationPeriodVersion
from audit.service import write_audit
from records.periods import period_scope, period_view, save_period
from requests.archive_documents import family_identity
from requests.observation_periods import period_receipt, submitted_period_intent
from requests.service import commit_intent
from api.base import handle


@api_view(['GET'])
@handle
@transaction.atomic
def observation_period_read(request, patient_id):
    if request.query_params:
        raise ApiError('OBSERVATION_PERIOD_QUERY_INVALID', 422)
    actor, _ = family_identity(request)
    patient, _, runtime = period_scope(request, actor, patient_id)
    return Response(period_view(actor, patient, runtime))


@api_view(['GET'])
@handle
@transaction.atomic
def observation_period_version_read(request, version_id):
    if request.query_params:
        raise ApiError('OBSERVATION_PERIOD_QUERY_INVALID', 422)
    actor, _ = family_identity(request)
    row = ObservationPeriodVersion.objects.get(id=version_id, owner_id=actor.id)
    patient, _, runtime = period_scope(request, actor, row.patient_id)
    return Response(period_view(actor, patient, runtime, row))


@api_view(['POST'])
@handle
@transaction.atomic
def observation_period_change(request, patient_id):
    actor, _ = family_identity(request)
    intent, patient, previous = submitted_period_intent(request, actor)
    if patient.id != patient_id:
        raise ApiError('OBSERVATION_PERIOD_SCOPE_DENIED', 403)
    if intent.status != 'committed':
        saved = save_period(patient, actor, intent.recovery_payload, previous)
        receipt = commit_intent(intent=intent, action=intent.operation_id, resource_type='observation_period',
            resource_id=saved.id, expected_version=saved.version)
        write_audit(actor=actor, patient=patient, action=intent.operation_id, object_type='observation_period',
            object_id=saved.id, object_version=str(saved.version), request_id=intent.request_id,
            outcome='committed', retention_policy=intent.retention_policy, occurred_at=receipt.completed_at)
    return Response(period_receipt(intent), status=201)
