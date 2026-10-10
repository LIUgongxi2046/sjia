from datetime import time
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, RootModel, TypeAdapter
from jsonschema import Draft202012Validator, FormatChecker

from archives.inputs import PROTOCOL, REGISTRY
from notifications.errors import NotificationRejected


Version = Annotated[str, Field(pattern=r'^[1-9][0-9]*$', max_length=19, strict=True)]
QuietTime = Annotated[str, Field(pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$', strict=True)]
LOCAL_TIME = TypeAdapter(time)
TIMEZONES = available_timezones()
VALIDATORS = {name: Draft202012Validator({**PROTOCOL, '$ref': f'#/$defs/{name}'},
    registry=REGISTRY, format_checker=FormatChecker()) for name in ('ReminderInput', 'ReminderChangeInput', 'PageQuery')}


def timestamp_text(value):
    if not isinstance(value, str):
        raise NotificationRejected('REMINDER_INPUT_INVALID')
    return value


Timestamp = Annotated[AwareDatetime, BeforeValidator(timestamp_text)]


class ReminderInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    target_type: Literal['draft', 'batch', 'record_task']
    target_id: UUID
    target_version: Version
    due_at: Timestamp
    timezone: str
    quiet_start: QuietTime | None
    quiet_end: QuietTime | None
    enabled: bool = Field(default=False, strict=True)


class ReminderActionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: Version
    action: Literal['disable', 'enable', 'pause', 'snooze']
    due_at: Timestamp | None


class ReminderConfigureInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: Version
    action: Literal['configure']
    target_version: Version
    due_at: Timestamp
    timezone: str
    quiet_start: QuietTime | None
    quiet_end: QuietTime | None
    enabled: bool | None = Field(default=None, strict=True)


class ReminderChangeInput(RootModel[Annotated[ReminderActionInput | ReminderConfigureInput,
        Field(discriminator='action')]]):
    pass


class ReminderQuery(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    cursor: str | None = Field(default=None, min_length=1, max_length=65536)
    limit: int | None = Field(default=None, ge=1)


class ReminderTargetQuery(ReminderQuery):
    target_type: Literal['draft', 'batch', 'record_task']


def checked_version(value):
    number = int(value)
    if number > 9223372036854775807:
        raise NotificationRejected('REMINDER_INPUT_INVALID')
    return number


def checked_schedule(value, now):
    if value is None or value.tzinfo is None or value.utcoffset() is None or value <= now:
        raise NotificationRejected('REMINDER_SCHEDULE_INVALID')
    return value


def checked_quiet_hours(timezone_name, start, end):
    if timezone_name not in TIMEZONES or (start is None) != (end is None):
        raise NotificationRejected('REMINDER_QUIET_HOURS_INVALID')
    if start is None:
        return None, None
    first, last = LOCAL_TIME.validate_python(start), LOCAL_TIME.validate_python(end)
    if first.tzinfo is not None or last.tzinfo is not None or first == last:
        raise NotificationRejected('REMINDER_QUIET_HOURS_INVALID')
    return first, last


def reminder_input(payload, *, change=False, now, check_schedule=True):
    name = 'ReminderChangeInput' if change else 'ReminderInput'
    if next(VALIDATORS[name].iter_errors(payload), None) is not None:
        raise NotificationRejected('REMINDER_INPUT_INVALID')
    value = ReminderChangeInput.model_validate(payload).root if change else ReminderInput.model_validate(payload)
    if change:
        checked_version(value.expected_version)
    if not change or value.action == 'configure':
        checked_version(value.target_version)
        checked_quiet_hours(value.timezone, value.quiet_start, value.quiet_end)
        if check_schedule:
            checked_schedule(value.due_at, now)
    elif value.action in ('disable', 'pause'):
        if value.due_at is not None:
            raise NotificationRejected('REMINDER_INPUT_INVALID')
    elif value.due_at is not None:
        if check_schedule:
            checked_schedule(value.due_at, now)
    elif value.action == 'snooze':
        raise NotificationRejected('REMINDER_SCHEDULE_INVALID')
    return value


def reminder_query(payload):
    if next(VALIDATORS['PageQuery'].iter_errors(payload), None) is not None:
        raise NotificationRejected('REMINDER_QUERY_INVALID')
    return ReminderQuery.model_validate(payload)


def reminder_target_query(payload):
    value = ReminderTargetQuery.model_validate(payload)
    reminder_query({key: item for key, item in payload.items() if key != 'target_type'})
    return value
