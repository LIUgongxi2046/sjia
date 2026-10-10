import json
from pathlib import Path
from uuid import UUID

from django.db import connection, transaction
from jsonschema import Draft202012Validator, FormatChecker
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, get_patient, handle
from api.views_reminders import reminder_identity, reminder_page_limit
from archives.inputs import REGISTRY
from notifications.material_delivery import acknowledge_notice, mark_notice_read, notice_list, notice_message
from notifications.material_delivery import notice_view, owner_notice_scope, retry_original_notice
from notifications.reminders import current_scope


SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'backend-design/material-reminder-notice.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text())
SCHEMA['$id'] = SCHEMA_PATH.as_uri()
VALIDATORS = {name: Draft202012Validator({**SCHEMA, '$ref': f'#/$defs/{name}'},
    registry=REGISTRY, format_checker=FormatChecker()) for name in ('Query', 'Acknowledgement', 'EmptyInput')}


def notice_identity(request, *, schema=None):
    actor, epoch = reminder_identity(request)
    if request.query_params or schema is not None and next(VALIDATORS[schema].iter_errors(request.data), None) is not None:
        raise ApiError('REMINDER_QUERY_INVALID', 422)
    return actor, epoch


@api_view(['POST'])
@handle
@transaction.atomic
def material_notice_search(request, patient_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    actor, epoch = notice_identity(request, schema='Query')
    scope = current_scope(owner=actor, patient=get_patient(patient_id), auth_epoch=epoch)
    return Response(notice_list(scope=scope, limit=reminder_page_limit(scope, request.data.get('limit')),
        expires_at=request.session.get_expiry_date(), cursor=request.data.get('cursor'),
        reminder_id=UUID(request.data['reminder_id']) if 'reminder_id' in request.data else None))


@api_view(['GET'])
@handle
@transaction.atomic
def material_notice_read(request, notice_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    actor, epoch = notice_identity(request)
    scope, notice = owner_notice_scope(actor=actor, epoch=epoch, notice_id=notice_id)
    return Response(notice_view(notice, scope))


@api_view(['GET'])
@handle
@transaction.atomic
def material_notice_message(request, notice_id):
    actor, epoch = notice_identity(request)
    return Response(notice_message(actor=actor, epoch=epoch, notice_id=notice_id,
        session_expires_at=request.session.get_expiry_date()))


@api_view(['POST'])
@handle
@transaction.atomic
def material_notice_delivered(request, notice_id):
    actor, epoch = notice_identity(request, schema='Acknowledgement')
    return Response(acknowledge_notice(actor=actor, epoch=epoch, notice_id=notice_id,
        ticket=request.data['delivery_ticket'], session_expires_at=request.session.get_expiry_date()))


@api_view(['POST'])
@handle
@transaction.atomic
def material_notice_mark_read(request, notice_id):
    actor, epoch = notice_identity(request, schema='EmptyInput')
    return Response(mark_notice_read(actor=actor, epoch=epoch, notice_id=notice_id))


@api_view(['POST'])
@handle
@transaction.atomic
def material_notice_retry(request, notice_id):
    actor, epoch = notice_identity(request, schema='EmptyInput')
    return Response(retry_original_notice(actor=actor, epoch=epoch, notice_id=notice_id))
