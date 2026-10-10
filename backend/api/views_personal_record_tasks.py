from django.db import connection, transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import ApiError, get_patient, handle
from api.views_reminders import reminder_identity, reminder_page_limit
from archives.models import PersonalRecordTask
from audit.service import write_audit
from notifications.personal_record_tasks import personal_tasks, reconcile_task_reminders, retention_options
from notifications.personal_record_tasks import save_personal_task, task_list, task_view
from notifications.reminder_schemas import reminder_query
from notifications.reminders import current_scope
from requests.personal_record_tasks import cancel_task_intent, submitted_task_intent, task_receipt
from requests.service import commit_intent


def read_scope(request, patient_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    actor, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('PERSONAL_RECORD_TASK_QUERY_INVALID', 422)
    return current_scope(owner=actor, patient=get_patient(patient_id), auth_epoch=epoch)


@api_view(['POST'])
@handle
@transaction.atomic
def personal_record_task_search(request, patient_id):
    scope = read_scope(request, patient_id)
    query = reminder_query(request.data)
    return Response(task_list(scope=scope, limit=reminder_page_limit(scope, query.limit),
        expires_at=request.session.get_expiry_date(), cursor=query.cursor))


@api_view(['GET'])
@handle
@transaction.atomic
def personal_record_task_retention(request, patient_id):
    return Response(retention_options(read_scope(request, patient_id)))


@api_view(['GET'])
@handle
@transaction.atomic
def personal_record_task_read(request, task_id):
    with connection.cursor() as cursor:
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    actor, epoch = reminder_identity(request)
    if request.query_params:
        raise ApiError('PERSONAL_RECORD_TASK_QUERY_INVALID', 422)
    task = PersonalRecordTask.objects.filter(id=task_id, owner_id=actor.id).first()
    if task is None:
        raise ApiError('PERSONAL_RECORD_TASK_NOT_FOUND', 404)
    scope = current_scope(owner=actor, patient=get_patient(task.patient_id), auth_epoch=epoch)
    return Response(task_view(task, scope))


def task_write(request, operation):
    actor, epoch = reminder_identity(request)
    intent, scope, task = submitted_task_intent(request, actor, epoch, operation)
    if intent.status != 'committed':
        current = save_personal_task(scope=scope, payload=intent.recovery_payload, task=task)
        saved = commit_intent(intent=intent, action=operation, resource_type='personal_record_task',
            resource_id=current.id, expected_version=current.version)
        write_audit(actor=actor, patient=scope.patient, action=operation, object_type='personal_record_task',
            object_id=current.id, object_version=str(current.version), request_id=intent.request_id,
            outcome='committed', retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
        if current.status != 'pending':
            reconcile_task_reminders(current)
    return Response(task_receipt(intent), status=201 if operation == 'personal_record_task_create' else 200)


@api_view(['POST'])
@handle
@transaction.atomic
def personal_record_task_create(request, patient_id):
    return task_write(request, 'personal_record_task_create')


@api_view(['POST'])
@handle
@transaction.atomic
def personal_record_task_change(request, task_id):
    return task_write(request, 'personal_record_task_change')


@api_view(['POST'])
@handle
@transaction.atomic
def personal_record_task_intent_cancel(request, intent_id):
    return Response(cancel_task_intent(request, intent_id))
