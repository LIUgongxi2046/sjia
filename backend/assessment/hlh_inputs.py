"""Trusted HLH adapter from selected, server-qualified facts.

No submitted numeric values, metadata or proof claims are accepted here. All
semantic metadata is projected from the same governed, verified field rows as
the selected facts; missing or ambiguous bindings remain explicitly unknown.
"""
from decimal import Decimal
import json

from assessment.hlh import evaluate_hlh_2004
from assessment.hlh_contracts import FACT_CODES, HLHInputs, HLHRulePolicy
from assessment.qualified_facts import QualificationRejected


NUMERIC = {'hemoglobin', 'platelet_count', 'neutrophil_count', 'triglycerides',
    'fibrinogen', 'ferritin', 'soluble_cd25'}


def validate_hlh_windows(rule, window):
    variables = {item['variable_code']: item for item in window['variables']}
    if (not set(FACT_CODES) <= set(variables)
            or window['max_cross_analyte_gap_seconds'] != rule.temporal_policy.maximum_cross_laboratory_span_seconds):
        raise QualificationRejected('HLH_RULE_WINDOW_POLICY_MISMATCH')
    for code in FACT_CODES:
        item = variables[code]
        if (item['current_max_age_seconds'] != rule.temporal_policy.maximum_age_seconds[code]
                or not item['lower_bound_inclusive'] or not item['upper_bound_inclusive']):
            raise QualificationRejected('HLH_RULE_WINDOW_POLICY_MISMATCH')


def materialize_hlh_inputs(inputs, *, assessment_at, known_at):
    payload = {'schema_version': 'hlh-2004-inputs-1', 'assessment_at': assessment_at.isoformat(), 'known_at': known_at.isoformat()}
    selected = {code: inputs.selections[code].selected for code in FACT_CODES}
    for code in FACT_CODES:
        fact = selected[code]
        if fact is not None and (not fact.eligible or fact.interval is None or not isinstance(fact.value, Decimal)
                or not fact.value.is_finite() or fact.reference.get('verification_id') is None):
            raise QualificationRejected('HLH_QUALIFIED_INPUT_INCOMPLETE')
        reference = fact.reference if fact is not None else None
        metadata = reference.get('hlh_metadata') if reference else None
        if fact is not None and metadata is None:
            raise QualificationRejected('HLH_SERVER_METADATA_REQUIRED')
        proof = None
        if fact is not None:
            proof = {'fact_version_id': reference['fact_version_id'], 'source_revision_id': reference['source_revision_id'],
                'verification_id': reference['verification_id'], 'verified_by': metadata['verified_by'],
                'evidence_ref': metadata['verification_evidence_ref'],
                'medical_interval': {**fact.interval.snapshot(), 'precision': reference['medical_precision']},
                'available_at': reference['available_at'], 'verified_at': metadata['verified_at'],
                'valid_until': None, 'sample_id': None}
            if (metadata['report_id'] is not None and metadata['report_source_matches']
                    and metadata['report_sample_interval'] == fact.interval.snapshot()):
                proof['sample_id'] = metadata['report_id']
        reason_codes = inputs.selections[code].reasons
        qualification = 'qualified' if fact is not None else ('conflict' if any('CONFLICT' in reason or 'AMBIGUOUS' in reason
            for reason in reason_codes) else 'missing')
        value = {'qualification': qualification, 'proof': proof}
        if code in NUMERIC:
            if fact is not None and fact.value < 0:
                value['qualification'] = 'invalid_context'
            value.update(value=format(fact.value, 'f') if fact is not None and fact.value >= 0 else None,
                qualifier=reference['qualifier'] if reference else None, unit=reference['normalized_unit'] if reference else None)
        elif code == 'age_at_hemoglobin':
            hemoglobin = selected['hemoglobin']
            bound = (fact is not None and hemoglobin is not None
                and reference['record_version_id'] == hemoglobin.reference['record_version_id']
                and fact.interval == hemoglobin.interval and fact.interval.lower == fact.interval.upper and fact.interval.upper_inclusive)
            if fact is not None and (not bound or fact.value < 0 or fact.value != fact.value.to_integral_value()):
                value['qualification'] = 'invalid_context'
            value.update(age_days=int(fact.value) if fact is not None and fact.value >= 0 and fact.value == fact.value.to_integral_value() else None,
                hemoglobin_fact_version_id=hemoglobin.reference['fact_version_id'] if bound else None)
        else:
            if fact is not None and fact.value not in (0, 1):
                raise QualificationRejected('HLH_ASSERTION_ENCODING_INVALID')
            value['value'] = bool(fact.value) if fact is not None else None
        if code == 'molecular':
            value['assertion_type'] = 'molecular_diagnosis_consistent_with_hlh'
        if code == 'hemophagocytosis':
            value['specimen_site'] = metadata['specimen_site'] if metadata and metadata['specimen_site'] else 'unknown'
        if code == 'nk_activity':
            value['laboratory_reference'] = (metadata['reference_interval']
                if metadata and isinstance(metadata['reference_interval'], str) and metadata['reference_interval'].strip() else None)
        payload[code] = value
    if inputs.applicability == 'not_applicable':
        # Preserve the qualified applicability observation; the external context
        # decision is applied by compute_hlh below and remains in the snapshot.
        pass
    elif inputs.context_reasons:
        payload['applicability'].update(qualification='invalid_context', value=None)
    return HLHInputs.model_validate_json(json.dumps(payload))


def compute_hlh(inputs):
    if inputs.input_rule is None or inputs.hlh_inputs is None:
        raise QualificationRejected('HLH_QUALIFIED_INPUTS_REQUIRED')
    rule = HLHRulePolicy.model_validate_json(json.dumps(inputs.rule.payload))
    result = evaluate_hlh_2004(rule, inputs.hlh_inputs).model_dump(mode='json')
    if inputs.applicability == 'not_applicable':
        result.update(applicability='not_applicable', reference_result='not_applicable', supported_paths=[])
        result['limitation_codes'].append('HLH_GOVERNED_CONTEXT_NOT_APPLICABLE')
    missing = [{'variable_code': code, 'reason_codes': list(selection.reasons)}
        for code, selection in sorted(inputs.selections.items()) if selection.selected is None]
    missing.extend(inputs.context_reasons)
    for criterion in result['criteria']:
        if criterion['state'] == 'unknown' and criterion['reason_codes']:
            missing.append({'variable_code': criterion['code'], 'reason_codes': criterion['reason_codes']})
    for code in ('applicability', 'molecular'):
        condition = result[code + '_condition']
        if condition['state'] == 'unknown' and condition['reason_codes']:
            missing.append({'variable_code': code, 'reason_codes': condition['reason_codes']})
    return {'computation_status': 'insufficient' if result['reference_result'] == 'insufficient' else 'completed',
        'missing_items': missing, 'classification_conditions': None, 'classification_result': None,
        'ms_contributions': None, 'ms_total': None, 'ms_threshold_result': None, 'hlh_result': result,
        'applicability': {'applicable': 'applicable', 'not_applicable': 'not_applicable', 'unknown': 'insufficient'}[result['applicability']]}
