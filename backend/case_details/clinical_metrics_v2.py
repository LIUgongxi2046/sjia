"""Explicit scheduled replay capture. No default clinical timing/threshold policy."""
from hashlib import sha256
import json
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from case_details.clinical_metrics import (Contract, Text, Digest, Prompt, State, Event,
    PatientPartition, IntervalEvidenceBinding, EvaluationInputError, _unique)


class StaticPolicyBindings(Contract):
    runtime_config_id: Text
    window_policy_id: Text
    verification_policy_id: Text


class DynamicPolicyBindings(StaticPolicyBindings):
    input_rule_id: Text


class ScheduledPoint(Contract):
    period_id: Text
    step_id: Text
    assessment_at: AwareDatetime
    known_at: AwareDatetime
    corpus_sha256: Digest
    static_rule_config_id: Text
    dynamic_rule_config_id: Text
    static_policies: StaticPolicyBindings
    dynamic_policies: DynamicPolicyBindings


class MeasurementPlanV2(Contract):
    schema_version: Literal['clinical-measurement-plan-2']
    plan_ref: Text
    matching_policy_ref: Text
    matching_mode: Literal['independent_explicit_pairings']
    confidence_interval_protocol_ref: Text | None
    confidence_interval_binding: IntervalEvidenceBinding | None = None
    subgroup_dimensions: list[Text]
    fixed_before: AwareDatetime
    criteria_refs: list[Text] = Field(min_length=1)
    static_criterion: Literal['classification_meets', 'ms_threshold_true']
    dynamic_criterion: Literal['decision_met']
    assessability: Literal['all_scheduled_steps_completed']
    occurrence_policy: Literal['first_positive_then_repeat']
    reset_policy: Literal['period_boundary_only']
    schedule: list[ScheduledPoint] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_schedule(self):
        if self.confidence_interval_binding is not None and self.confidence_interval_protocol_ref is None:
            raise ValueError('INTERVAL_REFERENCE_REQUIRED')
        keys = [(row.period_id, row.step_id) for row in self.schedule]
        if len(set(keys)) != len(keys) or len(set(self.subgroup_dimensions)) != len(self.subgroup_dimensions):
            raise ValueError('schedule identities and subgroup dimensions must be unique')
        return self


class CorpusFact(Contract):
    fact_ref: Text
    field_id: Text
    record_version_id: Text
    verification_id: Text | None
    reference_sha256: Digest
    source_ref: Text
    available_at: AwareDatetime
    verified_at: AwareDatetime | None


class ReplayExecution(Contract):
    execution_ref: Text
    task_id: Text
    rule_config_id: Text
    execution_input_sha256: Digest
    result_sha256: Digest
    status: Literal['completed', 'insufficient']
    used_fact_refs: list[Text]
    prompts: list[Prompt] = Field(max_length=1)


class PairedStep(Contract):
    id: Text
    assessment_at: AwareDatetime
    known_at: AwareDatetime
    corpus_sha256: Digest
    facts: list[CorpusFact]
    static: ReplayExecution
    dynamic: ReplayExecution


class PeriodV2(Contract):
    id: Text
    patient_id: Text
    label_id: Text
    label_state: State
    reference_answer_ref: Text | None
    start: AwareDatetime
    end: AwareDatetime
    subgroups: dict[str, str | None]
    steps: list[PairedStep] = Field(min_length=1)


class ClinicalCaptureV2(Contract):
    schema_version: Literal['clinical-result-capture-2']
    data_mode: Literal['synthetic', 'clinical']
    mode: Literal['replay']
    dataset_version_id: Text
    dataset_checksum: Digest
    implementation_sha256: Digest
    configuration_checksums: dict[str, Digest]
    measurement_plan_ref: Text
    captured_at: AwareDatetime
    patients: list[PatientPartition] = Field(min_length=1)
    periods: list[PeriodV2] = Field(min_length=1)
    events: list[Event]


