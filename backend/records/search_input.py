from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RecordSearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    kind: Literal['observation', 'report', 'therapy', 'professional'] | None = None
    concept_code: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    original_name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    source_organization: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    origin_type: Literal['self_report', 'report_transcription', 'authorized_clinical'] | None = None
    verification_status: Literal['unverified', 'pending', 'verified', 'needs_correction', 'revoked'] | None = None
    confirmed_by_self: bool = False
    from_date: str | None = None
    to_date: str | None = None
    cursor: Annotated[str, Field(min_length=1, max_length=8192)] | None = None
    limit: Annotated[int, Field(ge=1, le=100)] = 20

    @field_validator('concept_code', 'original_name', 'source_organization')
    @classmethod
    def meaningful_text(cls, value):
        if value is not None and not value.strip():
            raise ValueError('查询文字不能为空。')
        return value

    @field_validator('from_date', 'to_date')
    @classmethod
    def calendar_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError('查询日期需要完整年月日。')
        return value

    @model_validator(mode='after')
    def ordered_dates(self):
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError('结束日期不能早于开始日期。')
        return self
