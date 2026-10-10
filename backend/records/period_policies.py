from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Text = Annotated[str, Field(min_length=1, pattern=r'.*\S.*')]


class SamplingFrequencyPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_version: Literal['sampling-frequency-1']
    purpose: Literal['family_observation']
    concept_codes: Annotated[list[Text], Field(min_length=1)]
    interval_hours: Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')]
    evidence_refs: Annotated[list[Text], Field(min_length=1)]

    @model_validator(mode='after')
    def complete_basis(self):
        if (Decimal(self.interval_hours) <= 0 or len(set(self.concept_codes)) != len(self.concept_codes)
                or len(set(self.evidence_refs)) != len(self.evidence_refs)):
            raise ValueError('采样频率需要明确的正数间隔、适用项目及独立批准依据')
        return self
