import type { AssessmentClinicalSelection, AssessmentCreationPlan } from '../../../../frontend-design/v2-types';
import { timeLabel } from '../../api/records';

export function AssessmentSelectionFields({ plan, mode, disabled, canSelect, choices, onChange }: {
  plan: AssessmentCreationPlan; mode: 'current' | 'replay'; disabled: boolean; canSelect: boolean;
  choices: AssessmentClinicalSelection[]; onChange: (value: AssessmentClinicalSelection[]) => void;
}) {
  if (!plan.selection_variables.length) return null;
  if (mode === 'replay') return <label className="field"><span>截止时点之前保存的专业选择</span>
    <select disabled={disabled} value={choices[0]?.prior_task_id ?? ''} onChange={event => {
      const saved = plan.prior_selections.find(item => item.task_id === event.target.value);
      if (!saved && event.target.value) throw new Error('原专业选择已不属于当前批准组合。');
      onChange(saved ? saved.choices.map(item => ({ ...item, prior_task_id: saved.task_id })) : []);
    }}><option value="">没有引用时保留资料不足</option>{plan.prior_selections.map(item =>
      <option key={item.task_id} value={item.task_id}>{item.saved_at} · {item.task_id}</option>)}</select>
  </label>;
  const change = (code: string, update: Partial<AssessmentClinicalSelection>) => {
    const existing = choices.find(item => item.variable_code === code);
    const value: AssessmentClinicalSelection = { variable_code: code, fact_version_id: '', reason: '', evidence_ref: '', prior_task_id: null,
      ...existing, ...update };
    onChange([...choices.filter(item => item.variable_code !== code), value].filter(item => item.fact_version_id));
  };
  return <>
    {!canSelect && <p role="status">明确选择需要当前专业核对岗位授权。未选择的项目保留资料不足。</p>}
    {plan.selection_variables.map(variable => {
      const selected = choices.find(item => item.variable_code === variable.variable_code);
      return <section className="card form-card" key={variable.variable_code}>
        <h3>{variable.variable_code}</h3>
        <label className="field"><span>指定事实与版本</span><select disabled={disabled || !canSelect}
          value={selected?.fact_version_id ?? ''} onChange={event => change(variable.variable_code,
            { fact_version_id: event.target.value, reason: '', evidence_ref: '' })}>
          <option value="">没有选择时保留资料不足</option>{variable.candidates.map(item => <option key={item.fact_version_id} value={item.fact_version_id}>
            {item.original_name ?? variable.concept_code} · {item.qualifier && item.qualifier !== 'eq' ? `${item.qualifier} ` : ''}{item.raw_value ?? '未知'} {item.raw_unit ?? ''} · {timeLabel(item.medical_time)} · 版本 {item.fact_version}</option>)}</select></label>
        {selected && <>
          <label className="field"><span>选择原因</span><textarea required disabled={disabled || !canSelect} value={selected.reason}
            onChange={event => change(variable.variable_code, { reason: event.target.value })} /></label>
          <label className="field"><span>获准原始依据</span><select required disabled={disabled || !canSelect} value={selected.evidence_ref}
            onChange={event => change(variable.variable_code, { evidence_ref: event.target.value })}>
            <option value="">请选择准确依据</option>{plan.selection_evidence.map(reference => <option key={reference} value={reference}>{reference}</option>)}</select></label>
          <p className="muted">提交时重新核查资料资格、准确版本、采样关系、窗口与专业权限。</p>
        </>}
      </section>;
    })}
  </>;
}
