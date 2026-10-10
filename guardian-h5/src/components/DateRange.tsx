import { useId } from 'react';

export function DateRange({ start, end, onChange }: { start: string; end: string; onChange: (start: string, end: string) => void }) {
  const id = useId();
  const invalid = Boolean(start && end && start > end);
  return <div className="date-range">
    <div className="field"><label htmlFor={`${id}-start`}>开始日期</label><input id={`${id}-start`} type="date" value={start} onChange={event => onChange(event.target.value, end)} /></div>
    <div className="field"><label htmlFor={`${id}-end`}>结束日期</label><input id={`${id}-end`} type="date" value={end} aria-invalid={invalid} aria-describedby={invalid ? `${id}-error` : undefined} onChange={event => onChange(start, event.target.value)} /></div>
    {invalid && <p role="alert" id={`${id}-error`} className="range-error">结束日期不能早于开始日期。</p>}
  </div>;
}
