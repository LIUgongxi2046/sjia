from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel

REPORT_SCHEMA_VERSION = 'report-recognition-2'


class CandidateModel(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)


NonblankText = Annotated[str, Field(min_length=1, pattern=r'\S')]


class ReportNumeric(RootModel[Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')]]):
    model_config = ConfigDict(strict=True)


class VoiceRequest(CandidateModel):
    text: Annotated[NonblankText, Field(max_length=8000)]


class ReportRequest(CandidateModel):
    file_id: UUID = Field(strict=False)


class RecognitionRequest(CandidateModel):
    file_group_id: UUID = Field(strict=False)
    file_group_version: Annotated[str, Field(pattern=r'^[1-9][0-9]*$')]


class VoiceCandidates(CandidateModel):
    temperature: float | None
    temperature_unit: Literal['C', 'F'] | None
    symptoms: list[NonblankText]
    observed_time: NonblankText | None
    note: NonblankText | None


class ReportItem(CandidateModel):
    name: NonblankText
    value: NonblankText | None
    comparator: Literal['=', '<', '<=', '≤', '>', '>=', '≥'] | None
    unit: NonblankText | None
    reference: NonblankText | None
    method: NonblankText | None
    source_text: NonblankText


class ReportCandidates(CandidateModel):
    items: list[ReportItem]


class TherapyItem(CandidateModel):
    name: NonblankText
    source_text: NonblankText


class TherapyCandidates(CandidateModel):
    items: list[TherapyItem]


class ArkMessage(BaseModel):
    model_config = ConfigDict(strict=True)
    content: str | None
    refusal: str | None = None


class ArkChoice(BaseModel):
    model_config = ConfigDict(strict=True)
    finish_reason: str
    message: ArkMessage


class ArkCompletion(BaseModel):
    model_config = ConfigDict(strict=True)
    id: NonblankText
    model: NonblankText
    usage: dict | None = None
    choices: list[ArkChoice]
