import { useId } from 'react';
import { emptyTime, timeInputValue, timePayload, type TimeInput } from './medicalTimeValue';
export { emptyTime, timePayload, type TimeInput };

export function MedicalTimeInput({ label, value, onChange, ui2 = false, disabled = false, precisionDetails = false, extended = false }: { label: string; value: TimeInput; onChange: (value: TimeInput) => void; ui2?: boolean; disabled?: boolean; precisionDetails?: boolean; extended?: boolean }) {
  const id = useId();
  const extraPrecisions = [['second', '准确到秒'], ['year', '只知道年份'], ['month_range', '月份范围'], ['relative', '相对时间原文']] as const;
  const precision = <label className="field" htmlFor={`${id}-precision`}><span>时间精度</span><select aria-label="时间精度" id={`${id}-precision`} disabled={disabled} value={value.precision} onChange={event => onChange({ precision: event.target.value as TimeInput['precision'], value: '', timezone: value.timezone })}><option value="minute">{ui2 ? '准确到分钟' : '知道日期与时间'}</option><option value="date">{ui2 ? '只知道日期' : '仅知道日期'}</option><option value="month">只知道月份</option>{extraPrecisions.filter(([key]) => extended || value.precision === key).map(([key, text]) => <option key={key} value={key}>{text}</option>)}<option value="unknown">无法确定</option></select></label>;
  const textual = ['year', 'month_range', 'relative', 'unknown'].includes(value.precision);
  const input = <label className="field" htmlFor={id}><span>{label}</span><input id={id} disabled={disabled || (value.precision === 'unknown' && !precisionDetails)} type={value.precision === 'month' ? 'month' : value.precision === 'date' || (precisionDetails && value.precision === 'unknown') ? 'date' : textual ? 'text' : 'datetime-local'} step={value.precision === 'second' ? 1 : undefined} placeholder={value.precision === 'month_range' ? '2026-01/2026-03' : undefined} value={value.value} onChange={event => onChange(timeInputValue(value, event.currentTarget, precisionDetails))} /></label>;
  const timezone = <><label className="field"><span>时区</span>{extended ? <input aria-label="时区" disabled={disabled} value={value.timezone} placeholder="原文明确的时区，例如 Asia/Shanghai" onChange={event => onChange({ ...value, preserved: undefined, timezone: event.target.value, utcOffsetMinutes: null })} /> : <select aria-label="时区" disabled={disabled} value={value.timezone} onChange={event => onChange({ ...value, preserved: undefined, timezone: event.target.value })}><option value="">无法确定</option><option value="Asia/Shanghai">Asia/Shanghai（UTC+08:00）</option>{value.timezone && value.timezone !== 'Asia/Shanghai' && <option value={value.timezone}>{value.timezone}</option>}</select>}</label>{extended && <label className="field"><span>UTC 时差（分钟）</span><input aria-label="UTC 时差（分钟）" type="number" min={-840} max={840} step={1} disabled={disabled || !!value.timezone} value={value.utcOffsetMinutes ?? ''} onChange={event => onChange({ ...value, preserved: undefined, utcOffsetMinutes: event.target.value === '' ? null : Number(event.target.value) })} /></label>}</>;
  if (ui2 && precisionDetails) return <div style={{ minWidth: 0 }}>{input}<details><summary>时间精度与时区</summary>{precision}{value.precision !== 'unknown' && value.precision !== 'month' && timezone}</details></div>;
  if (ui2) return <>{input}{precision}{value.precision !== 'unknown' && value.precision !== 'month' && <details><summary>时间的补充信息</summary>{timezone}</details>}</>;
  return <fieldset className="medical-time-input"><legend>{label}</legend>
    {precision}
    {value.precision !== 'unknown' && <>{input}{value.precision !== 'month' && timezone}</>}
    <p className="field-hint">未知时间保持待补充，时间来自原始记录。</p>
  </fieldset>;
}
