from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from assessment.policies import (
    BinaryValue, Context, ContextVariable, DECIMAL_UNITS, Evidence, MS_VARIABLES,
    PolicyModel, StaticVariable, Text,
)


# These are serialization constraints, never clinical parameters. Every weight,
# threshold, evidence reference and human-readable preset name is supplied by an
# independently approved configuration; this module provides no clinical preset.
DecimalText = Annotated[str, Field(pattern=r'^(0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*(\.[0-9]+)?)$')]
FractionText = Annotated[str, Field(pattern=r'^(0\.[0-9]*[1-9][0-9]*|1(\.0+)?)$')]


class WeightedSignal(PolicyModel):
    signal_ref: Text
    classification: Literal['core', 'general']
    weight: DecimalText

    @model_validator(mode='after')
    def positive_weight(self):
        if Decimal(self.weight) <= 0:
            raise ValueError('指标权重必须明确且大于零')
        return self


class WeightedCoreGate(PolicyModel):
    operator: Literal['all', 'at_least']
    minimum_met: Annotated[int, Field(ge=1)] | None

    @model_validator(mode='after')
    def explicit_core_gate(self):
        if (self.operator == 'all') != (self.minimum_met is None):
            raise ValueError('核心组合必须明确全部满足或至少满足的数量')
        return self


class WeightedCombination(PolicyModel):
    """Versioned, fixed-denominator three-valued aggregation contract.

    All declared conditions participate exactly once. Unknown conditions remain
    in the denominator and contribute only to the possible upper bound. Core
    membership adds the explicitly configured core gate; it assigns no weight.
    """

    schema_version: Literal['weighted-combination-1']
    operator: Literal['weighted']
    members: Annotated[list[WeightedSignal], Field(min_length=1)]
    denominator: Literal['all_configured_weights']
    core_gate: WeightedCoreGate
    threshold_fraction: FractionText
    minimum_known_fraction: FractionText
    unknown_policy: Literal['bounded_no_imputation']
    evidence_refs: Evidence

    @model_validator(mode='after')
    def complete_weighted_contract(self):
        references = [item.signal_ref for item in self.members]
        if len(set(references)) != len(references):
            raise ValueError('加权指标不能重复')
        core_count = sum(item.classification == 'core' for item in self.members)
        if not core_count:
            raise ValueError('加权方案必须明确至少一个核心指标')
        if self.core_gate.minimum_met is not None and self.core_gate.minimum_met > core_count:
            raise ValueError('核心指标要求不能超过已配置核心指标数量')
        if any(not 0 < Decimal(value) <= 1 for value in
                (self.threshold_fraction, self.minimum_known_fraction)):
            raise ValueError('加权阈值和已知权重比例必须大于零且不超过一')
        return self


class SensitivityPreset(PolicyModel):
    """A name for one immutable approved rule, not a numerical override."""

    schema_version: Literal['dynamic-sensitivity-preset-1']
    preset_key: Text
    display_name: Text
    description: Text


def weighted_trigger_reason(payload):
    """Semantic validation after the complete alert-trigger-2 JSON schema."""
    combination = WeightedCombination.model_validate(payload['combination'])
    SensitivityPreset.model_validate(payload['sensitivity_preset'])
    codes = [item['signal_id'] for item in payload['conditions']]
    if len(codes) != len(set(codes)):
        return 'CONFIG_SIGNAL_DUPLICATE'
    if {item.signal_ref for item in combination.members} != set(codes):
        return 'CONFIG_WEIGHTED_SIGNAL_SCOPE_INVALID'
    return None


class DynamicNumericVariable(PolicyModel):
    """Explicit numeric input identity, never inferred from available report fields.

    An accepted definition is only a contract. Qualification still requires the
    referenced mapping's current approval, implementation validation and exact
    concept/source/unit/method match. New codes have no units or clinical defaults.
    Existing fixed-rule codes retain their established units and meanings.
    """

    variable_code: Text
    concept_code: Text
    value_type: Literal['decimal']
    normalized_unit: Text
    mapping_config_id: UUID
    categorical_values: Annotated[list[BinaryValue], Field(max_length=0)]
    accepted_context_types: Annotated[list[Context], Field(min_length=1)]

    @model_validator(mode='after')
    def explicit_numeric_shape(self):
        if len(set(self.accepted_context_types)) != len(self.accepted_context_types):
            raise ValueError('事实语境不能重复')
        if self.variable_code in (MS_VARIABLES - DECIMAL_UNITS.keys()) | {'sjia', 'fever'}:
            raise ValueError('动态数值变量不能改变固定专业变量的含义')
        if self.variable_code in DECIMAL_UNITS and self.normalized_unit != DECIMAL_UNITS[self.variable_code]:
            raise ValueError('既有固定数值变量必须保留 PRD 单位')
        return self


