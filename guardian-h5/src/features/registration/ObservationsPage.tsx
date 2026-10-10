import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Activity, Mic, Thermometer, UsersRound } from '../../components/ThreeDIcon';
import { PageFrame } from '../../components/PageFrame';
import { Disclosure, Field, Notice, Section, SelectField, TimeField } from '../../components/ClinicalUI';
import { DictionaryField } from '../../components/DictionaryField';
import { VoiceEntry } from '../../components/VoiceEntry';
import { VoiceCandidateReview } from '../../components/VoiceCandidateReview';
import { FormActions } from '../../components/SaveActions';
import { useAccess } from '../../state/access';
import { createRecord, type FieldConfigView } from '../../api/endpoints';
import { MedicalTimeInput, emptyTime, timePayload } from '../../components/MedicalTimeInput';
import { useResource } from '../../api/useResource';
import { useFormDraft, DraftActions } from '../../state/useFormDraft';
import { ReadState } from '../../components/ReadState';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { UI2Sheet } from '../../components/UI2Frame';
import type { VoiceTranscriptSource } from '../../components/VoiceEntry';

export function ObservationsPage({ ui2 = false }: { ui2?: boolean }) {
  const access = useAccess();
  const { patientId, csrfToken } = access;
  const navigate = useNavigate();
  const [description, setDescription] = useState('');
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceText, setVoiceText] = useState<string | null>(null);
  const [voiceSource, setVoiceSource] = useState<VoiceTranscriptSource | undefined>();
  const [voiceSavedVersion, setVoiceSavedVersion] = useState<string | null>(null);
  const [voiceApplied, setVoiceApplied] = useState(false);
  const [temp, setTemp] = useState('');
  const [tempUnit, setTempUnit] = useState('尚未选择');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [measuredTime, setMeasuredTime] = useState(emptyTime);
  const [observedTime, setObservedTime] = useState(emptyTime);
  const [method, setMethod] = useState('');
  const [symptoms, setSymptoms] = useState<Record<string, string>>({});
  const [voiceOpen, setVoiceOpen] = useState(false);
  const closeVoice = useCallback(() => { setVoiceOpen(false); setVoiceText(null); setVoiceSource(undefined); setVoiceBusy(false); }, []);
  const relation = access.relations.find(item => item.patient_id === patientId);
  const canWrite = Boolean(access.principalId && patientId && csrfToken && !access.loading && !access.error
    && relation?.status === 'active' && relation.actions.includes('write_record')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now()));
  const config = useResource<FieldConfigView>(patientId ? `/api/patients/${patientId}/field-config?kind=observation` : null);
  if (config.data && (config.data.patient_id !== patientId || config.data.kind !== 'observation')) throw new Error('观察配置与当前患儿或表单不一致');
  const writableFields = config.data?.state === 'available' ? config.data.fields.filter(field => field.writable) : [];
  const temperatureAllowed = writableFields.some(field => field.field_code === 'temp' && field.value_type === 'decimal');
  const descriptionAllowed = writableFields.some(field => field.field_code === 'observation_note' && field.value_type === 'text');
  const symptomFields = config.data?.state === 'available' ? config.data.fields.filter(field => field.writable && field.value_type === 'categorical') : [];
  const draft = useFormDraft('observation', { temp, tempUnit, description, measuredTime, observedTime, method, symptoms, voiceApplied }, value => {
    setTemp(value.temp); setTempUnit(value.tempUnit); setDescription(value.description); setMethod(value.method);
    setMeasuredTime(value.measuredTime); setObservedTime(value.observedTime); setSymptoms(value.symptoms ?? {});
    setVoiceApplied(value.voiceApplied === true);
  }, !saving && !voiceBusy && voiceText === null && (!ui2 || canWrite));

  const loggedIn = !!patientId;
  const inputBlocked = saving || draft.restoring || draft.saving || voiceBusy || voiceText !== null || (ui2 && !canWrite);
  const submissionBlocked = inputBlocked || (!temp.trim() && !description.trim() && !Object.keys(symptoms).length)
    || (Boolean(temp.trim()) && (tempUnit === '尚未选择' || !Number.isFinite(Number(temp))))
    || (measuredTime.precision !== 'unknown' && !measuredTime.value) || (observedTime.precision !== 'unknown' && !observedTime.value)
    || (ui2 && (config.loading || config.error !== null || config.data?.state !== 'available'
      || (Boolean(temp.trim()) && !temperatureAllowed) || (Boolean(description.trim()) && !descriptionAllowed)
      || Object.keys(symptoms).some(code => !symptomFields.some(field => field.field_code === code))));

  const submit = async () => {
    if (!patientId || draft.restoring || (ui2 && submissionBlocked)) return;
    setError(null);
    setSaving(true);
    const fields: Array<Record<string, unknown>> = [];
    if (temp.trim() !== '') {
      fields.push({ field_code: 'temp', concept_code: 'temp', value_type: 'decimal', value_state: 'known', raw_numeric: temp.trim(), raw_unit: tempUnit === '摄氏度（°C）' ? 'C' : tempUnit === '华氏度（°F）' ? 'F' : null, sample_time_origin: 'observation', medical_time: timePayload(measuredTime), method: method.trim() && method !== '未知' ? method.trim() : null });
    }
    if (description.trim() !== '') {
      fields.push({ field_code: 'observation_note', value_type: 'text', value_state: 'known', raw_text: description.trim(), sample_time_origin: 'observation', medical_time: timePayload(observedTime) });
    }
    for (const field of symptomFields) {
      const value = symptoms[field.field_code];
      if (value === undefined) continue;
      if (value && !field.allowed_values.includes(value)) throw new Error('观察状态不符合当前字段定义');
      fields.push({ field_code: field.field_code, original_name: field.label, value_type: 'categorical', value_state: value ? 'known' : 'unknown', categorical_value: value || null, sample_time_origin: 'observation', medical_time: timePayload(observedTime) });
    }
    try {
      await createRecord(patientId, { kind: 'observation', field_config_version: config.data?.config_version, capture_method: voiceApplied ? 'voice_confirmed' : 'manual', fields, ...draft.submission }, csrfToken ?? undefined);
      draft.markSubmitted();
      navigate('/records', { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
      setSaving(false);
    }
  };

  const handleVoice = (text: string, source?: VoiceTranscriptSource) => {
    setVoiceBusy(false);
    setVoiceText(text);
    setVoiceSource(source);
  };

  if (ui2) {
    const symptomRows = [
      { title: '发热表现', field: symptomFields.find(field => field.label === '发热表现') },
      { title: '皮疹', field: symptomFields.find(field => field.field_code === 'rash') },
      { title: '关节疼痛或肿胀', field: symptomFields.find(field => field.field_code === 'joint_discomfort') },
    ];
    const extraSymptoms = symptomFields.filter(field => !symptomRows.some(row => row.field?.field_code === field.field_code));
    const renderSymptoms = (title: string, field: typeof symptomFields[number] | undefined) => <div key={field?.field_code ?? title}>
      <h3>{title}</h3><div className="segments" role="tablist" aria-label={title}>
        {([{ label: '观察到', value: 'yes' }, { label: '未观察到', value: 'no' }, { label: '未知', value: '' }]).map(option => <button type="button" key={option.label} role="tab"
          aria-selected={(field ? symptoms[field.field_code] ?? '' : '') === option.value}
          className={(field ? symptoms[field.field_code] ?? '' : '') === option.value ? 'selected' : ''}
          disabled={inputBlocked || !field || (Boolean(option.value) && !field.allowed_values.includes(option.value))}
          onClick={() => { if (!field) throw new Error('当前字段尚未获准'); setSymptoms(current => ({ ...current, [field.field_code]: option.value })); }}>{option.label}</button>)}
      </div>
    </div>;
    return <UI2FamilyFrame screenId="UI2-02" title="体温与症状" note="语音、体温与观察状态在同一任务中确认。" backTo="/home"
      overlay={voiceOpen && <UI2Sheet title="语音记录与逐项核对" onClose={closeVoice}>
        {voiceText === null ? <VoiceEntry ui2 structured onApply={handleVoice} onBusyChange={setVoiceBusy} />
          : <VoiceCandidateReview ui2 persistRecord text={voiceText} source={voiceSource} onCancel={closeVoice}
            onSaved={recordVersionId => setVoiceSavedVersion(recordVersionId)} />}
      </UI2Sheet>}>
      <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
      <UI2ReadState loading={config.loading} error={config.error ? new Error(config.error) : null} onRetry={config.reload} />
      {loggedIn && !canWrite && <section className="status-panel permission" role="status"><h2>当前未取得登记权限</h2><p>登记前需要核验当前患儿的写入权限。</p></section>}
      {config.data && config.data.state !== 'available' && <section className="status-panel error" role="status"><h2>观察项目配置暂时不可用</h2><p>{config.data.reason_code}</p><button className="text-action" onClick={config.reload}>重新读取资料</button></section>}
      <button className="voice-entry" disabled={inputBlocked} onClick={() => setVoiceOpen(true)}><Mic size={32} /><span><strong>说一说观察到的情况</strong><small>转成文字后，由本人确认</small></span><ChevronRight size={19} /></button>
      <section className="card temperature"><div className="section-heading"><h2>测量体温</h2><span className="badge neutral">监护人自报</span></div><label className="temperature-value"><input aria-label="体温原值" inputMode="decimal" placeholder="待填写" disabled={inputBlocked || !temperatureAllowed} value={temp} onChange={event => setTemp(event.target.value)} /><span>{tempUnit === '华氏度（°F）' ? '°F' : tempUnit === '无法确定' ? '未知' : '°C'}</span></label><span className="muted">保留原始读数，不补充未知内容</span></section>
      <section className="card form-card"><MedicalTimeInput ui2 disabled={inputBlocked} label="测量时间" value={measuredTime} onChange={setMeasuredTime} /><details><summary>测量方式与补充信息</summary>
        <label className="field"><span>测量方式</span><select aria-label="测量方式" disabled={inputBlocked} value={method} onChange={event => setMethod(event.target.value)}><option value="" disabled>请选择</option>{['腋下', '耳温', '额温', '其他', '未知'].map(value => <option key={value}>{value}</option>)}</select></label>
        <label className="field"><span>体温单位</span><select aria-label="体温单位" disabled={inputBlocked} value={tempUnit} onChange={event => setTempUnit(event.target.value)}>{['尚未选择', '摄氏度（°C）', '华氏度（°F）', '无法确定'].map(value => <option key={value}>{value}</option>)}</select></label>
        <MedicalTimeInput ui2 disabled={inputBlocked} label="症状发生时间" value={observedTime} onChange={setObservedTime} /><small>体温与症状分别保留时间，未填写的项目不会保存。</small>
        {extraSymptoms.map(field => renderSymptoms(field.label, field))}
      </details></section>
      <div className="section-heading"><h2>观察到的症状</h2></div><section className="card symptoms">{symptomRows.map(row => renderSymptoms(row.title, row.field))}</section>
      <section className="card form-card"><label className="field"><span>补充说明</span><textarea disabled={inputBlocked || !descriptionAllowed} placeholder="记录观察到的内容，可不填写" value={description} onChange={event => setDescription(event.target.value)} /></label></section>
      {error && <p role="alert">{error}</p>}
      {voiceSavedVersion && <section className="status-panel" role="status"><h2>语音观察已保存</h2><p>RecordVersion：{voiceSavedVersion}</p><button className="text-action" onClick={() => navigate('/records')}>查看已保存记录</button></section>}
      <button className="button" disabled={submissionBlocked} onClick={() => void submit()}>{saving ? '正在保存' : '确认并保存'}</button>
      <button className="text-action center" disabled={inputBlocked} onClick={() => void draft.save()}>{draft.saving ? '正在保存草稿' : '保存为草稿'}</button>
      {draft.restoring && <p role="status">正在恢复草稿</p>}{draft.error && <p role="alert">{draft.error}</p>}
      {draft.draft && <p role="status">草稿保存时间：{new Date(draft.draft.saved_at).toLocaleString('zh-CN')} · 版本 {draft.draft.version}{draft.dirty ? ' · 当前修改尚未保存' : ''}</p>}
    </UI2FamilyFrame>;
  }

  if (!loggedIn) {
    return <PageFrame screenId="S04" title="体温与症状" subtitle="" backTo="/home">
      <Notice />
      <Section title="这次体温是多少？" icon={Thermometer}>
        <Field label="体温数值" numeric required placeholder="保留原始测量值" />
        <SelectField label="体温单位" options={['尚未选择', '摄氏度（°C）', '华氏度（°F）', '无法确定']} />
        <TimeField label="测量时间" />
        <Disclosure title="补充测量方式"><Field label="测量方式" placeholder="按照测量方式填写" hint="测量方式未知时保持未知。" /></Disclosure>
      </Section>
      <Disclosure title="记录症状或其他观察">
      <Section title="症状观察" caption="记录亲自观察的内容，不预选未观察到。" icon={Activity}>
        <DictionaryField label="观察项目" />
        <SelectField label="观察状态" options={['无法确定', '观察到', '未观察到']} />
        <TimeField label="症状发生时间" />
        <Field label="观察说明" multiline placeholder="描述观察内容和时间" />
        <VoiceEntry onApply={(text) => setDescription((prev) => [prev, text].filter(Boolean).join('\n'))} onBusyChange={setVoiceBusy} />
        {description && <p className="voice-status">已确认的症状说明：{description}</p>}
      </Section>
      </Disclosure>
      <FormActions label="保存本次记录" state={voiceBusy ? { status: 'unavailable' } : undefined} />
    </PageFrame>;
  }

  return <PageFrame screenId="S04" title="体温与症状" subtitle="记录本次测量。" backTo="/home">
    {voiceText === null ? <VoiceEntry structured onApply={handleVoice} onBusyChange={setVoiceBusy} /> : <VoiceCandidateReview text={voiceText} source={voiceSource} csrfToken={csrfToken ?? undefined} onCancel={() => { setVoiceText(null); setVoiceSource(undefined); }} onSaved={recordVersionId => setVoiceSavedVersion(recordVersionId)} />}
    <Section title="这次体温是多少？" icon={Thermometer}>
      <div className="temperature-entry">
      <div className="field"><label htmlFor="obs-temp">体温数值</label><input id="obs-temp" inputMode="decimal" placeholder="保留原始测量值" value={temp} onChange={event => setTemp(event.target.value)} /></div>
      <SelectField label="体温单位" options={['尚未选择', '摄氏度（°C）', '华氏度（°F）', '无法确定']} disabled={false} value={tempUnit} onChange={setTempUnit} />
      </div>
      <MedicalTimeInput label="测量时间" value={measuredTime} onChange={setMeasuredTime} />
      <label className="field">测量方式<input value={method} onChange={event => setMethod(event.target.value)} /></label>
    </Section>
    <Section title="症状观察" caption="记录亲自观察的内容。" icon={Activity}>
      <ReadState loading={config.loading} error={config.error} onRetry={config.reload} />
      {config.data && config.data.state !== 'available' && <p>观察项目配置暂时不可用，可以保留原始观察说明。</p>}
      {symptomFields.map(field => <div key={field.field_code} className="field"><label htmlFor={`obs-${field.field_code}`}>{field.label}</label><select id={`obs-${field.field_code}`} value={symptoms[field.field_code] ?? '__unselected'} onChange={event => setSymptoms(current => ({ ...current, [field.field_code]: event.target.value }))}><option value="__unselected" disabled>尚未记录</option><option value="">无法确定</option>{field.allowed_values.map(value => <option key={value} value={value}>{({ yes: '观察到', no: '未观察到' } as Record<string, string>)[value] ?? value}</option>)}</select></div>)}
      <div className="field"><label htmlFor="obs-desc">观察说明</label><textarea id="obs-desc" rows={3} placeholder="描述观察内容和时间" value={description} onChange={event => setDescription(event.target.value)} /></div>
      <MedicalTimeInput label="症状发生时间" value={observedTime} onChange={setObservedTime} />
    </Section>
    {error && <p className="form-error">{error}</p>}
    <DraftActions controller={draft} disabled={saving || voiceBusy || voiceText !== null} />
    <FormActions label="保存本次记录" state={saving ? { status: 'submitting' } : draft.restoring || draft.saving || voiceBusy || voiceText !== null || (!temp.trim() && !description.trim() && !Object.keys(symptoms).length) || (Boolean(temp.trim()) && (tempUnit === '尚未选择' || !Number.isFinite(Number(temp)))) || (measuredTime.precision !== 'unknown' && !measuredTime.value) || (observedTime.precision !== 'unknown' && !observedTime.value) ? { status: 'unavailable' } : { status: 'ready', onSubmit: submit }} />
  </PageFrame>;
}
