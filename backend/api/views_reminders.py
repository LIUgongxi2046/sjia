from django.db import connection, transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, current_principal, get_patient, handle
from archives.models import MaterialReminder
from configurations.models import ConfigVersion
from configurations.qualification import configuration_qualification_reason
from notifications.reminder_schemas import reminder_query, reminder_target_query
from notifications.reminders import current_scope, reminder_list, reminder_target_list, reminder_view


def reminder_identity(request):
    owner, epoch, audience = current_principal(request)
    if owner is None:
        raise ApiError('AUTH_REQUIRED', 401)
    if audience != 'family':
        raise ApiError('FAMILY_AUDIENCE_REQUIRED', 403)
    return owner, epoch


def reminder_page_limit(scope, requested):
    runtime = ConfigVersion.objects.filter(id=scope.gate.runtime_config_id,
        kind='provider', schema_version='backend-runtime-1').first()
    if runtime is None or configuration_qualification_reason(runtime, scope.gate.environment, audience='support'):
        raise ApiError('RUNTIME_CONFIGURATION_PENDING', 503)
    maximum = runtime.payload['api']['max_page_size']
    if requested is not None and requested > maximum:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    return maximum if requested is None else requested


@api_view(['POST'])
@handle
@transaction.atomic
def material_reminder_search(request, patient_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    owner, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    query = reminder_query(request.data)
    scope = current_scope(owner=owner, patient=get_patient(patient_id), auth_epoch=epoch)
    return Response(reminder_list(scope=scope, limit=reminder_page_limit(scope, query.limit),
        expires_at=request.session.get_expiry_date(), cursor=query.cursor))


@api_view(['GET'])
@handle
@transaction.atomic
def material_reminder_read(request, reminder_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    owner, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    reminder = MaterialReminder.objects.filter(id=reminder_id, owner_id=owner.id).first()
    if reminder is None:
        raise ApiError('REMINDER_NOT_FOUND', 404)
    scope = current_scope(owner=owner, patient=get_patient(reminder.patient_id), auth_epoch=epoch)
    return Response(reminder_view(reminder=reminder, scope=scope))


@api_view(['POST'])
@handle
@transaction.atomic
def material_reminder_target_search(request, patient_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    owner, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    query = reminder_target_query(request.data)
    scope = current_scope(owner=owner, patient=get_patient(patient_id), auth_epoch=epoch)
    return Response(reminder_target_list(scope=scope, target_type=query.target_type,
        limit=reminder_page_limit(scope, query.limit), expires_at=request.session.get_expiry_date(), cursor=query.cursor))
