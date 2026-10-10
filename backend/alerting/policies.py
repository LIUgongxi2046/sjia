from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from assessment.policies import ContextVariable, Evidence, PolicyModel, StaticVariable, Text


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
    variables: list[StaticVariable | ContextVariable]
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
    kind: Literal['classification', 'ms']
    rule_id: UUID
    window_policy_id: UUID
    verification_policy_id: UUID


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
        if 'configuration.changed' in self.event_types and not required <= set(self.watch_config_ids):
            raise ValueError('配置变化范围必须包含全部直接评估配置')
        return self
