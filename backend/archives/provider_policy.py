from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ArchiveProviderPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['provider-1']
    environment: str = Field(min_length=1)
    provider: Literal['ark']
    endpoint: HttpUrl
    model_version: str = Field(min_length=1)
    field_config_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    # Optional explicit bindings let the sole OCR provider serve both fixed
    # report/therapy workflows. Changes require ordinary config approval.
    workflow_field_config_ids: dict[Literal['report', 'therapy'],
        Annotated[str, Field(pattern=r'^[0-9a-f-]{36}$')]] = Field(default_factory=dict)
    # Explicit opt-in; requires a new normally qualified provider version.
    report_schema_version: Literal['report-recognition-2', 'report-recognition-3'] = 'report-recognition-2'
    pricing_evidence_ref: str = Field(min_length=1)
    input_per_million: str = Field(pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')
    output_per_million: str = Field(pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')
    currency: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
