from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from assessment.policies import Evidence, PolicyModel, Text


class VisibilityPolicy(PolicyModel):
    schema_version: Literal['visibility-1']
    environment: Text
    purpose: Text
    field_codes: Annotated[list[Text], Field(min_length=1)]
    clinical_fields: list[Literal['level', 'classification', 'ms']]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def unique_fields(self):
        if len(set(self.field_codes)) != len(self.field_codes) or len(set(self.clinical_fields)) != len(self.clinical_fields):
            raise ValueError('可见字段必须准确且不能重复')
        return self


class ContentPolicy(PolicyModel):
    schema_version: Literal['content-1']
    environment: Text
    purpose: Text
    rule_id: UUID
    window_policy_id: UUID
    approved_text: Text
    level: Literal['attention', 'warning', 'urgent']
    evidence_refs: Evidence


class NotificationPolicy(PolicyModel):
    schema_version: Literal['notification-1']
    environment: Text
    purpose: Text
    channels: Annotated[list[Literal['in_app', 'sms', 'wechat', 'web_push']], Field(min_length=1)]
    max_attempts: Annotated[int, Field(ge=1)]
    retry_delays_seconds: list[Annotated[int, Field(ge=1)]]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def complete_channels(self):
        if len(set(self.channels)) != len(self.channels) or len(self.retry_delays_seconds) != self.max_attempts - 1:
            raise ValueError('通知渠道必须唯一，重试预算需要准确间隔')
        return self


class ValidationPolicy(PolicyModel):
    schema_version: Literal['validation-1']
    environment: Text
    purpose: Text
    rule_id: UUID
    window_policy_id: UUID
    dataset_revision_id: UUID
    report_ref: Text
    report_sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    evidence_refs: Evidence


class SummaryPolicy(PolicyModel):
    schema_version: Literal['summary-1']
    environment: Text
    purpose: Text
    field_codes: Annotated[list[Text], Field(min_length=1)]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def unique_fields(self):
        if len(set(self.field_codes)) != len(self.field_codes):
            raise ValueError('摘要字段必须准确且不能重复')
        return self
