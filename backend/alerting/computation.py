from decimal import Decimal, Inexact, Rounded, localcontext
from fractions import Fraction
from operator import ge, gt, le, lt

from assessment.qualified_facts import QualificationRejected
from assessment.selection import strictly_before


STATES = {'met', 'not_met', 'unknown'}
OPERATORS = {'gt': gt, 'gte': ge, 'lt': lt, 'lte': le}


def combination_result(group, signals, *, maximum_depth, maximum_operands):
    pending = [(group, (), 1, False)]
    results, used, consumed = {}, set(), 0
    while pending:
        item, path, depth, visited = pending.pop()
        if not visited:
            refs, children = item.get('signal_refs', []), item.get('groups', [])
            consumed += len(refs) + len(children)
            if (depth > maximum_depth or consumed > maximum_operands or not refs and not children
                    or len(set(refs)) != len(refs) or used & set(refs) or not set(refs) <= set(signals)):
                raise QualificationRejected('DYNAMIC_COMBINATION_INVALID')
            used.update(refs)
            pending.append((item, path, depth, True))
            pending.extend((child, path + (index,), depth + 1, False) for index, child in enumerate(children))
            continue
        values = [signals[ref]['state'] for ref in item.get('signal_refs', [])]
        values.extend(results[path + (index,)]['state'] for index in range(len(item.get('groups', []))))
        if not set(values) <= STATES:
            raise QualificationRejected('DYNAMIC_SIGNAL_STATE_INVALID')
        met, unknown = values.count('met'), values.count('unknown')
        operator = item['operator']
        if operator == 'all':
            state = 'not_met' if 'not_met' in values else 'unknown' if unknown else 'met'
        elif operator == 'any':
            state = 'met' if met else 'unknown' if unknown else 'not_met'
        elif operator == 'at_least':
            minimum = item['minimum_met']
            if not 1 <= minimum <= len(values):
                raise QualificationRejected('DYNAMIC_COMBINATION_INVALID')
            state = 'met' if met >= minimum else 'not_met' if met + unknown < minimum else 'unknown'
        else:
            raise QualificationRejected('DYNAMIC_COMBINATION_OPERATOR_INVALID')
        results[path] = {'path': list(path), 'operator': operator, 'state': state, 'operands': values,
            'signal_refs': item.get('signal_refs', []), 'minimum_met': item.get('minimum_met')}
    return {'state': results[()]['state'], 'groups': [results[key] for key in sorted(results)]}


def time_bounds(value):
    return {'precision': value.reference['medical_precision'], 'earliest': value.interval.lower.isoformat(),
        'latest_exclusive': None if value.interval.upper_inclusive else value.interval.upper.isoformat(),
        'instant': value.interval.lower.isoformat() if value.interval.lower == value.interval.upper else None}


