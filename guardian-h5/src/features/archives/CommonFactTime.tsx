import { useState } from 'react';
import { MedicalTimeInput, emptyTime, timePayload, type TimeInput } from '../../components/MedicalTimeInput';
import { isTimeInputValid } from '../../components/medicalTimeValue';
import { editableTime, type EditableFact } from './FactFieldsEditor';
import type { FactDecision } from '../../../../frontend-design/v2-types';

export interface FactTimeDecision extends EditableFact { decision: FactDecision['decision'] | 'pending'; reason: string }

export function canApplyCommonTime(value: FactTimeDecision): boolean {
  return value.decision !== 'excluded' && (!value.field.medical_time || value.field.medical_time.precision === 'unknown')
    && !(value.context.raw_time_text?.trim() && !['unknown', 'explicit_common'].includes(value.context.time_origin));
}

export function applyCommonTime(form: Record<string, FactTimeDecision>, selected: string[], time: TimeInput,
  evidence: string, confirmed: boolean): Record<string, FactTimeDecision> {
  const medical = timePayload(time);
  if (!confirmed || !evidence.trim() || !medical || !selected.length) throw new Error('请确认原文共同时间依据及适用项目。');
  const next = { ...form };
  for (const id of new Set(selected)) {
    const value = form[id];
    if (!value || !canApplyCommonTime(value)) continue;
    const source = { ...medical, raw_text: evidence.trim() };
    next[id] = { ...value, decision: 'modified', reason: `本人确认共同时间依据：${evidence.trim()}`,
      time: editableTime(source), field: { ...value.field, medical_time: structuredClone(source), sample_time_origin: 'item' },
      context: { ...value.context, medical_time: structuredClone(source), raw_time_text: evidence.trim(), time_origin: 'explicit_common' } };
  }
  return next;
}

export function CommonFactTime({ form, disabled, onApply }: { form: Record<string, FactTimeDecision>;
  disabled: boolean; onApply: (value: Record<string, FactTimeDecision>) => void }) {
  const [time, setTime] = useState<TimeInput>({ ...emptyTime });
  const [selected, setSelected] = useState<string[]>([]), [evidence, setEvidence] = useState('');
  const [confirmed, setConfirmed] = useState(false), [message, setMessage] = useState('');
  const eligible = selected.filter(id => form[id] && canApplyCommonTime(form[id]));
  return <section className="card form-card"><details><summary>应用原文明确的共同时间</summary>
    <p>只应用于本人选择且没有明确逐项时间的候选；已有逐项时间保持原值。</p>
    <MedicalTimeInput label="共同医学时间" ui2 extended disabled={disabled} value={time} onChange={value => { setTime(value); setConfirmed(false); }} />
    <label className="field"><span>共同时间原文依据</span><textarea disabled={disabled} value={evidence} onChange={event => { setEvidence(event.target.value); setConfirmed(false); }} /></label>
    {Object.entries(form).map(([id, value], index) => <label className="field" key={id}><span>项目 {index + 1}：{value.field.original_name ?? value.field.field_code}{canApplyCommonTime(value) ? '' : '（已有明确时间或已排除，保持原值）'}</span><input type="checkbox" aria-label={`共同时间项目 ${index + 1}`} disabled={disabled || !canApplyCommonTime(value)} checked={eligible.includes(id)} onChange={event => { const checked = event.target.checked; setSelected(current => checked ? [...current, id] : current.filter(value => value !== id)); setConfirmed(false); }} /></label>)}
    <label className="field"><span>我已核对原文，以上共同时间明确适用于所选项目</span><input type="checkbox" aria-label="确认共同时间依据" disabled={disabled} checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /></label>
    <button className="button secondary" disabled={disabled || !confirmed || !evidence.trim() || !eligible.length || time.precision === 'unknown' || !isTimeInputValid(time)} onClick={() => { onApply(applyCommonTime(form, eligible, time, evidence, confirmed)); setSelected([]); setConfirmed(false); setMessage(`已应用到 ${eligible.length} 项，其他项目保持原值。`); }}>应用到所选项目</button>
    {message && <p role="status">{message}</p>}
  </details></section>;
}
