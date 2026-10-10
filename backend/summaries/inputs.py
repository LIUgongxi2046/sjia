from datetime import date
from typing import Annotated, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from relations.inputs import ApplicationIntentInput, ApplicationWriteHeaders, ApplicationRetryInput


class SummaryInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    from_date: Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]
    to_date: Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$')]
    scope_timezone: str
    include_unknown_time: bool
    record_kinds: Annotated[list[Literal['observation', 'report', 'therapy']], Field(min_length=1, max_length=3)]
    include_source_status: bool

    @field_validator('from_date', 'to_date')
    @classmethod
    def exact_date(cls, value):
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError('摘要日期格式无效。')
        return value

    @field_validator('scope_timezone')
    @classmethod
    def actual_timezone(cls, value):
        if value not in available_timezones():
            raise ValueError('摘要时区无效。')
        return value

    @model_validator(mode='after')
    def valid_scope(self):
        if self.to_date < self.from_date or len(set(self.record_kinds)) != len(self.record_kinds):
            raise ValueError('摘要范围无效。')
        return self


class SummaryIntentInput(ApplicationIntentInput):
    operation_id: Literal['summary_create']


class SummaryWriteHeaders(ApplicationWriteHeaders):
    pass


class SummaryRetryInput(ApplicationRetryInput):
    pass
