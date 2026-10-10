import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { Input, TextArea } from 'antd-mobile';
import { ArrowDown, LockKeyhole, type IconComponent } from './ThreeDIcon';
import { Link } from 'react-router';
export { FormActions } from './SaveActions';

export function Section({ title, caption, icon: Icon, children }: { title: string; caption?: string; icon?: IconComponent; children: ReactNode }) {
  const id = useId();
  return <section className="form-section" aria-labelledby={id}>
    <header className="form-section-header">{Icon && <span className="section-symbol"><Icon size={20} aria-hidden="true" /></span>}<div><h2 id={id}>{title}</h2>{caption && <p>{caption}</p>}</div></header>
    <div className="form-section-body">{children}</div>
  </section>;
}

export function Notice({ title = '登录并选择患儿后填写', children }: { title?: string; children?: ReactNode }) {
  return <aside className="access-notice"><LockKeyhole size={17} aria-hidden="true" /><div><strong>{title}</strong>{children && <span className="visually-hidden">{children}</span>}</div><Link to="/login" aria-label="查看登录入口">登录</Link></aside>;
}

export function Field({ label, hint, multiline = false, numeric = false, placeholder = '登录后填写', required = false }: { label: string; hint?: string; multiline?: boolean; numeric?: boolean; placeholder?: string; required?: boolean }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}{required && <span className="field-required">完整记录必填</span>}</label>
    {multiline ? <TextArea id={id} disabled aria-describedby={hint ? `${id}-hint` : undefined} placeholder={placeholder} rows={3} /> : <Input id={id} disabled aria-describedby={hint ? `${id}-hint` : undefined} inputMode={numeric ? 'decimal' : 'text'} placeholder={placeholder} />}
    {hint && <p id={`${id}-hint`} className="field-hint">{hint}</p>}
  </div>;
}

export function SelectField({ label, options, disabled = true, value, onChange, hint }: { label: string; options: readonly string[]; disabled?: boolean; value?: string; onChange?: (value: string) => void; hint?: string }) {
  const id = useId();
  return <div className="field"><label htmlFor={id}>{label}</label><select id={id} disabled={disabled} value={value} onChange={event => onChange?.(event.target.value)} aria-describedby={hint ? `${id}-hint` : undefined}>{options.map(option => <option key={option}>{option}</option>)}</select>{hint && <p id={`${id}-hint`} className="field-hint">{hint}</p>}</div>;
}

export function Disclosure({ title, children, open, onToggle }: { title: string; children: ReactNode; open?: boolean; onToggle?: (open: boolean) => void }) {
  return <details className="disclosure" open={open} onToggle={event => onToggle?.(event.currentTarget.open)}><summary><span>{title}</span><ArrowDown size={18} aria-hidden="true" /></summary><div className="disclosure-body">{children}</div></details>;
}

export function TimeField({ label }: { label: string }) {
  const [precision, setPrecision] = useState('无法确定');
  const id = useId();
  return <div className="time-field">
    <SelectField label={`${label}精度`} options={['无法确定', '仅知道日期', '知道日期与时间']} value={precision} onChange={setPrecision} disabled={false} />
    {precision !== '无法确定' && <div className="field"><label htmlFor={id}>{label}</label><input id={id} disabled type={precision === '仅知道日期' ? 'date' : 'datetime-local'} /><p className="field-hint">按照原始资料填写；时区需随资料确认。</p><SelectField label={`${label}时区`} options={['待确认', 'Asia/Shanghai（UTC+08:00）', '其他时区']} /></div>}
    <p className="field-hint">{precision === '无法确定' ? `${label}尚未填写，不使用登记时间补充。` : '精度选择仅调整字段展示，日期保持空白。'}</p>
  </div>;
}

export function EmptyPanel({ title, description, icon: Icon, children }: { title: string; description: string; icon: IconComponent; children?: ReactNode }) {
  return <section className="empty-panel"><span className="empty-symbol"><Icon size={30} strokeWidth={1.5} aria-hidden="true" /></span><h2>{title}</h2><p>{description}</p>{children}</section>;
}

export function Facts({ items }: { items: readonly (readonly [string, string])[] }) {
  return <dl className="facts">{items.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

export function SourceHelp() {
  return <Disclosure title="资料来源与核对状态"><dl className="help-definitions"><dt>资料来源</dt><dd>记录家庭自报、报告转录或获准临床资料的出处。</dd><dt>核对状态</dt><dd>由授权人员对指定内容核对。家庭登记或转录确认后，资料仍为未核对。</dd><dt>需要补充</dt><dd>缺少来源、单位或时间时，保留原始内容并注明待补项目。</dd></dl></Disclosure>;
}

export function ChoiceBar({ label, options, value, onChange }: { label: string; options: readonly string[]; value: string; onChange: (value: string) => void }) {
  return <div className="choice-bar" role="group" aria-label={label}>{options.map(option => <button type="button" key={option} aria-pressed={value === option} onClick={() => onChange(option)}>{option}</button>)}</div>;
}
