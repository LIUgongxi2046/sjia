from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


class MedicalIntervalRejected(Exception):
    def __init__(self, reason_code):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class MedicalInterval:
    lower: datetime
    upper: datetime
    upper_inclusive: bool

    def snapshot(self):
        return {'lower': self.lower.isoformat(), 'upper': self.upper.isoformat(), 'upper_inclusive': self.upper_inclusive}


def midnight_instant(day, zone):
    local = datetime.combine(day, time.min)
    candidates = {local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc) for fold in (0, 1)
        if local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == local}
    if len(candidates) != 1:
        raise MedicalIntervalRejected('MEDICAL_TIME_BOUNDARY_AMBIGUOUS')
    return candidates.pop()


def medical_interval(value):
    if value.precision not in ('date', 'minute', 'second'):
        raise MedicalIntervalRejected('MEDICAL_TIME_PRECISION_INSUFFICIENT')
    if value.resolution_status in ('unknown', 'ambiguous', 'invalid'):
        raise MedicalIntervalRejected('MEDICAL_TIME_UNRESOLVED')
    if value.precision == 'date':
        if value.local_date is None:
            raise MedicalIntervalRejected('MEDICAL_TIME_DATE_REQUIRED')
        if value.timezone_name is not None:
            zone = ZoneInfo(value.timezone_name)
        elif value.utc_offset_minutes is not None:
            zone = timezone(timedelta(minutes=value.utc_offset_minutes))
        else:
            raise MedicalIntervalRejected('MEDICAL_TIME_ZONE_REQUIRED')
        lower = midnight_instant(value.local_date, zone)
        upper = midnight_instant(value.local_date + timedelta(days=1), zone)
        if lower >= upper or value.instant is not None and not lower <= value.instant < upper:
            raise MedicalIntervalRejected('MEDICAL_TIME_CONTENT_INCONSISTENT')
        if value.timezone_name is not None and value.utc_offset_minutes is not None:
            offsets = {lower.astimezone(zone).utcoffset(), (upper - timedelta(microseconds=1)).astimezone(zone).utcoffset()}
            if timedelta(minutes=value.utc_offset_minutes) not in offsets:
                raise MedicalIntervalRejected('MEDICAL_TIME_CONTENT_INCONSISTENT')
        return MedicalInterval(lower, upper, False)
    if value.resolution_status != 'resolved' or value.instant is None or value.instant.utcoffset() is None:
        raise MedicalIntervalRejected('MEDICAL_TIME_UNRESOLVED')
    instant = value.instant.astimezone(timezone.utc)
    if value.timezone_name is not None:
        local = instant.astimezone(ZoneInfo(value.timezone_name))
        if value.utc_offset_minutes is not None and local.utcoffset() != timedelta(minutes=value.utc_offset_minutes):
            raise MedicalIntervalRejected('MEDICAL_TIME_CONTENT_INCONSISTENT')
        if value.local_datetime is not None and local.replace(tzinfo=None) != value.local_datetime:
            raise MedicalIntervalRejected('MEDICAL_TIME_CONTENT_INCONSISTENT')
    elif value.utc_offset_minutes is not None:
        local = instant.astimezone(timezone(timedelta(minutes=value.utc_offset_minutes)))
        if value.local_datetime is not None and local.replace(tzinfo=None) != value.local_datetime:
            raise MedicalIntervalRejected('MEDICAL_TIME_CONTENT_INCONSISTENT')
    else:
        raise MedicalIntervalRejected('MEDICAL_TIME_ZONE_REQUIRED')
    if local.microsecond or value.precision == 'minute' and local.second:
        raise MedicalIntervalRejected('MEDICAL_TIME_CONTENT_INCONSISTENT')
    if value.local_date is not None and local.date() != value.local_date:
        raise MedicalIntervalRejected('MEDICAL_TIME_CONTENT_INCONSISTENT')
    return MedicalInterval(instant, instant if value.precision == 'second' else instant + timedelta(minutes=1), value.precision == 'second')


def window_reason(interval, assessment_at, policy):
    if assessment_at.utcoffset() is None:
        raise MedicalIntervalRejected('ASSESSMENT_TIME_ZONE_REQUIRED')
    lower = assessment_at - timedelta(seconds=policy['current_max_age_seconds'])
    if interval.lower < lower or interval.lower == lower and not policy['lower_bound_inclusive']:
        return 'MEDICAL_TIME_OUTSIDE_WINDOW'
    if interval.upper > assessment_at or interval.upper == assessment_at and interval.upper_inclusive and not policy['upper_bound_inclusive']:
        return 'MEDICAL_TIME_OUTSIDE_WINDOW'
    return None
