from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ArchiveProviderPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['provider-1']
    environment: str = Field(min_length=1)
    provider: Literal['ark']
    endpoint: HttpUrl
    model_version: str = Field(min_length=1)
    field_config_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    pricing_evidence_ref: str = Field(min_length=1)
    input_per_million: str = Field(pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')
    output_per_million: str = Field(pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')
    currency: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
