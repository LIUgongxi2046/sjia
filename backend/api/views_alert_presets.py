from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, handle
from alerting.presets import directory
from requests.alert_presets import submit_preset


def identity(request):
    actor, epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    return actor, epoch, audience


@api_view(['GET'])
@handle
def alert_preset_directory(request, patient_id):
    if request.query_params:
        raise ApiError('PRESET_QUERY_INVALID', 422)
    actor, epoch, audience = identity(request)
    return Response(directory(actor=actor, patient_id=patient_id, auth_epoch=epoch, audience=audience))


@api_view(['POST'])
@handle
def alert_preset_select(request, patient_id):
    actor, epoch, audience = identity(request)
    return Response(submit_preset(actor=actor, patient_id=patient_id, auth_epoch=epoch, audience=audience,
        target=request.path, data=request.data, headers={'intent_id': request.headers.get('X-Write-Intent'),
            'request_id': request.headers.get('Idempotency-Key'), 'generation': request.headers.get('X-Intent-Generation')}))
