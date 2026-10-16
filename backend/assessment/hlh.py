"""Pure, deterministic HLH-2004 *reference support*, never a diagnosis.

Authenticated fact selection and clinical authorization live outside this
module. It deliberately performs no ORM, network, mutation, default policy
selection, notification or clinical approval. See hlh_contracts for the boundary.
"""
from datetime import timedelta
from decimal import Decimal
import hashlib
import json

from assessment.hlh_contracts import (
    FACT_CODES, HLHConditionResult, HLHCriterionResult, HLHInputs, HLHResult,
    HLHRulePolicy,
)


NUMERIC_CODES = ('hemoglobin', 'platelet_count', 'neutrophil_count',
    'triglycerides', 'fibrinogen', 'ferritin', 'soluble_cd25')


def _checksum(model):
    return hashlib.sha256(json.dumps(model.model_dump(mode='json'), ensure_ascii=True,
        sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _condition(state, *reasons, observations=()):
    identities = {item.proof.fact_version_id for item in observations if item.proof is not None}
    return HLHConditionResult(state=state, reason_codes=sorted(set(reasons)),
        fact_version_ids=sorted(identities, key=str))


def _combine(conditions, required):
    lower = sum(item.state == 'met' for item in conditions)
    upper = sum(item.state != 'not_met' for item in conditions)
    state = 'met' if lower >= required else 'not_met' if upper < required else 'unknown'
    return HLHConditionResult(state=state,
        reason_codes=sorted({reason for item in conditions for reason in item.reason_codes}),
        fact_version_ids=sorted({identity for item in conditions for identity in item.fact_version_ids}, key=str))


def _criterion(code, condition, subconditions=None):
    return HLHCriterionResult(code=code, **condition.model_dump(), subconditions=subconditions or {})


def compare_hlh_threshold(value, qualifier, operator, threshold):
    """Universal interval comparison. Censored bounds are never treated as exact.

    This is mathematical three-valued logic, not a convention stated by the
    clinical source. Values are already validated Decimal strings at the public
    boundary. An interval straddling the criterion boundary stays unknown.
    """
    if (not isinstance(value, Decimal) or not isinstance(threshold, Decimal)
            or not value.is_finite() or not threshold.is_finite()
            or qualifier not in ('eq', 'lt', 'le', 'gt', 'ge')
            or operator not in ('lt', 'le', 'gt', 'ge')):
        raise ValueError('HLH_THRESHOLD_COMPARISON_INVALID')
    if qualifier == 'eq':
        met = {'lt': value < threshold, 'le': value <= threshold,
            'gt': value > threshold, 'ge': value >= threshold}[operator]
        return 'met' if met else 'not_met'
    if operator == 'lt':
        if qualifier == 'lt' and value <= threshold or qualifier == 'le' and value < threshold:
            return 'met'
        if qualifier in ('gt', 'ge') and value >= threshold:
            return 'not_met'
    elif operator == 'le':
        if qualifier in ('lt', 'le') and value <= threshold:
            return 'met'
        if qualifier == 'gt' and value >= threshold or qualifier == 'ge' and value > threshold:
            return 'not_met'
    elif operator == 'gt':
        if qualifier in ('lt', 'le') and value <= threshold:
            return 'not_met'
        if qualifier == 'gt' and value >= threshold or qualifier == 'ge' and value > threshold:
            return 'met'
    elif operator == 'ge':
        if qualifier == 'lt' and value <= threshold or qualifier == 'le' and value < threshold:
            return 'not_met'
        if qualifier in ('gt', 'ge') and value >= threshold:
            return 'met'
    return 'unknown'


def _qualification_reason(code, observation, policy, inputs):
    if observation.qualification != 'qualified':
        return f'HLH_{observation.qualification.upper()}'
    proof = observation.proof
    if proof.verified_at < proof.available_at:
        return 'HLH_VERIFICATION_BEFORE_AVAILABILITY'
    if proof.medical_interval.upper > inputs.assessment_at:
        return 'HLH_MEDICAL_TIME_AFTER_ASSESSMENT'
    if proof.available_at > inputs.known_at or proof.verified_at > inputs.known_at:
        return 'HLH_NOT_AVAILABLE_AND_VERIFIED_AT_CUTOFF'
    if proof.valid_until is not None and proof.valid_until <= inputs.known_at:
        return 'HLH_VERIFICATION_EXPIRED'
    if inputs.assessment_at - proof.medical_interval.lower > timedelta(
            seconds=policy.temporal_policy.maximum_age_seconds[code]):
        return 'HLH_OUTSIDE_EXPLICIT_WINDOW'
    return None


def evaluate_hlh_2004(policy: HLHRulePolicy, inputs: HLHInputs) -> HLHResult:
    """Evaluate explicit, source-bound parameters and qualified input claims.

    Callers must use validated models. The models are revalidated here because
    BaseModel.model_copy/model_construct can otherwise bypass validation.
    References and role/consent/approval claims are not authenticated by a pure
    function, so every returned result retains its offline-only use boundary.
    """
    if not isinstance(policy, HLHRulePolicy) or not isinstance(inputs, HLHInputs):
        raise TypeError('HLH_VALIDATED_TYPED_INPUTS_REQUIRED')
    policy = HLHRulePolicy.model_validate_json(policy.model_dump_json())
    inputs = HLHInputs.model_validate_json(inputs.model_dump_json())
    reasons = {code: _qualification_reason(code, getattr(inputs, code), policy, inputs) for code in FACT_CODES}

    # A selected set outside its explicitly chosen cross-laboratory span cannot
    # silently be stitched into an eight-criterion result. The molecular path
    # remains independent of this numeric-set conflict.
    participating = [getattr(inputs, code).proof.medical_interval for code in NUMERIC_CODES if reasons[code] is None]
    cross_span = bool(participating and max(item.upper for item in participating) - min(item.lower for item in participating) > timedelta(
        seconds=policy.temporal_policy.maximum_cross_laboratory_span_seconds))
    if cross_span:
        for code in NUMERIC_CODES:
            if reasons[code] is None:
                reasons[code] = 'HLH_CROSS_LABORATORY_WINDOW_CONFLICT'

    def assertion(code):
        observation = getattr(inputs, code)
        if reasons[code]:
            return _condition('unknown', reasons[code], observations=[observation])
        return _condition('met' if observation.value else 'not_met', observations=[observation])

    def numeric(code, threshold=None):
        observation = getattr(inputs, code)
        threshold = threshold or getattr(policy.thresholds, code)
        if reasons[code]:
            return _condition('unknown', reasons[code], observations=[observation])
        if observation.unit != threshold.unit:
            return _condition('unknown', 'HLH_NATIVE_UNIT_MISMATCH', observations=[observation])
        if observation.qualifier == 'lt' and Decimal(observation.value) == 0:
            return _condition('unknown', 'HLH_IMPOSSIBLE_NONNEGATIVE_INTERVAL', observations=[observation])
        state = compare_hlh_threshold(Decimal(observation.value), observation.qualifier,
            threshold.operator, Decimal(threshold.value))
        return _condition(state, *(['HLH_CENSORED_VALUE_STRADDLES_BOUNDARY'] if state == 'unknown' else []),
            observations=[observation])

    age = inputs.age_at_hemoglobin
    if reasons['age_at_hemoglobin']:
        age_result = _condition('unknown', reasons['age_at_hemoglobin'], observations=[age])
    elif (inputs.hemoglobin.proof is None
            or age.hemoglobin_fact_version_id != inputs.hemoglobin.proof.fact_version_id
            or age.proof.medical_interval != inputs.hemoglobin.proof.medical_interval):
        age_result = _condition('unknown', 'HLH_AGE_NOT_BOUND_TO_HEMOGLOBIN_SAMPLE', observations=[age])
    elif age.proof.medical_interval.lower != age.proof.medical_interval.upper:
        age_result = _condition('unknown', 'HLH_AGE_SAMPLE_TIME_AMBIGUOUS', observations=[age])
    else:
        age_result = _condition('met', observations=[age])
    if age_result.state == 'unknown':
        hemoglobin = _condition('unknown', *age_result.reason_codes, observations=[inputs.hemoglobin, age])
    else:
        threshold = (policy.thresholds.infant_hemoglobin if age.age_days < policy.infant_age_days_exclusive
            else policy.thresholds.hemoglobin)
        hemoglobin = numeric('hemoglobin', threshold)
        hemoglobin = HLHConditionResult(state=hemoglobin.state, reason_codes=hemoglobin.reason_codes,
            fact_version_ids=sorted(set(hemoglobin.fact_version_ids + age_result.fact_version_ids), key=str))
    cytopenia_subconditions = {'hemoglobin': hemoglobin, 'age_at_hemoglobin': age_result,
        'platelet_count': numeric('platelet_count'), 'neutrophil_count': numeric('neutrophil_count')}
    cytopenia = _combine([cytopenia_subconditions[code] for code in
        ('hemoglobin', 'platelet_count', 'neutrophil_count')], policy.cytopenia_lineages_required)

    fasting = assertion('triglycerides_fasting')
    if fasting.state != 'unknown' and (inputs.triglycerides.proof is None
            or inputs.triglycerides.proof.sample_id is None
            or inputs.triglycerides_fasting.proof.sample_id != inputs.triglycerides.proof.sample_id
            or inputs.triglycerides_fasting.proof.medical_interval != inputs.triglycerides.proof.medical_interval):
        fasting = _condition('unknown', 'HLH_FASTING_NOT_BOUND_TO_TRIGLYCERIDES_SAMPLE',
            observations=[inputs.triglycerides_fasting])
    if fasting.state != 'unknown' and inputs.triglycerides.proof.medical_interval.lower != inputs.triglycerides.proof.medical_interval.upper:
        fasting = _condition('unknown', 'HLH_FASTING_SAMPLE_TIME_AMBIGUOUS', observations=[inputs.triglycerides_fasting])
    triglycerides_numeric = numeric('triglycerides')
    # Non-fasting or unknown fasting does not establish a negative fasting-TG
    # criterion: it makes that branch unavailable, even if the number is low.
    triglycerides = (triglycerides_numeric if fasting.state == 'met' else
        _condition('unknown', *fasting.reason_codes,
            'HLH_FASTING_NOT_CONFIRMED', observations=[inputs.triglycerides, inputs.triglycerides_fasting]))
    if fasting.state == 'met':
        triglycerides = HLHConditionResult(state=triglycerides.state, reason_codes=triglycerides.reason_codes,
            fact_version_ids=sorted(set(triglycerides.fact_version_ids + fasting.fact_version_ids), key=str))
    fibrinogen = numeric('fibrinogen')
    lipid_subconditions = {'triglycerides_numeric': triglycerides_numeric, 'fasting': fasting,
        'fasting_triglycerides': triglycerides, 'fibrinogen': fibrinogen}
    lipids = _combine([triglycerides, fibrinogen], 1)

    hemophagocytosis = assertion('hemophagocytosis')
    if hemophagocytosis.state != 'unknown' and inputs.hemophagocytosis.specimen_site not in (
            'bone_marrow', 'spleen', 'lymph_node'):
        hemophagocytosis = _condition('unknown', 'HLH_SOURCE_TABLE_SPECIMEN_NOT_CONFIRMED',
            observations=[inputs.hemophagocytosis])
    malignancy = assertion('no_malignancy_evidence')
    histology = _combine([hemophagocytosis, malignancy], 2)
    nk = assertion('nk_activity')
    if nk.state != 'unknown' and inputs.nk_activity.laboratory_reference is None:
        nk = _condition('unknown', 'HLH_NK_LOCAL_LAB_REFERENCE_REQUIRED', observations=[inputs.nk_activity])

    criteria = [
        _criterion('fever', assertion('fever')),
        _criterion('splenomegaly', assertion('splenomegaly')),
        _criterion('cytopenias', cytopenia, cytopenia_subconditions),
        _criterion('lipids_or_fibrinogen', lipids, lipid_subconditions),
        _criterion('hemophagocytosis', histology,
            {'hemophagocytosis': hemophagocytosis, 'no_malignancy_evidence': malignancy}),
        _criterion('nk_activity', nk),
        _criterion('ferritin', numeric('ferritin')),
        _criterion('soluble_cd25', numeric('soluble_cd25')),
    ]
    lower = sum(item.state == 'met' for item in criteria)
    upper = sum(item.state != 'not_met' for item in criteria)
    criteria_path = _combine(criteria, policy.criteria_required)
    molecular = assertion('molecular')
    paths = _combine([molecular, criteria_path], 1)
    applicability = assertion('applicability')
    reference_result = ('not_applicable' if applicability.state == 'not_met' else
        'insufficient' if applicability.state == 'unknown' or paths.state == 'unknown' else
        'criteria_supported' if paths.state == 'met' else 'criteria_not_supported')
    limitations = ['HLH_REFERENCE_SUPPORT_NOT_DIAGNOSIS', 'HLH_NOT_SUPPORTED_DOES_NOT_EXCLUDE_DISEASE',
        'HLH_OFFLINE_PROOFS_AND_POLICY_REQUIRE_AUTHENTICATED_GOVERNANCE',
        'HLH_WINDOW_AND_UNKNOWN_SEMANTICS_ARE_VERSIONED_SOFTWARE_POLICY']
    if cross_span:
        limitations.append('HLH_CROSS_LABORATORY_WINDOW_CONFLICT')
    return HLHResult(schema_version='hlh-2004-result-1', kind='hlh_2004_reference',
        use_boundary='offline_reference_support_only', clinical_use_authorized=False,
        policy_revision_id=policy.policy_revision_id, policy_checksum=_checksum(policy), input_checksum=_checksum(inputs),
        source_table=policy.source_table, source_revision_id=policy.source_revision_id,
        semantics_version=policy.semantics_version, assessment_at=inputs.assessment_at, known_at=inputs.known_at,
        applicability={'met': 'applicable', 'not_met': 'not_applicable', 'unknown': 'unknown'}[applicability.state],
        applicability_condition=applicability, molecular_condition=molecular, criteria=criteria,
        criteria_met_lower_bound=lower, criteria_met_upper_bound=upper, criteria_path=criteria_path.state,
        molecular_or_criteria=paths.state, reference_result=reference_result,
        supported_paths=([name for name, condition in [('molecular', molecular), ('five_of_eight', criteria_path)]
            if condition.state == 'met'] if applicability.state == 'met' else []),
        limitation_codes=limitations)
