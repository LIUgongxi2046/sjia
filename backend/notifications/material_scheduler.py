from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from archives.models import MaterialReminder, MaterialReminderNotice
from config.celery import app
from family_content.models import EnvironmentGate
from notifications.errors import NotificationRejected
from notifications.material_delivery import authorize_due, material_configuration, submit_notice
from notifications.reminder_lifecycle import reconcile_reminder
from notifications.reminder_schedule import next_permitted_time


def _due_candidates(*, limit, now):
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise NotificationRejected('REMINDER_QUERY_INVALID')
    notices = MaterialReminderNotice.objects.filter(reminder_id=OuterRef('id'),
        reminder_version=OuterRef('version'))
    rows = (MaterialReminder.objects.filter(status='enabled', schedule_at__lte=now)
        .annotate(has_notice=Exists(notices)).filter(has_notice=False).order_by('schedule_at', 'id'))
    # Limit deliverable reminders, not the unfiltered prefix. Stream the stable
    # query snapshot in bounded chunks so quiet rows cannot starve later rows.
    ready, scanned = [], 0
    for reminder in rows.iterator(chunk_size=min(limit, 200)):
        scanned += 1
        due = next_permitted_time(reminder.schedule_at, reminder.timezone_name,
            reminder.quiet_start, reminder.quiet_end, not_before=now)
        if due <= now:
            ready.append(reminder)
            if len(ready) == limit:
                break
    return ready, scanned


def due_candidates(*, limit, now):
    return _due_candidates(limit=limit, now=now)[0]


@transaction.atomic
def enqueue_reminder(reminder_id):
    result = reconcile_reminder(reminder_id)
    if result['status'] != 'enabled':
        return {'reminder_id': str(reminder_id), 'status': result['status'], 'notice_id': None}
    notice = authorize_due(reminder_id)
    notice = submit_notice(notice.id)
    return {'reminder_id': str(reminder_id), 'status': notice.status, 'notice_id': str(notice.id)}


@app.task(name='notifications.material_scheduler.schedule_due')
def schedule_due():
    with transaction.atomic():
        gate = EnvironmentGate.objects.get(environment=settings.SJIA_ENVIRONMENT)
        configured, reason = material_configuration(gate)
        if reason is not None:
            raise NotificationRejected(reason)
        runtime, _, _, _, _, policy = configured
        maximum = min(policy.max_due_per_tick, runtime.payload['api']['max_page_size'])
        now = timezone.now()
        rows, scanned = _due_candidates(limit=maximum, now=now)
    results = []
    for reminder in rows:
        due = next_permitted_time(reminder.schedule_at, reminder.timezone_name, reminder.quiet_start, reminder.quiet_end)
        permitted = next_permitted_time(now, reminder.timezone_name, reminder.quiet_start, reminder.quiet_end)
        if due <= now and permitted <= now:
            results.append(enqueue_reminder(reminder.id))
    return {'scanned': scanned, 'processed': len(results), 'items': results}