def pair_result(baseline, current, condition, comparison, window, relations, *, position, maximum_digits):
    if any(not item.eligible or item.interval is None or not isinstance(item.value, Decimal)
            or not item.value.is_finite() or item.reference['qualifier'] != 'eq' for item in (baseline, current)):
        raise QualificationRejected('DYNAMIC_QUALIFIED_INPUT_INCOMPLETE')
    reasons = []
    smallest = (current.interval.lower - baseline.interval.upper).total_seconds()
    largest = (current.interval.upper - baseline.interval.lower).total_seconds()
    if (not strictly_before(baseline.interval, current.interval) or smallest < window['minimum_separation_seconds']
            or largest > condition['series']['max_gap_seconds']):
        reasons.append('DYNAMIC_TIME_ORDER_OR_GAP_UNRESOLVED')
    if not any(not row.reasons and row.relation_type == 'independent_sampling'
            and {row.left_id, row.right_id} == {baseline.reference['fact_version_id'], current.reference['fact_version_id']} for row in relations):
        reasons.append('DYNAMIC_INDEPENDENT_SAMPLING_REQUIRED')
    if not any(pair.baseline_method == baseline.reference['method'] and pair.current_method == current.reference['method'] for pair in comparison.methods):
        reasons.append('DYNAMIC_METHOD_NOT_COMPARABLE')
    if baseline.reference['normalized_unit'] != current.reference['normalized_unit']:
        reasons.append('DYNAMIC_UNIT_NOT_COMPARABLE')
    absolute, relative, exact_relative = None, None, None
    metric_reason = None
    if not reasons:
        with localcontext() as context:
            context.prec = maximum_digits
            context.traps[Inexact], context.traps[Rounded] = True, True
            absolute = current.value - baseline.value
        if baseline.value == 0:
            metric_reason = 'DYNAMIC_BASELINE_ZERO'
        elif baseline.value < 0 and not comparison.allow_negative_baseline:
            metric_reason = 'DYNAMIC_BASELINE_NONPOSITIVE_NOT_APPROVED'
        else:
            exact_relative = Fraction(absolute) * 100 / Fraction(baseline.value)
            if max(len(str(abs(exact_relative.numerator))), len(str(exact_relative.denominator))) > maximum_digits:
                raise QualificationRejected('DYNAMIC_DECIMAL_LIMIT_EXCEEDED')
            with localcontext() as context:
                context.prec = maximum_digits
                context.rounding = comparison.rounding
                relative = (Decimal(exact_relative.numerator) / Decimal(exact_relative.denominator)).quantize(
                    Decimal(1).scaleb(-comparison.relative_decimal_places))
    if reasons:
        state = 'unknown'
    elif condition['metric'] == 'relative_delta' and exact_relative is None:
        reasons.append(metric_reason)
        state = 'unknown'
    else:
        threshold = Decimal(condition['threshold'])
        if not threshold.is_finite():
            raise QualificationRejected('DYNAMIC_THRESHOLD_INVALID')
        if condition['unit'] != ('%' if condition['metric'] == 'relative_delta' else current.reference['normalized_unit']):
            raise QualificationRejected('DYNAMIC_THRESHOLD_UNIT_MISMATCH')
        direction = (absolute > 0 if condition['series']['direction'] == 'increasing' else absolute < 0)
        direction = direction or absolute == 0 and not condition['series']['equal_value_breaks_series']
        metric = Fraction(absolute) if condition['metric'] == 'absolute_delta' else exact_relative
        state = 'met' if direction and OPERATORS[condition['operator']](metric, Fraction(threshold)) else 'not_met'
    return {'position': position, 'baseline_field_id': baseline.reference['field_id'], 'current_field_id': current.reference['field_id'],
        'baseline_time': time_bounds(baseline), 'current_time': time_bounds(current), 'state': state,
        'reason_codes': list(dict.fromkeys(reasons)), 'absolute_delta': format(absolute, 'f') if absolute is not None else None,
        'relative_delta': format(relative, 'f') if relative is not None else None,
        'elapsed_seconds': int(smallest) if smallest == largest and smallest >= 1 else None,
        'elapsed_min_seconds': int(smallest) if smallest >= 0 and smallest.is_integer() else None,
        'elapsed_max_seconds': int(largest) if largest >= 1 and largest.is_integer() else None,
        'relative_reason': metric_reason,
        'relative_exact': None if exact_relative is None else {'numerator': str(exact_relative.numerator), 'denominator': str(exact_relative.denominator)}}


def trend_signal(condition, samples, baseline, current, comparison, window, relations, maximum_digits):
    if current is None or baseline is None:
        return {'state': 'unknown', 'reason_codes': ['DYNAMIC_BASELINE_OR_CURRENT_REQUIRED'], 'pair_results': [], 'samples': []}
    ordered = sorted([item for item in samples if item is current or strictly_before(item.interval, current.interval)],
        key=lambda item: (item.interval.lower, item.reference['fact_version_id']))
    missing_count = len(ordered) < condition['series']['sample_count']
    adjacent = list(zip(ordered, ordered[1:]))
    pairs = [pair_result(left, right, condition, comparison, window, relations,
        position=index, maximum_digits=maximum_digits) for index, (left, right) in enumerate(adjacent)]
    evaluated = pairs
    if condition['series']['comparison_scope'] == 'baseline_to_current':
        if any(item['state'] == 'unknown' for item in pairs):
            evaluated = pairs
        else:
            evaluated = [pair_result(baseline, current, condition, comparison, window, relations,
                position=0, maximum_digits=maximum_digits)]
    deltas = [Decimal(item['absolute_delta']) for item in pairs if item['absolute_delta'] is not None]
    direction_broken = any(value < 0 if condition['series']['direction'] == 'increasing' else value > 0 for value in deltas)
    direction_broken = direction_broken or condition['series']['equal_value_breaks_series'] and any(value == 0 for value in deltas)
    state = 'not_met' if direction_broken or any(item['state'] == 'not_met' for item in evaluated) else 'unknown' if missing_count or any(item['state'] == 'unknown' for item in evaluated) else 'met'
    reasons = list(dict.fromkeys(reason for item in pairs + ([] if evaluated is pairs else evaluated) for reason in item['reason_codes']))
    if missing_count:
        reasons.append('DYNAMIC_SERIES_SAMPLE_COUNT_INSUFFICIENT')
    if direction_broken:
        reasons.append('DYNAMIC_SERIES_DIRECTION_NOT_MET')
    return {'state': state, 'reason_codes': reasons,
        'pair_results': pairs, 'baseline_current_result': None if evaluated is pairs else evaluated[0], 'samples': ordered}
