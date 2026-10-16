import type { AssessmentView, HlhConditionResult, HlhState } from '../../../frontend-design/v2-types';

export const hlhCriterionCodes = ['fever', 'splenomegaly', 'cytopenias', 'lipids_or_fibrinogen',
  'hemophagocytosis', 'nk_activity', 'ferritin', 'soluble_cd25'] as const;
const aggregate = (states: HlhState[], minimum: number): HlhState =>
  states.filter(value => value === 'met').length >= minimum ? 'met'
    : states.filter(value => value !== 'not_met').length < minimum ? 'not_met' : 'unknown';

/** Cross-field checks after JSON-schema validation. Unknown is never negative. */
export function validateHlhReference(view: AssessmentView): void {
  const result = view.output?.hlh_result;
  if (!result || view.output!.classification_conditions !== null || view.output!.classification_result !== null
      || view.output!.ms_contributions !== null || view.output!.ms_total !== null || view.output!.ms_threshold_result !== null)
    throw new Error('HLH 参考输出类型不一致。');
  if (new Set(result.criteria.map(item => item.code)).size !== 8
      || hlhCriterionCodes.some(code => !result.criteria.some(item => item.code === code)))
    throw new Error('HLH 八项条件身份不完整或重复。');
  const lower = result.criteria.filter(item => item.state === 'met').length;
  const upper = result.criteria.filter(item => item.state !== 'not_met').length;
  const criteriaPath = aggregate(result.criteria.map(item => item.state), 5);
  const paths = aggregate([result.molecular_condition.state, criteriaPath], 1);
  const reference = result.applicability === 'not_applicable' ? 'not_applicable'
    : result.applicability === 'unknown' || paths === 'unknown' ? 'insufficient'
      : paths === 'met' ? 'criteria_supported' : 'criteria_not_supported';
  const supported = result.applicability === 'applicable'
    ? [...(result.molecular_condition.state === 'met' ? ['molecular'] : []), ...(criteriaPath === 'met' ? ['five_of_eight'] : [])] : [];
  if (lower !== result.criteria_met_lower_bound || upper !== result.criteria_met_upper_bound
      || criteriaPath !== result.criteria_path || paths !== result.molecular_or_criteria
      || reference !== result.reference_result || JSON.stringify(supported) !== JSON.stringify(result.supported_paths)
      || (reference === 'insufficient') !== (view.computation_status === 'insufficient')
      || Date.parse(result.assessment_at) !== Date.parse(view.assessment_at) || Date.parse(result.known_at) !== Date.parse(view.known_at)
      || view.applicability !== (result.applicability === 'unknown' ? 'insufficient' : result.applicability))
    throw new Error('HLH 条件计数、未知范围或适用结论不一致。');
  const factIds = view.inputs.map(item => item.input_snapshot.reference.fact_version_id);
  const facts = new Set(factIds);
  if (facts.size !== factIds.length) throw new Error('HLH 输入不能重复采用同一事实。');
  const checkCondition = (condition: HlhConditionResult) => {
    if (condition.state !== 'unknown' && condition.fact_version_ids.length === 0
        || new Set(condition.fact_version_ids).size !== condition.fact_version_ids.length
        || condition.fact_version_ids.some(id => !facts.has(id))) throw new Error('HLH 条件缺少本次采用的事实版本。');
  };
  [result.applicability_condition, result.molecular_condition, ...result.criteria].forEach(checkCondition);
  for (const item of result.criteria) {
    Object.values(item.subconditions).forEach(checkCondition);
    const keys = item.code === 'cytopenias' ? ['hemoglobin', 'platelet_count', 'neutrophil_count']
      : item.code === 'lipids_or_fibrinogen' ? ['fasting_triglycerides', 'fibrinogen']
        : item.code === 'hemophagocytosis' ? ['hemophagocytosis', 'no_malignancy_evidence'] : [];
    if (keys.length && (keys.some(key => !item.subconditions[key])
        || aggregate(keys.map(key => item.subconditions[key].state), item.code === 'lipids_or_fibrinogen' ? 1 : 2) !== item.state))
      throw new Error('HLH 复合条件不能拆开重复计数。');
  }
}
