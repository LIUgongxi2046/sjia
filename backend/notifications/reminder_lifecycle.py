from django.db import connection, transaction
from django.utils import timezone

from archives.models import MaterialReminder
from audit.service import write_audit
from identity.models import Patient, Principal
from notifications.errors import NotificationRejected
from notifications.reminder_notices import cancel_unstarted_notices
from notifications.reminders import inspect_scope, inspect_target, reminder_transition
from requests.material_reminders import reminder_receipt
from requests.models import WriteIntent


def originating_intent(reminder):
    intents = list(WriteIntent.objects.filter(actor_id=reminder.owner_id, patient_id=reminder.patient_id,
        operation_id='material_reminder_create', status='committed', write_request__resource_type='material_reminder',
        write_request__resource_id=reminder.id).order_by('id')[:2])
    if len(intents) != 1:
        raise NotificationRejected('REMINDER_RECEIPT_INVALID')
    intent = intents[0]
    reminder_receipt(intent)
    return intent


@transaction.atomic
def reconcile_reminder(reminder_id, *, dry_run=False):
    initial = MaterialReminder.objects.filter(id=reminder_id).first()
    if initial is None:
        raise NotificationRejected('REMINDER_NOT_FOUND')
    owner = Principal.objects.get(id=initial.owner_id)
    patient = Patient.objects.get(id=initial.patient_id)
    submitted = WriteIntent.objects.filter(actor_id=owner.id, patient_id=patient.id, status='committed',
        operation_id__in=['material_reminder_create', 'material_reminder_change'],
        write_request__resource_type='material_reminder', write_request__resource_id=initial.id,
        write_request__expected_version__lte=initial.version).order_by('-write_request__expected_version').first()
    if submitted is None:
        raise NotificationRejected('REMINDER_RECEIPT_INVALID')
    reminder_receipt(submitted)
    scope, reason = inspect_scope(owner=owner, patient=patient,
        auth_epoch=int(submitted.authorization_context['auth_epoch']), lock=not dry_run)
    initial = MaterialReminder.objects.get(id=reminder_id, patient_id=patient.id, owner_id=owner.id)
    state = None
    if reason is None:
        target, state = inspect_target(scope, initial.target_type, initial.target_ref,
            initial.target_version, lock=not dry_run)
        if target is None and state != 'unsupported':
            reason = 'REMINDER_SCOPE_DENIED'
    rows = MaterialReminder.objects
    if not dry_run and reason != 'REMINDER_ENVIRONMENT_UNAVAILABLE':
        rows = rows.select_for_update()
    current = rows.get(id=reminder_id, patient_id=patient.id, owner_id=owner.id)
    proposed = reminder_transition(current.status, state, reason)
    changed = proposed != current.status
    result = {'reminder_id': str(current.id), 'version': str(current.version), 'status': current.status,
        'proposed_status': proposed, 'target_state': state, 'reason_code': reason,
        'changed': False, 'would_change': changed, 'cancelled_notice_ids': [], 'unresolved_notice_ids': []}
    if dry_run or not changed:
        return result
    if current.version == 9223372036854775807:
        raise NotificationRejected('VERSION_CONFLICT')
    intent = originating_intent(current)
    current.status = proposed
    current.version += 1
    current.updated_at = timezone.now()
    current.save(update_fields=['status', 'version', 'updated_at'])
    write_audit(patient=patient, action='material_reminder_reconcile', object_type='material_reminder',
        object_id=current.id, object_version=str(current.version), request_id=intent.request_id,
        outcome='committed', reason_code=reason or f'REMINDER_TARGET_{state.upper()}',
        retention_policy=intent.retention_policy, occurred_at=current.updated_at)
    cancelled, unresolved = cancel_unstarted_notices(reminder=current, patient=patient,
        retention_policy=intent.retention_policy, request_id=intent.request_id, occurred_at=current.updated_at)
    return {**result, 'version': str(current.version), 'status': current.status, 'changed': True,
        'cancelled_notice_ids': cancelled, 'unresolved_notice_ids': unresolved}


def reconcile_reminders(*, limit, after=None, dry_run=False):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NotificationRejected('REMINDER_QUERY_INVALID')
    if dry_run:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return scan_reminders(limit=limit, after=after, dry_run=True)
    return scan_reminders(limit=limit, after=after, dry_run=False)


def scan_reminders(*, limit, after, dry_run):
    rows = MaterialReminder.objects.filter(status__in=['disabled', 'enabled', 'paused']).order_by('id')
    if after is not None:
        rows = rows.filter(id__gt=after)
    identifiers = list(rows.values_list('id', flat=True)[:limit + 1])
    results = [reconcile_reminder(identifier, dry_run=dry_run) for identifier in identifiers[:limit]]
    return {'dry_run': dry_run, 'scanned': len(results),
        'changed': sum(item['changed'] for item in results),
        'would_change': sum(item['would_change'] for item in results),
        'next_cursor': str(identifiers[limit - 1]) if len(identifiers) > limit else None, 'items': results}
