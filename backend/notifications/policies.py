from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import available_timezones

from pydantic import Field, model_validator

from assessment.policies import Evidence, PolicyModel, Text


class NativeNotificationPolicy(PolicyModel):
    schema_version: Literal['notification-in-app-2']
    environment: Text
    purpose: Text
    channel: Literal['in_app']
    endpoint: Literal['urn:sjia:notification:in-app:2']
    runtime_config_key: Text
    executor_id: UUID
    signing_secret_ref: Annotated[str, Field(pattern=r'^env:[A-Z][A-Z0-9_]*$')]
    permit_seconds: Annotated[int, Field(ge=1)]
    delivery_ticket_seconds: Annotated[int, Field(ge=1)]
    max_daily_messages: Annotated[int, Field(ge=1)]
    max_recipients_per_event: Annotated[int, Field(ge=1)]
    timezone: Text
    retention_policy_id: UUID
    evidence_refs: Evidence
    material_reminder_policy_id: UUID | None = None
    event_purposes: list[Text] = []

    @model_validator(mode='after')
    def valid_timezone(self):
        if self.timezone not in available_timezones():
            raise ValueError('通知预算需要准确时区')
        if len(set(self.event_purposes)) != len(self.event_purposes) or self.purpose in self.event_purposes:
            raise ValueError('事件通知用途必须唯一，并与主要用途分别记录')
        return self


class MaterialReminderChannelPolicy(PolicyModel):
    schema_version: Literal['material-reminder-channel-2']
    environment: Text
    purpose: Literal['family_observation']
    channel: Literal['in_app']
    provider_config_key: Text
    runtime_config_key: Text
    notification_text: Annotated[str, Field(min_length=1, max_length=4000, pattern=r'.*\S.*')]
    max_due_per_tick: Annotated[int, Field(ge=1)]
    evidence_refs: Evidence
