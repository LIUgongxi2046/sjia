"""Opt-in v3 report-time candidates. Extraction never confirms or adopts time."""
from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from recognition.schemas import CandidateModel, NonblankText, ReportItem, ReportCandidates

REPORT_TIME_SCHEMA_VERSION = 'report-recognition-3'


class ReportTimeCandidate(CandidateModel):
    raw_text: NonblankText
    precision: Literal['unknown', 'year', 'month', 'month_range', 'relative', 'date', 'minute', 'second']
    local_year: Annotated[int, Field(ge=1, le=9999)] | None
    local_month: Annotated[int, Field(ge=1, le=12)] | None
    end_year: Annotated[int, Field(ge=1, le=9999)] | None
    end_month: Annotated[int, Field(ge=1, le=12)] | None
    relative_text: NonblankText | None
    local_date: str | None
    local_datetime: str | None
    timezone_name: NonblankText | None
    utc_offset_minutes: Annotated[int, Field(ge=-1439, le=1439)] | None
    instant: None
    resolution_status: Literal['unknown', 'local_only']

    @model_validator(mode='after')
    def exact_precision(self):
        required = {'unknown': (), 'year': ('local_year',), 'month': ('local_year','local_month'),
            'month_range': ('local_year','local_month','end_year','end_month'), 'relative': ('relative_text',),
            'date': ('local_date',), 'minute': ('local_datetime',), 'second': ('local_datetime',)}[self.precision]
        allowed = set(required)
        if self.precision in ('minute','second'):
            allowed.update(('local_date','timezone_name','utc_offset_minutes'))
        names = ('local_year','local_month','end_year','end_month','relative_text','local_date',
            'local_datetime','timezone_name','utc_offset_minutes')
        if any(getattr(self,key) is None for key in required) or any(
                getattr(self,key) is not None for key in names if key not in allowed):
            raise ValueError('TIME_PRECISION_CONTENT_MISMATCH')
        if self.resolution_status != ('unknown' if self.precision == 'unknown' else 'local_only'):
            raise ValueError('TIME_CANDIDATE_NOT_RESOLVED')
        if self.local_date is not None:
            if len(self.local_date) != 10:
                raise ValueError('TIME_DATE_INVALID')
            date.fromisoformat(self.local_date)
        if self.local_datetime is not None:
            expected = 16 if self.precision == 'minute' else 19
            if len(self.local_datetime) != expected or self.local_datetime[10] != 'T':
                raise ValueError('TIME_PRECISION_CONTENT_MISMATCH')
            local = datetime.fromisoformat(self.local_datetime)
            if local.tzinfo is not None or local.microsecond or self.precision == 'minute' and local.second:
                raise ValueError('TIME_LOCAL_INVALID')
            if self.local_date is not None and local.date().isoformat() != self.local_date:
                raise ValueError('TIME_DATE_MISMATCH')
        if self.timezone_name is not None:
            try:
                ZoneInfo(self.timezone_name)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError('TIME_ZONE_INVALID') from None
        if self.precision == 'month_range' and (self.local_year,self.local_month) > (self.end_year,self.end_month):
            raise ValueError('TIME_RANGE_INVALID')
        if self.precision == 'relative' and self.relative_text != self.raw_text:
            raise ValueError('TIME_RELATIVE_MUST_PRESERVE_QUOTE')
        return self


class ReportTimeItem(ReportItem):
    sample_time: ReportTimeCandidate | None

    @model_validator(mode='after')
    def time_is_source_quote(self):
        if self.sample_time is not None and self.sample_time.raw_text not in self.source_text:
            raise ValueError('TIME_SOURCE_QUOTE_REQUIRED')
        return self


class ReportTimeCandidates(CandidateModel):
    schema_version: Literal['report-recognition-3']
    source_text: NonblankText
    sample_time: ReportTimeCandidate | None
    result_time: ReportTimeCandidate | None
    items: list[ReportTimeItem]

    @model_validator(mode='after')
    def report_times_are_source_quotes(self):
        if any(value is not None and value.raw_text not in self.source_text
                for value in (self.sample_time,self.result_time)):
            raise ValueError('TIME_SOURCE_QUOTE_REQUIRED')
        return self


def report_schema_version(provider=None):
    version = provider.payload.get('report_schema_version', 'report-recognition-2') if provider else 'report-recognition-2'
    if version not in ('report-recognition-2', REPORT_TIME_SCHEMA_VERSION):
        from recognition.invocation import InvocationRejected
        raise InvocationRejected('MODEL_CONTRACT_UNREGISTERED')
    return version


def report_candidate_model(provider=None):
    return ReportTimeCandidates if report_schema_version(provider) == REPORT_TIME_SCHEMA_VERSION else ReportCandidates
