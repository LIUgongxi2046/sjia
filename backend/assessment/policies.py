from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


Text = Annotated[str, Field(min_length=1, pattern=r'.*\S.*')]
Evidence = Annotated[list[Text], Field(min_length=1)]
Context = Literal['history', 'current', 'discharge', 'actual']
VariableCode = Literal['ferritin', 'platelet_count', 'ast', 'triglycerides', 'fibrinogen', 'ldh',
    'cns', 'hemorrhagic_manifestations', 'active_arthritis']
DECIMAL_UNITS = {'ferritin': 'ng/mL', 'platelet_count': '10^9/L', 'ast': 'U/L',
    'triglycerides': 'mg/dL', 'fibrinogen': 'mg/dL', 'ldh': 'U/L'}
CLASSIFICATION_VARIABLES = {'ferritin', 'platelet_count', 'ast', 'triglycerides', 'fibrinogen'}
MS_VARIABLES = {'cns', 'hemorrhagic_manifestations', 'active_arthritis', 'platelet_count', 'ldh', 'fibrinogen', 'ferritin'}


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @model_validator(mode='after')
    def evidence_unique(self):
        if hasattr(self, 'evidence_refs') and len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError('批准依据不能重复')
        return self


class BinaryValue(PolicyModel):
    value: Text
    encoded: Literal[0, 1]
    assertion_state: Literal['present', 'absent']


class StaticVariable(PolicyModel):
    variable_code: VariableCode
    concept_code: Text
    value_type: Literal['decimal', 'categorical']
    normalized_unit: Text | None
    mapping_config_id: UUID | None
    categorical_values: list[BinaryValue]
    accepted_context_types: Annotated[list[Context], Field(min_length=1)]

    @model_validator(mode='after')
    def variable_shape(self):
        if len(set(self.accepted_context_types)) != len(self.accepted_context_types):
            raise ValueError('事实语境不能重复')
        if self.variable_code in DECIMAL_UNITS:
            if (self.value_type != 'decimal' or self.normalized_unit != DECIMAL_UNITS[self.variable_code]
                    or self.mapping_config_id is None or self.categorical_values):
                raise ValueError('数值变量必须采用 PRD 单位及准确批准映射')
        elif (self.value_type != 'categorical' or self.normalized_unit is not None or self.mapping_config_id is not None
                or {item.encoded for item in self.categorical_values} != {0, 1}
                or len({item.value for item in self.categorical_values}) != len(self.categorical_values)):
            raise ValueError('专业变量必须明确已核对状态与零一编码')
        return self


class PremiseCondition(PolicyModel):
    concept_code: Text
    states: Annotated[list[BinaryValue], Field(min_length=2)]
    accepted_context_types: Annotated[list[Context], Field(min_length=1)]

    @model_validator(mode='after')
    def known_states(self):
        if (len({item.value for item in self.states}) != len(self.states) or {item.encoded for item in self.states} != {0, 1}
                or len(set(self.accepted_context_types)) != len(self.accepted_context_types)):
            raise ValueError('适用前提必须具有独立且完整的明确状态')
        return self


class StaticRulePolicy(PolicyModel):
    schema_version: Literal['assessment-rule-2']
    kind: Literal['classification', 'ms']
    sjia: PremiseCondition
    fever: PremiseCondition
    variables: Annotated[list[StaticVariable], Field(min_length=1)]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def complete_variables(self):
        required = CLASSIFICATION_VARIABLES if self.kind == 'classification' else MS_VARIABLES
        if len(self.variables) != len(required) or {item.variable_code for item in self.variables} != required:
            raise ValueError('静态规则需要 PRD 的完整变量集合')
        concepts = [self.sjia.concept_code, self.fever.concept_code, *(item.concept_code for item in self.variables)]
        if len(set(concepts)) != len(concepts):
            raise ValueError('适用前提和计算变量需要独立项目身份')
        return self


