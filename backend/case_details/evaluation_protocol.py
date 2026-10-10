from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from case_details.evaluation_units import Digest, LabelUnit, References, Text, UnitCoverage, UnitModel, Version


UnitName = Literal['files', 'pages', 'documents', 'patients', 'encounters', 'sampling_events',
    'mas_events', 'observation_periods', 'labels']


class EvaluationProtocol(UnitModel):
    schema_version: Literal['evaluation-protocol-1']
    purpose: Text
    evaluation_kind: Literal['format', 'workflow', 'clinical']
    measurement_plan_ref: Text
    available_before: AwareDatetime
    required_units: list[UnitName]
    criteria_refs: References

    @field_validator('required_units', 'criteria_refs')
    @classmethod
    def unique_items(cls, value):
        if not value or len(set(value)) != len(value):
            raise ValueError('评价范围及依据必须明确且不能重复')
        return value

    @model_validator(mode='after')
    def denominator_scope(self):
        purposes = {'format': 'format_evaluation', 'workflow': 'workflow_evaluation', 'clinical': 'clinical_evaluation'}
        if self.purpose != purposes[self.evaluation_kind]:
            raise ValueError('评价类别必须使用对应用途')
        required = {'files', 'patients'}
        if self.evaluation_kind == 'format':
            required.add('pages')
        if self.evaluation_kind == 'workflow':
            required.add('documents')
        if self.evaluation_kind == 'clinical':
            required.update({'observation_periods', 'labels'})
        if not required <= set(self.required_units):
            raise ValueError('评价方案必须明确对应统计单位')
        return self


class EvaluationLabels(UnitModel):
    schema_version: Literal['evaluation-labels-1']
    protocol_ref: Text
    source_manifest_sha256: Digest
    labels: UnitCoverage[LabelUnit]


class DatasetPartitionEvidence(UnitModel):
    schema_version: Literal['evaluation-partition-1']
    dataset_id: UUID
    protocol_ref: Text
    source_manifest_sha256: Digest
    patient_id: UUID
    partition: Literal['development', 'validation']
    authorization_ids: list[UUID] = Field(min_length=1)
    confirmed_by: UUID
    confirmed_at: AwareDatetime

    @field_validator('authorization_ids')
    @classmethod
    def unique_authorizations(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('分组用途授权不能重复')
        return value


class DatasetFreezeEvidence(UnitModel):
    schema_version: Literal['evaluation-freeze-1']
    dataset_id: UUID
    version_id: UUID
    version: Version
    checksum: Digest
    protocol_ref: Text
    confirmed_by: UUID
    confirmed_at: AwareDatetime
