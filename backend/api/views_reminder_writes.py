from django.db import transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from api.base import handle
from api.views_reminders import reminder_identity
from audit.service import write_audit
from notifications.reminders import save_reminder
from notifications.reminder_notices import cancel_unstarted_notices
from requests.material_reminders import reminder_receipt, submitted_reminder_intent, cancel_reminder_intent
from requests.service import commit_intent


def reminder_write(request, operation):
    actor, epoch = reminder_identity(request)
    intent, scope, reminder = submitted_reminder_intent(request, actor, epoch, operation)
    if intent.status != 'committed':
        saved_reminder = save_reminder(owner=actor, patient=scope.patient, auth_epoch=epoch,
            payload=intent.recovery_payload, reminder_id=reminder.id if reminder else None)
        saved = commit_intent(intent=intent, action=operation, resource_type='material_reminder',
            resource_id=saved_reminder.id, expected_version=saved_reminder.version)
        write_audit(actor=actor, patient=scope.patient, action=operation, object_type='material_reminder',
            object_id=saved_reminder.id, object_version=str(saved_reminder.version), request_id=intent.request_id,
            outcome='committed', retention_policy=intent.retention_policy, occurred_at=saved.completed_at)
        cancel_unstarted_notices(reminder=saved_reminder, patient=scope.patient, actor=actor,
            retention_policy=intent.retention_policy, request_id=intent.request_id, occurred_at=saved.completed_at)
    return Response(reminder_receipt(intent), status=201 if operation == 'material_reminder_create' else 200)


@api_view(['POST'])
@handle
@transaction.atomic
def material_reminder_create(request, patient_id):
    return reminder_write(request, 'material_reminder_create')


@api_view(['POST'])
@handle
@transaction.atomic
def material_reminder_change(request, reminder_id):
    return reminder_write(request, 'material_reminder_change')


@api_view(['POST'])
@handle
@transaction.atomic
def material_reminder_intent_cancel(request, intent_id):
    return Response(cancel_reminder_intent(request, intent_id))
