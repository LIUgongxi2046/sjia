from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.tz import datetime_ambiguous, resolve_imaginary

from notifications.errors import NotificationRejected
from notifications.reminder_schemas import checked_quiet_hours


def utc_instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NotificationRejected('REMINDER_SCHEDULE_INVALID')
    return value.astimezone(timezone.utc)


def next_permitted_time(scheduled_at, timezone_name, quiet_start, quiet_end, *, not_before=None):
    start, end = checked_quiet_hours(timezone_name, quiet_start, quiet_end)
    if start is not None and (start.microsecond or end.microsecond):
        raise NotificationRejected('REMINDER_QUIET_HOURS_INVALID')
    candidate = utc_instant(scheduled_at)
    if not_before is not None:
        candidate = max(candidate, utc_instant(not_before))
    if start is None:
        return candidate
    zone = ZoneInfo(timezone_name)
    while True:
        local = candidate.astimezone(zone)
        clock = local.time()
        inside = start <= clock < end if start < end else clock >= start or clock < end
        if not inside:
            return candidate
        ending_date = local.date() + timedelta(days=int(start > end and clock >= start))
        ending = resolve_imaginary(datetime.combine(ending_date, end, tzinfo=zone))
        # 重复出现的结束时间采用第二次，保持整个重复时段安静。
        if datetime_ambiguous(ending):
            ending = ending.replace(fold=1)
        following = utc_instant(ending)
        if following <= candidate:
            raise NotificationRejected('REMINDER_SCHEDULE_INVALID')
        candidate = following


def reminder_next_due_at(reminder, target_state, *, now):
    if reminder.status != 'enabled' or target_state != 'pending':
        return None
    return next_permitted_time(reminder.schedule_at, reminder.timezone_name,
        reminder.quiet_start, reminder.quiet_end, not_before=now)