def corpus_digest(capture, period, step):
    """Same candidate universe; consumed sets and task inputs may differ."""
    value = {'dataset_version_id': capture.dataset_version_id, 'dataset_checksum': capture.dataset_checksum,
        'patient_id': period.patient_id, 'period_id': period.id, 'step_id': step.id,
        'assessment_at': step.assessment_at.isoformat(), 'known_at': step.known_at.isoformat(),
        'facts': [row.model_dump(mode='json') for row in sorted(step.facts, key=lambda row: row.fact_ref)]}
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def validate_v2(plan, capture):
    if capture.measurement_plan_ref != plan.plan_ref or capture.captured_at < plan.fixed_before:
        raise EvaluationInputError('MEASUREMENT_PLAN_BINDING_INVALID')
    _unique(capture.periods, 'id', 'PERIOD_DUPLICATE')
    planned = {(row.period_id, row.step_id): row for row in plan.schedule}
    actual = {(period.id, step.id) for period in capture.periods for step in period.steps}
    if actual != set(planned):
        raise EvaluationInputError('REPLAY_SCHEDULE_MISMATCH')
    task_ids, excluded, normalized, execution_rows = set(), 0, [], []
    all_prompt_rows = []
    for period in capture.periods:
        _unique(period.steps, 'id', 'STEP_DUPLICATE')
        previous = None
        prompts = {'static': [], 'dynamic': []}
        statuses = {'static': [], 'dynamic': []}
        for step in period.steps:
            point = planned[period.id, step.id]
            if (step.assessment_at != point.assessment_at or step.known_at != point.known_at
                    or not period.start <= step.assessment_at < period.end or step.assessment_at > step.known_at
                    or step.known_at > capture.captured_at or previous is not None and step.assessment_at <= previous):
                raise EvaluationInputError('REPLAY_STEP_TIME_INVALID')
            previous = step.assessment_at
            if corpus_digest(capture, period, step) != step.corpus_sha256 or step.corpus_sha256 != point.corpus_sha256:
                raise EvaluationInputError('REPLAY_CORPUS_DIGEST_MISMATCH')
            policy_ids = {*point.static_policies.model_dump().values(), *point.dynamic_policies.model_dump().values()}
            if not policy_ids <= set(capture.configuration_checksums):
                raise EvaluationInputError('POLICY_NOT_IN_FROZEN_CONFIGURATIONS')
            facts = _unique(step.facts, 'fact_ref', 'FACT_REFERENCE_DUPLICATE')
            for mode in ('static', 'dynamic'):
                result = getattr(step, mode)
                if result.rule_config_id != getattr(point, mode + '_rule_config_id'):
                    raise EvaluationInputError('REPLAY_PREDECLARED_RULE_MISMATCH')
                if result.task_id in task_ids:
                    raise EvaluationInputError('REPLAY_TASK_REUSED')
                task_ids.add(result.task_id)
                if result.rule_config_id not in capture.configuration_checksums:
                    raise EvaluationInputError('RULE_NOT_IN_FROZEN_CONFIGURATIONS')
                if len(set(result.used_fact_refs)) != len(result.used_fact_refs) or not set(result.used_fact_refs) <= set(facts):
                    raise EvaluationInputError('REPLAY_USED_FACT_SCOPE_INVALID')
                for identity in result.used_fact_refs:
                    fact = facts[identity]
                    if fact.available_at > step.known_at or fact.verified_at is None or fact.verified_at > step.known_at:
                        raise EvaluationInputError('FUTURE_OR_UNVERIFIED_FACT_USED')
                if result.status == 'insufficient' and result.prompts:
                    raise EvaluationInputError('INSUFFICIENT_RESULT_HAS_PROMPTS')
                for prompt in result.prompts:
                    expected = 'repeat' if prompts[mode] else 'first'
                    if prompt.at != step.assessment_at or prompt.occurrence != expected:
                        raise EvaluationInputError('REPLAY_PROMPT_OCCURRENCE_INVALID')
                    if any(facts[key].available_at > prompt.at or facts[key].verified_at > prompt.at for key in result.used_fact_refs):
                        raise EvaluationInputError('PROMPT_USES_FUTURE_FACT')
                    prompts[mode].append(prompt.model_dump(mode='json'))
                statuses[mode].append(result.status)
                execution_rows.append({'period_id': period.id, 'step_id': step.id, 'mode': mode,
                    'task_id': result.task_id, 'execution_input_sha256': result.execution_input_sha256,
                    'result_sha256': result.result_sha256, 'corpus_sha256': step.corpus_sha256, 'status': result.status})
            excluded += sum(fact.available_at > step.known_at or fact.verified_at is None or fact.verified_at > step.known_at
                for fact in facts.values())
        row = period.model_dump(mode='json', exclude={'steps'})
        row.update(known_at=max(step.known_at for step in period.steps).isoformat(), facts=[])
        for mode in ('static', 'dynamic'):
            first = getattr(period.steps[0], mode)
            status = 'completed' if all(item == 'completed' for item in statuses[mode]) else 'insufficient'
            row[mode] = {'execution_ref': first.execution_ref, 'task_id': first.task_id,
                'rule_config_id': first.rule_config_id, 'input_sha256': period.steps[0].corpus_sha256,
                'status': status, 'prompts': prompts[mode] if status == 'completed' else []}
        normalized.append(row)
        all_prompt_rows.append({**row, **{mode: {**row[mode], 'status': 'completed', 'prompts': prompts[mode]}
            for mode in ('static', 'dynamic')}})
    # Validate every observed prompt, even if a later missing step makes its
    # period unassessable. Denominator exclusion must never hide bad pairings.
    from case_details.clinical_metrics import MeasurementPlan, ClinicalCapture, validate_capture
    legacy_plan = plan.model_dump(mode='json', include={'plan_ref', 'matching_policy_ref', 'matching_mode',
        'confidence_interval_protocol_ref', 'subgroup_dimensions', 'fixed_before', 'criteria_refs'})
    legacy_plan['schema_version'] = 'clinical-measurement-plan-1'
    legacy_capture = capture.model_dump(mode='json', exclude={'periods'})
    legacy_capture.update(schema_version='clinical-result-capture-1', periods=all_prompt_rows,
        published_count=0, notified_count=0, current_cycle_mutations=0)
    validate_capture(MeasurementPlan.model_validate(legacy_plan), ClinicalCapture.model_validate(legacy_capture))
    return normalized, excluded, execution_rows


