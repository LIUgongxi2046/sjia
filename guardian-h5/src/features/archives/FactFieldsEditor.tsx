import type { FieldInput, MedicalTime, MedicalTimeInput } from '../../../../frontend-design/types';
import type { FactContextInput } from '../../../../frontend-design/v2-types';
import { MedicalTimeInput as TimeEditor, timePayload, type TimeInput } from '../../components/MedicalTimeInput';
import { editableMedicalTime, isTimeInputValid } from '../../components/medicalTimeValue';
import { assertionStateOptions, factContextOptions } from '../../components/factPresentation';

export interface EditableFact { field: FieldInput; context: FactContextInput; time: TimeInput; documented: TimeInput }
export function editableTime(value: MedicalTime | MedicalTimeInput | null): TimeInput {
  return editableMedicalTime(value);
}
export function FactFieldsEditor({ value, onChange, disabled, sourceLabel }: { value: EditableFact; onChange: (value: EditableFact) => void; disabled: boolean; sourceLabel: string }) {
  const field = value.field, context = value.context;
  const update = (changes: Partial<FieldInput>) => onChange({ ...value, field: { ...field, ...changes } });
  const contextUpdate = (changes: Partial<FactContextInput>) => onChange({ ...value, context: { ...context, ...changes } });
  const changeTime = (time: TimeInput) => {
    if (!isTimeInputValid(time)) { onChange({ ...value, time }); return; }
    const medical = time.precision === 'unknown' || !time.value ? null : timePayload(time) as MedicalTimeInput;
    const origin = medical ? context.time_origin === 'unknown' || context.time_origin === 'legacy' ? 'item' : context.time_origin : 'unknown';
    onChange({ ...value, time, field: { ...field, medical_time: medical,
      sample_time_origin: origin === 'explicit_common' ? 'item' : origin }, context: { ...context, medical_time: medical,
        time_origin: origin, raw_time_text: context.raw_time_text ?? medical?.raw_text ?? null } });
  };
  return <>
    <label className="field"><span>原始项目名称</span><input disabled={disabled} value={field.original_name ?? ''} placeholder="按原文填写" onChange={event => update({ original_name: event.target.value || null })} /></label>
    <div className="field-grid"><label className="field"><span>原始数值</span><input disabled={disabled} inputMode={field.value_type === 'decimal' ? 'decimal' : 'text'} value={field.value_type === 'decimal' ? field.raw_numeric ?? field.raw_text ?? '' : field.raw_text ?? ''} onChange={event => {
      const text = event.target.value;
      update({ raw_text: text || null, raw_numeric: field.value_type === 'decimal' && text ? text : null,
        categorical_value: field.value_type === 'categorical' && text ? text : null, value_state: text ? 'known' : 'unknown' });
    }} /></label><label className="field"><span>原始单位</span><input disabled={disabled} value={field.raw_unit ?? ''} onChange={event => update({ raw_unit: event.target.value || null })} /></label></div>
    <label className="field"><span>比较符</span><select disabled={disabled} value={field.qualifier ?? ''} onChange={event => update({ qualifier: event.target.value ? event.target.value as FieldInput['qualifier'] : null })}>{[['eq', '等于'], ['lt', '小于'], ['gt', '大于'], ['le', '小于等于'], ['ge', '大于等于'], ['', '未知']].map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
    <TimeEditor label="此项医学时间" ui2 extended disabled={disabled} value={value.time} onChange={changeTime} />
    {!isTimeInputValid(value.time) && <p role="alert">请补全符合所选精度的医学时间。</p>}
    <details><summary>语境、就诊与原文时间</summary>
      <label className="field"><span>事实语境</span><select disabled={disabled} value={context.context_type} onChange={event => contextUpdate({ context_type: event.target.value as FactContextInput['context_type'] })}>{factContextOptions.map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <label className="field"><span>发生状态</span><select disabled={disabled} value={context.assertion_state} onChange={event => contextUpdate({ assertion_state: event.target.value as FactContextInput['assertion_state'] })}>{assertionStateOptions.map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <label className="field"><span>原始时间文字</span><input disabled={disabled} value={context.raw_time_text ?? ''} onChange={event => contextUpdate({ raw_time_text: event.target.value || null })} /></label>
      <label className="field"><span>时间出处</span><select disabled={disabled} value={context.time_origin} onChange={event => {
        const origin = event.target.value as FactContextInput['time_origin'];
        onChange({ ...value, field: { ...field, sample_time_origin: origin === 'explicit_common' ? 'item' : origin === 'legacy' ? 'unknown' : origin }, context: { ...context, time_origin: origin } });
      }}><option value="item">此项原文</option><option value="explicit_common">明确共同时间</option><option value="observation">观察记录</option><option value="therapy">治疗记录</option><option value="unknown">无法确定</option>{context.time_origin === 'legacy' && <option value="legacy">历史时间出处</option>}</select></label>
      <label className="field"><span>关联就诊标识</span><input disabled={disabled} value={context.episode_id ?? ''} onChange={event => contextUpdate({ episode_id: event.target.value || null })} /></label>
      <TimeEditor label="文书形成时间" ui2 extended disabled={disabled} value={value.documented} onChange={documented => onChange(isTimeInputValid(documented) ? { ...value, documented, context: { ...context, documented_time: documented.precision === 'unknown' || !documented.value ? null : timePayload(documented) as MedicalTimeInput } } : { ...value, documented })} />
      {!isTimeInputValid(value.documented) && <p role="alert">请补全文书形成时间。</p>}
      <label className="field"><span>检测方法</span><input disabled={disabled} value={field.method ?? ''} onChange={event => update({ method: event.target.value || null })} /></label>
      <label className="field"><span>原始参考范围</span><input disabled={disabled} value={field.reference_interval ?? ''} onChange={event => update({ reference_interval: event.target.value || null })} /></label>
      <div className="pair"><span>来源文书与页码</span><strong style={{ overflowWrap: 'anywhere' }}>{sourceLabel}</strong></div>
    </details>
  </>;
}