class PremiseVariable(PolicyModel):
    variable_code: Literal['sjia', 'fever']
    concept_code: Text
    value_type: Literal['categorical']
    normalized_unit: None
    mapping_config_id: None
    categorical_values: list[BinaryValue]
    accepted_context_types: list[Context]


def static_variable(rule, variable_code):
    if variable_code in ('sjia', 'fever'):
        premise = getattr(rule, variable_code)
        return PremiseVariable(variable_code=variable_code, concept_code=premise.concept_code, value_type='categorical',
            normalized_unit=None, mapping_config_id=None, categorical_values=premise.states,
            accepted_context_types=premise.accepted_context_types)
    matches = [item for item in rule.variables if item.variable_code == variable_code]
    if len(matches) != 1:
        raise ValueError('计算变量不属于准确静态规则')
    return matches[0]


class QualificationPolicy(PolicyModel):
    schema_version: Literal['qualification-policy-2']
    purpose: Text
    allowed_origin_types: Annotated[list[Literal['self_report', 'report_transcription', 'authorized_clinical']], Field(min_length=1)]
    verification_evidence_purpose: Text
    evidence_refs: Evidence

    @model_validator(mode='after')
    def origins_unique(self):
        if len(set(self.allowed_origin_types)) != len(self.allowed_origin_types):
            raise ValueError('资料来源类型不能重复')
        return self


class ContextVariable(PolicyModel):
    variable_code: Text
    concept_code: Text
    value_type: Literal['categorical']
    normalized_unit: None
    mapping_config_id: None
    categorical_values: Annotated[list[BinaryValue], Field(min_length=2)]
    accepted_context_types: Annotated[list[Context], Field(min_length=1)]

    @model_validator(mode='after')
    def explicit_states(self):
        if (self.variable_code in CLASSIFICATION_VARIABLES | MS_VARIABLES | {'sjia', 'fever'}
                or len({item.value for item in self.categorical_values}) != len(self.categorical_values)
                or {item.encoded for item in self.categorical_values} != {0, 1}
                or len(set(self.accepted_context_types)) != len(self.accepted_context_types)):
            raise ValueError('背景条件需要独立项目和明确核对状态')
        return self


class StaticContextPolicy(PolicyModel):
    schema_version: Literal['assessment-context-policy-2']
    role: Literal['population', 'therapy', 'clinical_finding', 'late_result']
    purpose: Text
    conditions: list[ContextVariable]
    unknown_action: Literal['insufficient', 'record_limitation']
    not_met_action: Literal['not_applicable', 'record_limitation']
    require_available_and_verified_by_cutoff: Literal[True]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def independent_conditions(self):
        for attribute in ('variable_code', 'concept_code'):
            values = [getattr(item, attribute) for item in self.conditions]
            if len(set(values)) != len(values):
                raise ValueError('背景条件身份不能重复')
        if self.role == 'population' and (self.unknown_action != 'insufficient' or self.not_met_action != 'not_applicable'):
            raise ValueError('人群适用条件需要保存不足和不适用状态')
        return self


class MappingEntry(PolicyModel):
    mapping_id: UUID
    concept_code: Text
    source_revision_id: UUID
    raw_unit: Text
    normalized_unit: Text
    method_scope: Text
    multiplier: Annotated[str, Field(pattern=r'^(0|[1-9][0-9]*)(\.[0-9]+)?$')]
    additive: Annotated[str, Field(pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')]
    exact_values_only: bool

    @model_validator(mode='after')
    def positive_multiplier(self):
        if Decimal(self.multiplier) <= 0:
            raise ValueError('单位映射需要明确的正数转换系数')
        return self


class MappingPolicy(PolicyModel):
    schema_version: Literal['unit-mapping-2']
    mappings: Annotated[list[MappingEntry], Field(min_length=1)]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def mapping_identity(self):
        identities = [item.mapping_id for item in self.mappings]
        keys = [(item.concept_code, item.source_revision_id, item.raw_unit, item.method_scope) for item in self.mappings]
        if len(set(identities)) != len(identities) or len(set(keys)) != len(keys):
            raise ValueError('单位映射身份和适用范围不能重复')
        return self
