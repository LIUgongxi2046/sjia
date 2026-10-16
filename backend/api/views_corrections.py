from django.conf import settings
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, handle
from verification.correction_review import read_review, review_queue


def identity(request):
    actor, epoch, audience = current_principal(request)
    if actor is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'support':
        raise ApiError('SUPPORT_AUDIENCE_REQUIRED', 403)
    return {'actor': actor, 'epoch': epoch, 'audience': audience, 'environment': settings.SJIA_ENVIRONMENT}


@api_view(['GET'])
@handle
def correction_review_queue(request):
    return Response(review_queue(parameters=request.query_params, **identity(request)))


@api_view(['GET'])
@handle
def correction_review_read(request, correction_request_id):
    if request.query_params:
        raise ApiError('CORRECTION_QUERY_INVALID', 422)
    return Response(read_review(correction_request_id, **identity(request)))
