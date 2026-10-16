"""Portable deterministic evaluation, never a clinical approval authority.

Input event windows/pairings/labels are independent evidence. This module does
not generate Gold, choose medical windows, call providers, or publish alerts.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
State = Literal['positive', 'negative', 'unknown']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class IntervalEvidenceBinding(Contract):
    """Immutable CI/approval identities contained in the pre-frozen plan bytes."""
    protocol_sha256: Digest
    approval_evidence_ref: Text
    approval_evidence_sha256: Digest
    approval_audit_id: UUID
    purpose: Literal['clinical_evaluation']


class MeasurementPlan(Contract):
    schema_version: Literal['clinical-measurement-plan-1']
    plan_ref: Text
    matching_policy_ref: Text
    matching_mode: Literal['independent_explicit_pairings']
    confidence_interval_protocol_ref: Text | None
    confidence_interval_binding: IntervalEvidenceBinding | None = None
    subgroup_dimensions: list[Text]
    fixed_before: AwareDatetime
    criteria_refs: list[Text] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_dimensions(self):
        if self.confidence_interval_binding is not None and self.confidence_interval_protocol_ref is None:
            raise ValueError('INTERVAL_REFERENCE_REQUIRED')
        if len(set(self.subgroup_dimensions)) != len(self.subgroup_dimensions):
            raise ValueError('subgroup dimensions must be unique')
        return self


class Fact(Contract):
    fact_ref: Text
    source_ref: Text
    available_at: AwareDatetime
    verified_at: AwareDatetime | None
    used: StrictBool


class Prompt(Contract):
    id: Text
    at: AwareDatetime
    outcome: State
    outcome_ref: Text
    matched_event_id: Text | None
    occurrence: Literal['first', 'repeat']


class ReplayResult(Contract):
    execution_ref: Text
    task_id: Text
    rule_config_id: Text
    input_sha256: Digest
    status: Literal['completed', 'insufficient']
    prompts: list[Prompt]


class Period(Contract):
    id: Text
    patient_id: Text
    label_id: Text
    label_state: State
    reference_answer_ref: Text | None
    start: AwareDatetime
    end: AwareDatetime
    known_at: AwareDatetime
    subgroups: dict[str, str | None]
    facts: list[Fact]
    static: ReplayResult
    dynamic: ReplayResult


class Event(Contract):
    id: Text
    patient_id: Text
    period_id: Text
    label_state: Literal['positive', 'unknown']
    reference_answer_ref: Text | None
    reference_at: AwareDatetime | None
    valid_alert_start: AwareDatetime
    valid_alert_end: AwareDatetime


class PatientPartition(Contract):
    patient_id: Text
    partition: Literal['development', 'validation']


class ClinicalCapture(Contract):
    schema_version: Literal['clinical-result-capture-1']
    data_mode: Literal['synthetic', 'clinical']
    mode: Literal['replay']
    dataset_version_id: Text
    dataset_checksum: Digest
    implementation_sha256: Digest
    configuration_checksums: dict[str, Digest]
    measurement_plan_ref: Text
    captured_at: AwareDatetime
    published_count: Literal[0]
    notified_count: Literal[0]
    current_cycle_mutations: Literal[0]
    patients: list[PatientPartition] = Field(min_length=1)
    periods: list[Period] = Field(min_length=1)
    events: list[Event]

    @field_validator('published_count', 'notified_count', 'current_cycle_mutations', mode='before')
    @classmethod
    def exact_zero_counts(cls, value):
        if type(value) is not int or value != 0:
            raise ValueError('replay side-effect counts must be explicit integer zero')
        return value


class EvaluationInputError(ValueError):
    pass


def _unique(rows, key, reason):
    identifiers = [getattr(row, key) for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise EvaluationInputError(reason)
    return dict(zip(identifiers, rows))


def validate_capture(plan, capture, *, expected_binding=None):
    if capture.measurement_plan_ref != plan.plan_ref or capture.captured_at < plan.fixed_before:
        raise EvaluationInputError('MEASUREMENT_PLAN_BINDING_INVALID')
    if not capture.configuration_checksums:
        raise EvaluationInputError('FIXED_CONFIGURATIONS_REQUIRED')
    if expected_binding is not None:
        actual = {name: getattr(capture, name) for name in expected_binding}
        if actual != expected_binding:
            raise EvaluationInputError('FROZEN_INPUT_BINDING_MISMATCH')
    patients = _unique(capture.patients, 'patient_id', 'PATIENT_PARTITION_DUPLICATE')
    periods = _unique(capture.periods, 'id', 'PERIOD_DUPLICATE')
    events = _unique(capture.events, 'id', 'EVENT_DUPLICATE')
    labels, intervals, future_excluded, task_ids = set(), {}, 0, set()
    prompt_ids = {'static': set(), 'dynamic': set()}
    for period in periods.values():
        if (period.patient_id not in patients or period.start >= period.end
                or period.end > capture.captured_at or period.known_at > capture.captured_at):
            raise EvaluationInputError('OBSERVATION_SCOPE_INVALID')
        if period.label_id in labels:
            raise EvaluationInputError('LABEL_DUPLICATE')
        labels.add(period.label_id)
        if period.label_state != 'unknown' and not period.reference_answer_ref:
            raise EvaluationInputError('INDEPENDENT_LABEL_REQUIRED')
        if set(period.subgroups) != set(plan.subgroup_dimensions):
            raise EvaluationInputError('SUBGROUP_SCOPE_INVALID')
        intervals.setdefault(period.patient_id, []).append((period.start, period.end))
        if period.static.input_sha256 != period.dynamic.input_sha256:
            raise EvaluationInputError('STATIC_DYNAMIC_INPUT_MISMATCH')
        _unique(period.facts, 'fact_ref', 'FACT_REFERENCE_DUPLICATE')
        for fact in period.facts:
            eligible = fact.available_at <= period.known_at and fact.verified_at is not None and fact.verified_at <= period.known_at
            if fact.used and not eligible:
                raise EvaluationInputError('FUTURE_OR_UNVERIFIED_FACT_USED')
            if not fact.used and not eligible:
                future_excluded += 1
        for mode in ('static', 'dynamic'):
            result = getattr(period, mode)
            if result.task_id in task_ids:
                raise EvaluationInputError('REPLAY_TASK_REUSED')
            task_ids.add(result.task_id)
            if result.rule_config_id not in capture.configuration_checksums:
                raise EvaluationInputError('RULE_NOT_IN_FROZEN_CONFIGURATIONS')
            if result.status == 'insufficient' and result.prompts:
                raise EvaluationInputError('INSUFFICIENT_RESULT_HAS_PROMPTS')
            for prompt in result.prompts:
                if prompt.id in prompt_ids[mode]:
                    raise EvaluationInputError('PROMPT_DUPLICATE')
                prompt_ids[mode].add(prompt.id)
                if not period.start <= prompt.at < period.end or prompt.at > period.known_at:
                    raise EvaluationInputError('PROMPT_OUTSIDE_OBSERVATION')
                # Contract v1 describes one fixed fact set for the period. It
                # cannot attribute an earlier alert to facts acquired later.
                # Variable per-alert snapshots require a future capture version,
                # not silent use of the period's final known_at as every cutoff.
                if any(fact.used and (fact.available_at > prompt.at
                        or fact.verified_at is None or fact.verified_at > prompt.at) for fact in period.facts):
                    raise EvaluationInputError('PROMPT_USES_FUTURE_FACT')
                event = events.get(prompt.matched_event_id)
                if prompt.matched_event_id is not None:
                    if event is None or event.patient_id != period.patient_id or event.period_id != period.id:
                        raise EvaluationInputError('PROMPT_EVENT_SCOPE_INVALID')
                    if not event.valid_alert_start <= prompt.at <= event.valid_alert_end:
                        raise EvaluationInputError('PROMPT_OUTSIDE_APPROVED_MATCH_WINDOW')
                if prompt.outcome == 'positive' and (event is None or event.label_state != 'positive'):
                    raise EvaluationInputError('POSITIVE_PROMPT_INDEPENDENT_EVENT_REQUIRED')
                if prompt.outcome == 'unknown' and event is not None and event.label_state == 'positive':
                    raise EvaluationInputError('PROMPT_EVENT_OUTCOME_CONFLICT')
                if prompt.outcome == 'negative' and event is not None:
                    raise EvaluationInputError('NEGATIVE_PROMPT_HAS_MATCH')
    for values in intervals.values():
        previous = None
        for start, end in sorted(values):
            if previous is not None and start < previous:
                raise EvaluationInputError('OBSERVATION_PERIODS_OVERLAP')
            previous = end
    for event in events.values():
        period = periods.get(event.period_id)
        if (period is None or event.patient_id != period.patient_id
                or event.valid_alert_start > event.valid_alert_end
                or not period.start <= event.valid_alert_start <= event.valid_alert_end < period.end):
            raise EvaluationInputError('EVENT_OBSERVATION_SCOPE_INVALID')
        if event.label_state == 'positive' and (not event.reference_answer_ref or event.reference_at is None):
            raise EvaluationInputError('INDEPENDENT_EVENT_REFERENCE_REQUIRED')
        if event.label_state == 'positive' and period.label_state != 'positive':
            raise EvaluationInputError('EVENT_PERIOD_LABEL_CONFLICT')
        if event.reference_at is not None and not period.start <= event.reference_at < period.end:
            raise EvaluationInputError('EVENT_REFERENCE_TIME_OUTSIDE_OBSERVATION')
    return future_excluded


def _ratio(numerator, denominator):
    return float(Decimal(numerator) / Decimal(denominator)) if denominator else None


def _hours(periods):
    seconds = sum((Decimal(str((period.end - period.start).total_seconds())) for period in periods), Decimal(0))
    return seconds / Decimal(3600)


def mode_metrics(periods, events, mode):
    completed = [period for period in periods if getattr(period, mode).status == 'completed']
    complete_ids = {period.id for period in completed}
    positive = [event for event in events if event.label_state == 'positive' and event.period_id in complete_ids]
    unassessable = [event for event in events if event.label_state == 'positive' and event.period_id not in complete_ids]
    unknown = [event for event in events if event.label_state == 'unknown']
    prompts = [prompt for period in completed for prompt in getattr(period, mode).prompts]
    confirmed = [prompt for prompt in prompts if prompt.outcome != 'unknown']
    positive_prompts = [prompt for prompt in prompts if prompt.outcome == 'positive']
    detected, differences = set(), {}
    for event in positive:
        matches = [prompt for prompt in positive_prompts if prompt.matched_event_id == event.id]
        if matches:
            detected.add(event.id)
            differences[event.id] = (min(prompt.at for prompt in matches) - event.reference_at).total_seconds()
    negative_periods = [period for period in completed if period.label_state == 'negative']
    negative_prompts = sum(len(getattr(period, mode).prompts) for period in negative_periods)
    hours, negative_hours = _hours(completed), _hours(negative_periods)
    return {'sensitivity': _ratio(len(detected), len(positive)),
        'detected_events': len(detected), 'assessable_positive_events': len(positive),
        'missed_event_ids': sorted(event.id for event in positive if event.id not in detected),
        'unassessable_positive_event_ids': sorted(event.id for event in unassessable),
        'unknown_event_ids': sorted(event.id for event in unknown),
        'event_unassessable_fraction': _ratio(len(unassessable), len(positive) + len(unassessable)),
        'ppv': _ratio(len(positive_prompts), len(confirmed)),
        'positive_prompts': len(positive_prompts), 'known_outcome_prompts': len(confirmed),
        'unknown_outcome_prompts': len(prompts) - len(confirmed),
        'prompt_counts': {'total': len(prompts), 'first': sum(item.occurrence == 'first' for item in prompts),
            'repeat': sum(item.occurrence == 'repeat' for item in prompts)},
        'assessable_observation_person_hours': str(hours),
        'all_observation_person_hours': str(_hours(periods)),
        'alerts_per_observation_person_hour': _ratio(len(prompts), hours),
        'negative_observation_person_hours': str(negative_hours),
        'alerts_per_negative_observation_person_hour': _ratio(negative_prompts, negative_hours),
        'first_alert_difference_seconds_by_event': differences,
        'confidence_interval': None,
        'confidence_interval_status': 'NOT_MEASURED_REQUIRES_APPROVED_PATIENT_CORRELATED_METHOD'}


def _evaluate_v1(plan_payload, capture_payload, *, expected_binding=None):
    plan = MeasurementPlan.model_validate(plan_payload)
    capture = ClinicalCapture.model_validate(capture_payload)
    excluded = validate_capture(plan, capture, expected_binding=expected_binding)
    modes = {mode: mode_metrics(capture.periods, capture.events, mode) for mode in ('static', 'dynamic')}
    partitions = {}
    for partition in ('development', 'validation'):
        patient_ids = {item.patient_id for item in capture.patients if item.partition == partition}
        periods = [period for period in capture.periods if period.patient_id in patient_ids]
        events = [event for event in capture.events if event.patient_id in patient_ids]
        partitions[partition] = {'patients': len(patient_ids), 'periods': len(periods),
            **{mode: mode_metrics(periods, events, mode) for mode in ('static', 'dynamic')}}
    subgroups = {}
    for dimension in plan.subgroup_dimensions:
        subgroups[dimension] = {}
        for value in sorted({period.subgroups[dimension] or 'unknown' for period in capture.periods}):
            periods = [period for period in capture.periods if (period.subgroups[dimension] or 'unknown') == value]
            ids = {period.id for period in periods}
            subgroups[dimension][value] = {'patients': len({period.patient_id for period in periods}),
                **{mode: mode_metrics(periods, [event for event in capture.events if event.period_id in ids], mode)
                    for mode in ('static', 'dynamic')}}
    return {'schema_version': 'clinical-metrics-report-1', 'data_mode': capture.data_mode,
        'status': 'CALCULATED_FROM_DECLARED_CAPTURE', 'clinical_validation': False,
        'evidence_qualification': 'NOT_VERIFIED_BY_PURE_EVALUATOR',
        'binding': {'dataset_version_id': capture.dataset_version_id, 'dataset_checksum': capture.dataset_checksum,
            'implementation_sha256': capture.implementation_sha256,
            'configuration_checksums': capture.configuration_checksums, 'measurement_plan_ref': plan.plan_ref},
        'denominators': {'patients': len(capture.patients), 'periods': len(capture.periods),
            'labels': dict(Counter(period.label_state for period in capture.periods)),
            'unknown_labels_excluded_from_negative_denominator': sum(period.label_state == 'unknown' for period in capture.periods)},
        'future_or_unverified_facts_excluded': excluded, **modes,
        'partitions': partitions, 'subgroups': subgroups,
        'confidence_interval_protocol_ref': plan.confidence_interval_protocol_ref,
        'limitations': ['Pure computation does not validate evidence approval, saved replay tasks or clinical performance.',
            'Confidence intervals require the independently approved patient-correlated method and are not calculated here.',
            'Unavailable or insufficient results are not negatives; unassessable denominators are reported separately.']}


_INTERVAL_UNSET = object()


def evaluate(plan_payload, capture_payload, *, expected_binding=None, interval_protocol=_INTERVAL_UNSET,
        interval_protocol_sha256=None):
    if capture_payload.get('schema_version') == 'clinical-result-capture-2':
        from case_details.clinical_metrics_v2 import evaluate_v2
        result = evaluate_v2(plan_payload, capture_payload, expected_binding=expected_binding)
    else:
        result = _evaluate_v1(plan_payload, capture_payload, expected_binding=expected_binding)
    if interval_protocol is not _INTERVAL_UNSET:
        from case_details.clinical_intervals import evaluate_intervals, add_interval_report
        result = add_interval_report(result, evaluate_intervals(plan_payload, capture_payload,
            interval_protocol, protocol_body_sha256=interval_protocol_sha256))
    return result
