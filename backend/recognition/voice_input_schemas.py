from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class VoiceModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)


class VoiceInputCreate(VoiceModel):
    request_id: UUID = Field(strict=False)
    authorization_id: UUID = Field(strict=False)
    field_config_id: UUID = Field(strict=False)
    confirmed_text: Annotated[str, Field(min_length=1, max_length=8000, pattern=r'\S')]
    transcript_confirmed: Literal[True]
    input_mode: Literal['LOCAL_AUDIO_CONFIRMED_TEXT', 'SIMULATION_TEXT_ONLY']
    transcription_evidence_id: UUID | None = Field(default=None, strict=False)

    @model_validator(mode='after')
    def exact_transcription_mode(self):
        if self.input_mode == 'LOCAL_AUDIO_CONFIRMED_TEXT' and self.transcription_evidence_id is None:
            raise ValueError('本机音频确认文字必须引用准确转写证据')
        if self.input_mode == 'SIMULATION_TEXT_ONLY' and self.transcription_evidence_id is not None:
            raise ValueError('模拟文字输入不能声明音频转写证据')
        return self

    @field_validator('transcript_confirmed', mode='before')
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError('确认文字需要本人明确确认')
        return value


class VoiceInputProcess(VoiceModel):
    expected_status_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]
    purpose_copy_id: UUID = Field(strict=False)
    provider_config_id: UUID = Field(strict=False)


class VoiceFieldDecision(VoiceModel):
    field_path: Literal['temperature', 'temperature_unit', 'symptoms', 'observed_time', 'note']
    decision: Literal['accepted', 'modified', 'unknown', 'excluded']
    selected_value: str | list[str] | dict | None
    record_field_codes: list[str]


class VoiceInputAdoption(VoiceModel):
    voice_input_id: UUID = Field(strict=False)
    expected_status_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]
    decisions: Annotated[list[VoiceFieldDecision], Field(min_length=5, max_length=5)]

    @model_validator(mode='after')
    def complete_decisions(self):
        required = {'temperature', 'temperature_unit', 'symptoms', 'observed_time', 'note'}
        if {row.field_path for row in self.decisions} != required:
            raise ValueError('全部候选字段必须具有唯一的本人决定')
        for row in self.decisions:
            if isinstance(row.selected_value, dict) and row.field_path != 'observed_time':
                raise ValueError('只有发生时间允许提交明确的时间对象')
            if row.decision in ('unknown', 'excluded') and row.selected_value is not None:
                raise ValueError('未知或排除的候选不得提交采用值')
            if row.decision == 'excluded' and row.record_field_codes:
                raise ValueError('排除的候选不得关联保存字段')
            if len(set(row.record_field_codes)) != len(row.record_field_codes):
                raise ValueError('保存字段引用必须唯一')
        return self
