import { fieldLabel, timeLabel, type RecordFieldView } from '../api/records';
import { MedicalTimeInput, timePayload, type TimeInput } from './MedicalTimeInput';
import { Disclosure } from './ClinicalUI';
import { editableMedicalTime, isTimeInputValid } from './medicalTimeValue';

export function recordTimeInput(field: RecordFieldView): TimeInput {
  // Record responses are schema-validated; their legacy TS view uses string enums.
  return editableMedicalTime(field.medical_time as Parameters<typeof editableMedicalTime>[0]);
}

export function RecordFieldEditor({ field, onChange, timeInput, onTimeChange, disabled = false, ui2 = false }: { field: RecordFieldView; onChange: (field: RecordFieldView) => void; timeInput?: TimeInput; onTimeChange: (value: TimeInput) => void; disabled?: boolean; ui2?: boolean }) {
  const valueKey = field.value_type === 'decimal' ? 'raw_numeric' : field.value_type === 'categorical' ? 'categorical_value' : 'raw_text';
  const storedTime = recordTimeInput(field);
  const time = timeInput ?? storedTime;
  return <fieldset disabled={disabled} className={ui2 ? 'card form-card' : 'report-item'} aria-label={fieldLabel(field)}>
    <h3>{fieldLabel(field)}</h3>
    <label className="field">值的状态<select value={field.value_state} onChange={event => onChange({ ...field, value_state: event.target.value, raw_numeric: null, raw_text: null, categorical_value: null })}><option value="known">已经记录</option><option value="unknown">无法确定</option></select></label>
    {field.value_type !== 'time' && <label className="field">原始内容<input value={field[valueKey] ?? ''} disabled={field.value_state !== 'known'} onChange={event => onChange({ ...field, [valueKey]: event.target.value || null, ...(field.value_type === 'decimal' ? { raw_text: event.target.value || null } : {}) })} /></label>}
    {field.value_type === 'decimal' && <><label className="field">原始单位<input value={field.raw_unit ?? ''} onChange={event => onChange({ ...field, raw_unit: event.target.value || null })} /></label><label className="field">原报告比较符<select value={field.qualifier ?? ''} onChange={event => onChange({ ...field, qualifier: event.target.value || null })}><option value="">无法确定</option><option value="eq">=</option><option value="lt">&lt;</option><option value="le">≤</option><option value="gt">&gt;</option><option value="ge">≥</option></select></label></>}
    <Disclosure title="名称、方法与参考区间">{(['original_name', 'method', 'reference_interval', 'report_flag'] as const).map(key => <label key={key} className="field">{({ original_name: '原项目名称', method: '测量或检测方法', reference_interval: '参考区间原文', report_flag: '报告标记' })[key]}<input value={field[key] ?? ''} onChange={event => onChange({ ...field, [key]: event.target.value || null })} /></label>)}</Disclosure>
    <Disclosure title="更正医学时间"><p>当前时间：{timeLabel(field.medical_time)}</p><MedicalTimeInput extended ui2={ui2} disabled={disabled} label="医学时间" value={time} onChange={value => {
      onTimeChange(value);
      if (ui2) return;
      if (!isTimeInputValid(value)) return;
      onChange({ ...field, medical_time: timePayload(value) as RecordFieldView['medical_time'], ...(field.value_type === 'time' ? { value_state: value.precision === 'unknown' ? 'unknown' : 'known' } : {}) });
    }} /></Disclosure>
  </fieldset>;
}
