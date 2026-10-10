from decimal import Decimal, Inexact, Rounded, localcontext

from assessment.classification import classify_2016
from assessment.ms import compute_ms
from assessment.policies import CLASSIFICATION_VARIABLES, MS_VARIABLES
from assessment.qualified_facts import QualificationRejected


def compute_static(kind, selections, *, maximum_digits, context_reasons, applicability):
    required = CLASSIFICATION_VARIABLES if kind == 'classification' else MS_VARIABLES if kind == 'ms' else None
    if required is None or set(selections) != required | {'sjia', 'fever'}:
        raise QualificationRejected('ASSESSMENT_VARIABLE_SET_INVALID')
    if applicability not in ('applicable', 'not_applicable'):
        raise QualificationRejected('ASSESSMENT_APPLICABILITY_INVALID')
    for selection in selections.values():
        fact = selection.selected
        if fact is not None and (not fact.eligible or fact.interval is None or not isinstance(fact.value, Decimal)
                or not fact.value.is_finite() or fact.reference['verification_id'] is None):
            raise QualificationRejected('ASSESSMENT_QUALIFIED_INPUT_INCOMPLETE')
    missing = [{'variable_code': code, 'reason_codes': list(selection.reasons)}
        for code, selection in sorted(selections.items()) if selection.selected is None]
    missing.extend(context_reasons)
    values = {code: selection.selected.value if selection.selected is not None else None for code, selection in selections.items()}
    for code in ('sjia', 'fever'):
        if values[code] is not None and values[code] not in (0, 1):
            raise QualificationRejected('ASSESSMENT_PREMISE_ENCODING_INVALID')
    premise = ('not_met' if any(values[code] == 0 for code in ('sjia', 'fever'))
        else 'unknown' if any(values[code] is None for code in ('sjia', 'fever')) else 'met')
    if applicability == 'not_applicable' and kind == 'ms':
        return {'computation_status': 'completed', 'missing_items': missing,
            'classification_conditions': {'BR-C00': 'not_met', 'BR-C06': 'not_applicable'} if kind == 'classification' else None,
            'classification_result': 'not_applicable' if kind == 'classification' else None,
            'ms_total': None, 'ms_contributions': None if kind == 'classification' else {},
            'ms_threshold_result': None, 'applicability': 'not_applicable'}
    if applicability == 'not_applicable':
        premise = 'not_met'
    if kind == 'classification':
        inputs = {code: {'value': value,
            'qualifier': selections[code].selected.reference['qualifier'] if selections[code].selected is not None else None}
            for code, value in values.items() if code in required}
        if context_reasons and applicability != 'not_applicable':
            inputs = {code: {'value': None, 'qualifier': None} for code in required}
            premise = 'unknown'
        result, conditions = classify_2016(premise={'state': 'unknown' if premise == 'unknown' else 'known', 'met': premise == 'met'},
            ferritin=inputs['ferritin'], platelet=inputs['platelet_count'], ast=inputs['ast'],
            triglycerides=inputs['triglycerides'], fibrinogen=inputs['fibrinogen'])
        conditions['sjia'] = 'unknown' if values['sjia'] is None else 'met' if values['sjia'] == 1 else 'not_met'
        conditions['fever'] = 'unknown' if values['fever'] is None else 'met' if values['fever'] == 1 else 'not_met'
        return {'computation_status': 'insufficient' if result == 'insufficient' else 'completed', 'missing_items': missing,
            'applicability': 'not_applicable' if result == 'not_applicable' else 'insufficient' if result == 'insufficient' else 'applicable',
            'classification_conditions': conditions, 'classification_result': result,
            'ms_total': None, 'ms_contributions': None, 'ms_threshold_result': None}
    if premise != 'met' or context_reasons:
        return {'computation_status': 'completed' if premise == 'not_met' and not context_reasons else 'insufficient', 'missing_items': missing,
            'classification_conditions': None, 'classification_result': None,
            'ms_total': None, 'ms_contributions': {}, 'ms_threshold_result': None,
            'applicability': 'not_applicable' if premise == 'not_met' else 'insufficient'}
    for code in required:
        fact = selections[code].selected
        if fact is not None and code not in ('cns', 'hemorrhagic_manifestations', 'active_arthritis') and fact.reference['qualifier'] != 'eq':
            raise QualificationRejected('MS_EXACT_VALUE_REQUIRED')
        if fact is not None and code in ('cns', 'hemorrhagic_manifestations', 'active_arthritis') and fact.value not in (0, 1):
            raise QualificationRejected('MS_CATEGORICAL_ENCODING_INVALID')
    with localcontext() as context:
        context.prec = maximum_digits
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        computed = compute_ms({code: values[code] for code in required})
    return {'computation_status': 'insufficient' if computed['missing_items'] else 'completed', 'missing_items': missing,
        'classification_conditions': None, 'classification_result': None,
        'ms_total': computed['ms_total'], 'ms_contributions': computed['contributions'],
        'ms_threshold_result': computed['threshold_result'], 'applicability': 'applicable'}