class MethodPair(PolicyModel):
    baseline_method: Text | None
    current_method: Text | None


class DynamicComparison(PolicyModel):
    variable_code: Text
    allow_negative_baseline: bool
    relative_decimal_places: Annotated[int, Field(ge=0)]
    rounding: Literal['ROUND_HALF_EVEN', 'ROUND_HALF_UP', 'ROUND_DOWN']
    methods: Annotated[list[MethodPair], Field(min_length=1)]

    @model_validator(mode='after')
    def unique_methods(self):
        pairs = [(item.baseline_method, item.current_method) for item in self.methods]
        if len(set(pairs)) != len(pairs):
            raise ValueError('可比较方法组合不能重复')
        return self


class DynamicInputPolicy(PolicyModel):
    schema_version: Literal['dynamic-input-policy-2']
    purpose: Text
    variables: list[DynamicNumericVariable | StaticVariable | ContextVariable]
    comparisons: list[DynamicComparison]
    max_combination_depth: Annotated[int, Field(ge=1)]
    max_combination_operands: Annotated[int, Field(ge=1)]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def unique_variables(self):
        codes = [item.variable_code for item in self.variables]
        concepts = [item.concept_code for item in self.variables]
        decimal_codes = {item.variable_code for item in self.variables if item.value_type == 'decimal'}
        comparisons = [item.variable_code for item in self.comparisons]
        if (len(set(codes)) != len(codes) or len(set(concepts)) != len(concepts)
                or len(set(comparisons)) != len(comparisons) or set(comparisons) != decimal_codes):
            raise ValueError('动态变量、项目身份与比较定义需要准确对应')
        return self


class LifecyclePolicy(PolicyModel):
    schema_version: Literal['alert-lifecycle-2']
    purpose: Text
    kind: Literal['reset', 'retrigger']
    conditions: Annotated[list[dict], Field(min_length=1)]
    combination: dict
    minimum_sample_count: Annotated[int, Field(ge=1)]
    minimum_duration_seconds: Annotated[int, Field(ge=1)]
    allow_rule_version_change: bool
    evidence_refs: Evidence


class ScheduledAssessment(PolicyModel):
    kind: Literal['classification', 'ms', 'hlh']
    rule_id: UUID
    window_policy_id: UUID
    verification_policy_id: UUID
    input_rule_id: UUID | None = None

    @model_validator(mode='after')
    def hlh_input_rule_required(self):
        if self.kind == 'hlh' and self.input_rule_id is None:
            raise ValueError('HLH 计划需要明确批准的输入规则')
        if self.kind != 'hlh' and 'input_rule_id' in self.model_fields_set:
            raise ValueError('既有静态计划不能附带未使用的输入规则')
        return self


class DynamicSchedulePolicy(PolicyModel):
    schema_version: Literal['dynamic-schedule-2']
    environment: Text
    purpose: Text
    patient_id: UUID
    actor_id: UUID
    policy_id: UUID
    input_rule_id: UUID
    window_policy_id: UUID
    verification_policy_id: UUID
    use_authorization_id: UUID
    observation_period_id: UUID | None
    static_assessments: list[ScheduledAssessment]
    event_types: Annotated[list[Literal['verification.changed', 'configuration.changed', 'clinical.context_changed', 'dynamic.timer']], Field(min_length=1)]
    watch_config_ids: list[UUID]
    interval_seconds: Annotated[int, Field(ge=1)] | None
    starts_at: AwareDatetime | None
    trigger_deadline_seconds: Annotated[int, Field(ge=1)]
    evidence_refs: Evidence

    @model_validator(mode='after')
    def complete_schedule(self):
        if len(set(self.event_types)) != len(self.event_types) or len(set(self.watch_config_ids)) != len(self.watch_config_ids):
            raise ValueError('触发事件和配置范围不能重复')
        periodic = 'dynamic.timer' in self.event_types
        if periodic != (self.interval_seconds is not None and self.starts_at is not None):
            raise ValueError('定时触发必须明确批准间隔和开始时间')
        if not periodic and (self.interval_seconds is not None or self.starts_at is not None):
            raise ValueError('事件触发不能附带未启用的定时参数')
        required = {self.policy_id, self.input_rule_id, self.window_policy_id, self.verification_policy_id}
        kinds = [item.kind for item in self.static_assessments]
        if len(set(kinds)) != len(kinds):
            raise ValueError('独立静态路径不能重复')
        required.update(value for item in self.static_assessments
            for value in (item.rule_id, item.window_policy_id, item.verification_policy_id))
        required.update(item.input_rule_id for item in self.static_assessments if item.input_rule_id is not None)
        if 'configuration.changed' in self.event_types and not required <= set(self.watch_config_ids):
            raise ValueError('配置变化范围必须包含全部直接评估配置')
        return self
