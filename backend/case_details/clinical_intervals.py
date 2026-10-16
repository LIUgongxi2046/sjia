"""Explicit, bounded whole-patient percentile intervals; not an approval authority.

No ORM, network, provider, or publication calls. Every clinical/statistical
choice comes from the versioned protocol. Engineering ceilings are not sample
size recommendations. Values are sufficient-statistic sums, never resampled
IDs passed through a set-based event deduplicator.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
from time import monotonic
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from case_details.clinical_metrics import ClinicalCapture, MeasurementPlan, EvaluationInputError, mode_metrics

Text = Annotated[str, Field(min_length=1, pattern=r'\S')]
Digest = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
Integer = Annotated[int, Field(strict=True, le=2**53-1)]
# Fixed software safety budgets, not hidden statistical choices.
MAX_RESAMPLES = 10000
MAX_CLUSTERS = 2000
MAX_DRAW_CELLS = 2000000
MAX_WORK = 4000000
MAX_SECONDS = 10
METRICS = {
    'sensitivity': ('ratio_of_sums', 'fraction', 'assessable_independently_positive_events'),
    'missed_event_fraction': ('one_minus_ratio_of_sums', 'fraction', 'assessable_independently_positive_events'),
    'ppv': ('ratio_of_sums', 'fraction', 'known_outcome_prompts_including_first_and_repeat'),
    'alerts_per_observation_person_hour': ('ratio_of_sums', 'prompts_per_person_hour', 'completed_period_hours_including_unknown_labels'),
    'alerts_per_negative_observation_person_hour': ('ratio_of_sums', 'prompts_per_person_hour', 'completed_negative_period_hours'),
    'event_unassessable_fraction': ('ratio_of_sums', 'fraction', 'all_independently_positive_events'),
    'first_alert_difference_mean_seconds': ('pooled_detected_event_mean', 'seconds', 'detected_events_only'),
    'first_alert_difference_median_seconds': ('pooled_detected_event_median', 'seconds', 'detected_events_only'),
}


class StrictContract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Subgroup(StrictContract):
    dimension: Text
    value: Text


class Scope(StrictContract):
    partition: Literal['development', 'validation']
    subgroup: Subgroup | None


class MetricSpec(StrictContract):
    spec_id: Text
    metric_id: Literal[tuple(METRICS)]
    mode: Literal['static', 'dynamic']
    scope: Scope
    aggregation: Text
    unit: Text
    denominator_policy: Text
    required: StrictBool

    @model_validator(mode='after')
    def registered_statistic(self):
        if (self.aggregation, self.unit, self.denominator_policy) != METRICS[self.metric_id]:
            raise ValueError('UNSUPPORTED_METRIC_CONTRACT')
        return self


class IntervalProtocol(StrictContract):
    schema_version: Literal['clinical-interval-protocol-1']
    protocol_ref: Text
    measurement_plan_ref: Text
    method: Literal['patient_cluster_percentile_v1']
    algorithm_version: Literal['1']
    confidence_level: Annotated[float, Field(strict=True, gt=0, lt=1, allow_inf_nan=False)]
    alternative: Literal['two_sided']
    quantile_method: Literal['linear']
    cluster_key: Literal['patient_id']
    sampling: Literal['whole_cluster_with_replacement']
    cluster_frame: Literal['all_frozen_patients_in_selected_partition']
    partition_policy: Literal['separate_only']
    n_resamples: Annotated[Integer, Field(ge=2)]
    rng_algorithm: Literal['PCG64']
    seed: Annotated[Integer, Field(ge=0)]
    patient_order: Literal['patient_id_lexicographic']
    numpy_version: Text
    implementation_sha256: Digest
    invalid_resample_policy: Literal['fail_interval']
    degenerate_policy: Literal['not_computed']
    minimum_clusters: Annotated[Integer, Field(ge=2)]
    minimum_contributing_clusters: Annotated[Integer, Field(ge=2)]
    coverage: Literal['pointwise']
    multiplicity_adjustment: Literal['none']
    metric_specs: list[MetricSpec] = Field(min_length=1, max_length=64)

    @model_validator(mode='after')
    def unique_specs(self):
        if len({spec.spec_id for spec in self.metric_specs}) != len(self.metric_specs):
            raise ValueError('DUPLICATE_INTERVAL_SPEC')
        return self


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def canonical(value):
    """Canonical identity for already validated, order-independent capture rows."""
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return sorted([canonical(item) for item in value], key=lambda item: json.dumps(item, sort_keys=True))
    return value


def interval_engine():
    root = Path(__file__).parent
    manifest = {name: sha256((root / name).read_bytes()).hexdigest() for name in
        ('clinical_intervals.py', 'clinical_metrics.py', 'clinical_metrics_v2.py')}
    return {'method': 'patient_cluster_percentile_v1', 'algorithm_version': '1', 'numpy_version': np.__version__,
        'implementation_sha256': digest(manifest), 'rng_algorithm': 'PCG64',
        'resource_limits': {'max_resamples': MAX_RESAMPLES, 'max_clusters': MAX_CLUSTERS,
            'max_draw_cells': MAX_DRAW_CELLS, 'max_work': MAX_WORK, 'max_seconds': MAX_SECONDS}}


def parse_protocol(payload, plan):
    if payload is None:
        return None, 'NOT_CONFIGURED'
    if isinstance(payload, dict):
        for key, expected in (('protocol_ref', plan.confidence_interval_protocol_ref), ('measurement_plan_ref', plan.plan_ref)):
            if key in payload and payload[key] != expected:
                raise EvaluationInputError('INTERVAL_PROTOCOL_BINDING_MISMATCH')
    if not isinstance(payload, dict) or payload.get('schema_version') != 'clinical-interval-protocol-1':
        return None, 'UNSUPPORTED_PROTOCOL'
    if payload.get('method') != 'patient_cluster_percentile_v1':
        return None, 'UNSUPPORTED_METHOD'
    # Explicit unsupported versions are different from missing/invalid decisions.
    if payload.get('algorithm_version') not in (None, '1'):
        return None, 'UNSUPPORTED_PROTOCOL'
    protocol = IntervalProtocol.model_validate(payload)
    if protocol.protocol_ref != plan.confidence_interval_protocol_ref or protocol.measurement_plan_ref != plan.plan_ref:
        raise EvaluationInputError('INTERVAL_PROTOCOL_BINDING_MISMATCH')
    engine = interval_engine()
    if protocol.implementation_sha256 != engine['implementation_sha256']:
        raise EvaluationInputError('INTERVAL_IMPLEMENTATION_MISMATCH')
    if protocol.numpy_version != engine['numpy_version']:
        return protocol, 'UNSUPPORTED_PROTOCOL'
    if any(spec.scope.subgroup and spec.scope.subgroup.dimension not in plan.subgroup_dimensions for spec in protocol.metric_specs):
        raise EvaluationInputError('INTERVAL_SUBGROUP_NOT_FROZEN')
    return protocol, None


@dataclass(frozen=True)
class Contribution:
    numerator: float
    denominator: float
    differences: tuple = ()
    detected: int = 0
    missed: int = 0
    unknown: int = 0
    unassessable: int = 0


def contributions(capture, spec, frame):
    subgroup = spec.scope.subgroup
    periods = {patient: [] for patient in frame}
    events = {patient: [] for patient in frame}
    for period in sorted(capture.periods, key=lambda item: item.id):
        if period.patient_id in periods and (subgroup is None or (period.subgroups[subgroup.dimension] or 'unknown') == subgroup.value):
            periods[period.patient_id].append(period)
    period_ids = {period.id for values in periods.values() for period in values}
    for event in sorted(capture.events, key=lambda item: item.id):
        if event.period_id in period_ids:
            events[event.patient_id].append(event)
    rows = []
    for patient in frame:
        summary = mode_metrics(periods[patient], events[patient], spec.mode)
        values = tuple(summary['first_alert_difference_seconds_by_event'].values())
        metric = spec.metric_id
        if metric in ('sensitivity', 'missed_event_fraction'):
            numerator, denominator = summary['detected_events'], summary['assessable_positive_events']
        elif metric == 'ppv':
            numerator, denominator = summary['positive_prompts'], summary['known_outcome_prompts']
        elif metric == 'alerts_per_observation_person_hour':
            numerator, denominator = summary['prompt_counts']['total'], float(summary['assessable_observation_person_hours'])
        elif metric == 'alerts_per_negative_observation_person_hour':
            numerator = sum(len(getattr(period, spec.mode).prompts) for period in periods[patient]
                if period.label_state == 'negative' and getattr(period, spec.mode).status == 'completed')
            denominator = float(summary['negative_observation_person_hours'])
        elif metric == 'event_unassessable_fraction':
            numerator = len(summary['unassessable_positive_event_ids'])
            denominator = numerator + summary['assessable_positive_events']
        else:
            numerator, denominator = math.fsum(values), len(values)
        rows.append(Contribution(numerator, denominator, values, summary['detected_events'],
            len(summary['missed_event_ids']), len(summary['unknown_event_ids']), len(summary['unassessable_positive_event_ids'])))
    return rows


def statistic(rows, indices, spec):
    numerator = math.fsum(rows[int(index)].numerator for index in indices)
    denominator = math.fsum(rows[int(index)].denominator for index in indices)
    if denominator == 0:
        return None, numerator, denominator
    if spec.aggregation == 'pooled_detected_event_median':
        value = median([value for index in indices for value in rows[int(index)].differences])
    else:
        value = numerator / denominator
        if spec.aggregation == 'one_minus_ratio_of_sums':
            value = 1 - value
    return value, numerator, denominator


def interval_from_draws(rows, draws, spec, protocol, *, deadline=None):
    estimate, numerator, denominator = statistic(rows, range(len(rows)), spec)
    if spec.metric_id == 'missed_event_fraction':
        numerator = denominator - numerator
    result = {**spec.model_dump(mode='json'), 'estimate': estimate, 'lower': None, 'upper': None,
        'confidence_level': protocol.confidence_level, 'method': protocol.method,
        'status': 'CALCULATED', 'reason_codes': [], 'numerator': numerator, 'denominator': denominator,
        'clusters': len(rows), 'contributing_clusters': sum(row.denominator > 0 for row in rows),
        'draws_requested': len(draws), 'draws_valid': 0, 'draws_invalid': 0,
        'draw_sequence_sha256': None, 'diagnostics': {'detected_events': sum(row.detected for row in rows),
            'missed_events': sum(row.missed for row in rows), 'unknown_events': sum(row.unknown for row in rows),
            'unassessable_positive_events': sum(row.unassessable for row in rows)}}
    def failed(status):
        result.update(status=status, reason_codes=[status], lower=None, upper=None)
        return result
    if not all(value is None or math.isfinite(value) for value in (estimate, numerator, denominator)):
        result.update(estimate=None, numerator=None, denominator=None)
        return failed('NUMERIC_FAILURE')
    if denominator == 0:
        return failed('ZERO_DENOMINATOR')
    if len(rows) < protocol.minimum_clusters or result['contributing_clusters'] < protocol.minimum_contributing_clusters:
        return failed('INSUFFICIENT_CLUSTERS')
    distribution = []
    numeric_invalid = False
    for draw in draws:
        if deadline is not None and monotonic() >= deadline:
            return failed('RESOURCE_LIMIT')
        value, _, _ = statistic(rows, draw, spec)
        if value is None or not math.isfinite(value):
            result['draws_invalid'] += 1
            numeric_invalid = numeric_invalid or value is not None
        else:
            result['draws_valid'] += 1
            distribution.append(value)
    if result['draws_invalid']:
        return failed('NUMERIC_FAILURE' if numeric_invalid else 'INVALID_RESAMPLE')
    if not distribution or min(distribution) == max(distribution):
        return failed('DEGENERATE_DISTRIBUTION')
    lower, upper = np.quantile(distribution, [(1-protocol.confidence_level)/2, (1+protocol.confidence_level)/2], method='linear')
    if not math.isfinite(lower) or not math.isfinite(upper):
        return failed('NUMERIC_FAILURE')
    result.update(lower=float(lower), upper=float(upper))
    return result


def normalized_inputs(plan_payload, capture_payload):
    if capture_payload.get('schema_version') == 'clinical-result-capture-2':
        from case_details.clinical_metrics_v2 import MeasurementPlanV2, ClinicalCaptureV2, validate_v2
        plan, capture = MeasurementPlanV2.model_validate(plan_payload), ClinicalCaptureV2.model_validate(capture_payload)
        periods, _, _ = validate_v2(plan, capture)
        normalized = capture.model_dump(mode='json', exclude={'periods'})
        normalized.update(schema_version='clinical-result-capture-1', periods=periods,
            published_count=0, notified_count=0, current_cycle_mutations=0)
        return plan, ClinicalCapture.model_validate(normalized)
    from case_details.clinical_metrics import validate_capture
    plan, capture = MeasurementPlan.model_validate(plan_payload), ClinicalCapture.model_validate(capture_payload)
    validate_capture(plan, capture)
    return plan, capture


def evaluate_intervals(plan_payload, capture_payload, protocol_payload, *, protocol_body_sha256=None, compute=True):
    plan, capture = normalized_inputs(plan_payload, capture_payload)
    protocol, failure = parse_protocol(protocol_payload, plan)
    declared_binding = plan.confidence_interval_binding
    if (declared_binding is not None and protocol_payload is not None
            and declared_binding.protocol_sha256 != (protocol_body_sha256 or digest(protocol_payload))):
        raise EvaluationInputError('INTERVAL_FROZEN_BODY_MISMATCH')
    section = {'schema_version': 'clinical-interval-results-1', 'status': failure or 'PENDING',
        'required_complete': False, 'reason_codes': [failure] if failure else [],
        'protocol_ref': plan.confidence_interval_protocol_ref,
        'protocol_sha256': protocol_body_sha256 or (digest(protocol_payload) if protocol_payload is not None else None),
        'input_sha256': digest(canonical({'plan': plan_payload, 'capture': capture_payload})),
        'engine': interval_engine(), 'protocol': protocol_payload,
        'unlisted_metrics_status': 'NOT_REQUESTED', 'results': []}
    if protocol is None:
        return section
    frames = {partition: sorted(item.patient_id for item in capture.patients if item.partition == partition)
        for partition in {spec.scope.partition for spec in protocol.metric_specs}}
    work = protocol.n_resamples * sum(len(frames[spec.scope.partition]) for spec in protocol.metric_specs)
    # Median retains every sampled difference, so the budget also bounds that work.
    if (protocol.n_resamples > MAX_RESAMPLES or any(len(frame) > MAX_CLUSTERS for frame in frames.values())
            or protocol.n_resamples * sum(map(len, frames.values())) > MAX_DRAW_CELLS or work > MAX_WORK
            or any(spec.aggregation == 'pooled_detected_event_median' for spec in protocol.metric_specs)
               and protocol.n_resamples * len(capture.events) * max(map(len, frames.values()), default=0) > MAX_WORK):
        failure = 'RESOURCE_LIMIT'
    deadline, draws_by_partition = monotonic() + MAX_SECONDS, {}
    if compute and not failure:
        for partition, frame in sorted(frames.items()):
            generator = np.random.Generator(np.random.PCG64(protocol.seed))
            draws = generator.integers(0, max(1,len(frame)), size=(protocol.n_resamples, len(frame)), dtype=np.int64)
            draws_by_partition[partition] = (draws, digest({'frame':frame,
                'indices_sha256':sha256(draws.astype('<i8', copy=False).tobytes()).hexdigest()}))
    for spec in protocol.metric_specs:
        frame = frames[spec.scope.partition]
        rows = contributions(capture, spec, frame)
        if failure or not compute:
            item = interval_from_draws(rows, [], spec, protocol)
            item.update(status=failure or 'PENDING', reason_codes=[failure or 'PENDING'],
                draws_requested=protocol.n_resamples, lower=None, upper=None)
        else:
            draws, draws_digest = draws_by_partition[spec.scope.partition]
            item = interval_from_draws(rows, draws, spec, protocol, deadline=deadline)
            item['draw_sequence_sha256'] = draws_digest
        section['results'].append(item)
    if failure:
        section.update(status=failure, reason_codes=[failure])
    elif compute:
        section['required_complete'] = all(item['status'] == 'CALCULATED' for item in section['results'] if item['required'])
        section['status'] = 'COMPLETE' if all(item['status']=='CALCULATED' for item in section['results']) else 'INCOMPLETE'
        section['reason_codes'] = sorted({reason for item in section['results'] for reason in item['reason_codes']})
    return section


def add_interval_report(report, section):
    result = {**report, 'schema_version': 'clinical-metrics-report-3',
        'point_report_schema_version': report['schema_version'], 'confidence_intervals': section}
    result['limitations'] = [item for item in report['limitations'] if not item.startswith('Confidence intervals require')]
    result['limitations'].append('Only explicitly requested pointwise intervals are computed; no simultaneous coverage or clinical-method approval is established. Detected-event time differences are conditional on detection and are not care-seeking lead time.')
    return result


def interval_protocol_schema():
    schema = IntervalProtocol.model_json_schema()
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    schema['$defs']['MetricSpec']['allOf'] = [
        {'if': {'properties': {'metric_id': {'const': metric}}},
         'then': {'properties': {key: {'const': value} for key, value in
             zip(('aggregation', 'unit', 'denominator_policy'), contract)}}}
        for metric, contract in METRICS.items()]
    return schema