def evaluate_v2(plan_payload, capture_payload, *, expected_binding=None):
    from case_details.clinical_metrics import _evaluate_v1
    plan = MeasurementPlanV2.model_validate(plan_payload)
    capture = ClinicalCaptureV2.model_validate(capture_payload)
    if expected_binding is not None and any(getattr(capture, key) != value for key, value in expected_binding.items()):
        raise EvaluationInputError('FROZEN_INPUT_BINDING_MISMATCH')
    periods, excluded, executions = validate_v2(plan, capture)
    legacy_plan = plan.model_dump(mode='json', include={'plan_ref', 'matching_policy_ref', 'matching_mode',
        'confidence_interval_protocol_ref', 'subgroup_dimensions', 'fixed_before', 'criteria_refs'})
    legacy_plan['schema_version'] = 'clinical-measurement-plan-1'
    legacy_capture = capture.model_dump(mode='json', exclude={'periods'})
    legacy_capture.update(schema_version='clinical-result-capture-1', periods=periods,
        published_count=0, notified_count=0, current_cycle_mutations=0)
    result = _evaluate_v1(legacy_plan, legacy_capture)
    result.update(schema_version='clinical-metrics-report-2', execution_steps=executions,
        future_or_unverified_facts_excluded=excluded,
        sequence_policy={'assessability': plan.assessability, 'occurrence_policy': plan.occurrence_policy,
            'reset_policy': plan.reset_policy, 'static_criterion': plan.static_criterion,
            'dynamic_criterion': plan.dynamic_criterion})
    result['limitations'].append('A period is unassessable unless every independently scheduled step completed; partial positive prompts are not counted as negative evidence.')
    return result
