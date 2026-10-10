from archives.models import MaterialReminderNotice
from audit.service import write_audit
from notifications.errors import NotificationRejected


def cancel_unstarted_notices(*, reminder, patient, retention_policy, request_id, occurred_at, actor=None):
    if patient.id != reminder.patient_id:
        raise NotificationRejected('REMINDER_INTENT_SCOPE_MISMATCH')
    rows = MaterialReminderNotice.objects.select_for_update().filter(reminder_id=reminder.id,
        patient_id=reminder.patient_id, status='pending').order_by('id')
    cancelled, unresolved = [], []
    for notice in rows:
        if notice.reminder_version > reminder.version:
            raise NotificationRejected('REMINDER_NOTICE_STATE_INVALID')
        if reminder.status == 'enabled' and notice.reminder_version == reminder.version:
            continue
        if notice.permit_refs.get('schema_version') == 'material-reminder-notification-task-2':
            from notifications.material_delivery import checked_notice
            from tasks.models import TaskAttempt

            task = checked_notice(notice, lock=True, verify_permit=False)
            if (task.status in ('accepted', 'queued', 'cancelled') and not TaskAttempt.objects.filter(task=task).exists()
                    and notice.provider_request_id is None and not notice.receipt_refs
                    and notice.delivered_at is None and notice.read_at is None):
                if task.status != 'cancelled':
                    task.status, task.status_version = 'cancelled', task.status_version + 1
                    task.save(update_fields=['status', 'status_version'])
                notice.status = 'cancelled'
                notice.save(update_fields=['status'])
                write_audit(actor=actor, patient=patient, action='material_reminder_notice_cancel',
                    object_type='material_reminder_notice', object_id=notice.id, object_version=str(notice.reminder_version),
                    request_id=request_id, outcome='committed', retention_policy=retention_policy, occurred_at=occurred_at)
                cancelled.append(str(notice.id))
                continue
        if (notice.permit_refs or notice.provider_request_id is not None or notice.receipt_refs
                or notice.delivered_at is not None or notice.read_at is not None):
            unresolved.append(str(notice.id))
            continue
        notice.status = 'cancelled'
        notice.save(update_fields=['status'])
        write_audit(actor=actor, patient=patient, action='material_reminder_notice_cancel',
            object_type='material_reminder_notice', object_id=notice.id, object_version=str(notice.reminder_version),
            request_id=request_id, outcome='committed', retention_policy=retention_policy, occurred_at=occurred_at)
        cancelled.append(str(notice.id))
    return cancelled, unresolved
