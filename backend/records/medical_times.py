from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.tz import datetime_ambiguous, datetime_exists
from pydantic import TypeAdapter

from api.base import ApiError
from medical_time.models import MedicalTime


DATE = TypeAdapter(date)
DATETIME = TypeAdapter(datetime)


def time_values(payload):
    names = ('raw_text', 'local_year', 'local_month', 'end_year', 'end_month', 'relative_text',
        'timezone_name', 'utc_offset_minutes')
    values = {name: payload.get(name) for name in names}
    values.update(precision=payload['precision'], resolution_status=payload['resolution_status'], instant=None,
        local_date=DATE.validate_python(payload['local_date']) if payload.get('local_date') is not None else None,
        local_datetime=DATETIME.validate_python(payload['local_datetime']) if payload.get('local_datetime') is not None else None)
    local = values['local_datetime']
    if local is not None and (local.utcoffset() is not None or local.microsecond
            or values['precision'] == 'minute' and local.second):
        raise ApiError('MEDICAL_TIME_LOCAL_CONTENT_INVALID', 422)
    if (local is not None and values['local_date'] is not None and local.date() != values['local_date']):
        raise ApiError('MEDICAL_TIME_LOCAL_CONTENT_INVALID', 422)
    if values['precision'] in ('minute', 'second') and local is not None and values['resolution_status'] == 'local_only':
        zone = (ZoneInfo(values['timezone_name']) if values['timezone_name'] is not None else
            timezone(timedelta(minutes=values['utc_offset_minutes'])) if values['utc_offset_minutes'] is not None else None)
        if zone is not None:
            if not datetime_exists(local, zone):
                values['resolution_status'] = 'invalid'
            elif datetime_ambiguous(local, zone):
                values['resolution_status'] = 'ambiguous'
            else:
                resolved = local.replace(tzinfo=zone)
                offset = int(resolved.utcoffset().total_seconds() / 60)
                if values['utc_offset_minutes'] is not None and values['utc_offset_minutes'] != offset:
                    raise ApiError('MEDICAL_TIME_OFFSET_INCONSISTENT', 422)
                values.update(instant=resolved.astimezone(timezone.utc), resolution_status='resolved', utc_offset_minutes=offset)
    return values


def create_medical_time(patient, payload):
    return MedicalTime.objects.create(patient=patient, **time_values(payload))
