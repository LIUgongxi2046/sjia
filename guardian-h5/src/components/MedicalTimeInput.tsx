import { useId } from 'react';
import { emptyTime, timeInputValue, timePayload, type TimeInput } from './medicalTimeValue';
export { emptyTime, timePayload, type TimeInput };

export function MedicalTimeInput({ label, value, onChange, ui2 = false, disabled = false, precisionDetails = false }: { label: string; value: TimeInput; onChange: (value: TimeInput) => void; ui2?: boolean; disabled?: boolean; precisionDetails?: boolean }) {
  const id = useId();
  const precision = <label className="field" htmlFor={`${id}-precision`}><span>时间精度</span><select aria-label="时间精度" id={`${id}-precision`} disabled={disabled} value={value.precision} onChange={event => onChange({ precision: event.target.value as TimeInput['precision'], value: '', timezone: value.timezone })}><option value="minute">{ui2 ? '准确时间' : '知道日期与时间'}</option><option value="date">{ui2 ? '只知道日期' : '仅知道日期'}</option><option value="month">只知道月份</option><option value="unknown">无法确定</option></select></label>;
  const input = <label className="field" htmlFor={id}><span>{label}</span><input id={id} disabled={disabled || (value.precision === 'unknown' && !precisionDetails)} type={value.precision === 'month' ? 'month' : value.precision === 'date' || (precisionDetails && value.precision === 'unknown') ? 'date' : 'datetime-local'} value={value.value} onChange={event => onChange(timeInputValue(value, event.currentTarget, precisionDetails))} /></label>;
  const timezone = <label className="field"><span>时区</span><select aria-label="时区" disabled={disabled} value={value.timezone} onChange={event => onChange({ ...value, timezone: event.target.value })}><option value="">无法确定</option><option value="Asia/Shanghai">Asia/Shanghai（UTC+08:00）</option></select></label>;
  if (ui2 && precisionDetails) return <div style={{ minWidth: 0 }}>{input}<details><summary>时间精度与时区</summary>{precision}{value.precision !== 'unknown' && value.precision !== 'month' && timezone}</details></div>;
  if (ui2) return <>{input}{precision}{value.precision !== 'unknown' && value.precision !== 'month' && <details><summary>时间的补充信息</summary>{timezone}</details>}</>;
  return <fieldset className="medical-time-input"><legend>{label}</legend>
    {precision}
    {value.precision !== 'unknown' && <>{input}{value.precision !== 'month' && timezone}</>}
    <p className="field-hint">未知时间保持待补充，时间来自原始记录。</p>
  </fieldset>;
}
