import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react';
import { Mic, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2Sheet } from '../../components/UI2Frame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { correctRecord, readRecord, type FieldConfigView } from '../../api/endpoints';
import { ApiError } from '../../api/client';
import { useRecordResource } from '../../api/useRecordResource';
import { useResource } from '../../api/useResource';
import { fieldLabel, timeLabel, type RecordFieldView, type RecordView } from '../../api/records';
import { RecordFieldEditor, recordTimeInput } from '../../components/RecordFieldEditor';
import { useFormDraft } from '../../state/useFormDraft';
import { MedicalTimeInput, emptyTime, timePayload, type TimeInput } from '../../components/MedicalTimeInput';
import { VoiceEntry } from '../../components/VoiceEntry';
import { VoiceCandidateReview, type ConfirmedVoice } from '../../components/VoiceCandidateReview';
import { correctionFields, correctionContentMatches } from './correctionFields';

interface CorrectionState extends Record<string, unknown> {
  recordId: string; fields: RecordFieldView[]; kind: string; reason: string; version: number;
  timeInputs?: Record<string, TimeInput>;
}

export function CorrectionPage() {
  const access = useAccess();
  const { recordId } = useParams();
  return <CorrectionContent key={JSON.stringify([access.principalId, access.patientId, recordId])} recordId={recordId ?? null} />;
}

function CorrectionContent({ recordId }: { recordId: string | null }) {
  const access = useAccess();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const hasDraft = params.has('draft');
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const resource = useRecordResource<RecordView>(canRead && recordId ? `/api/records/${recordId}` : null, access.patientId, 'RecordView', recordId);
  const record = canRead ? resource.data : null;
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  const canCorrect = !!record && record.kind !== 'professional' && !!access.csrfToken && record.allowed_actions?.includes('correct_record') === true
    && relation?.status === 'active' && relation.actions.includes('correct_record')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  const config = useResource<FieldConfigView>(canCorrect && access.patientId ? `/api/patients/${access.patientId}/field-config?kind=${record.kind}` : null);
  if (config.data && (config.data.patient_id !== access.patientId || config.data.kind !== record?.kind)) throw new Error('更正字段配置与当前患儿或记录类型不一致。');
  const [form, setForm] = useState<CorrectionState | null>(null);
  const [draftValidator, setDraftValidator] = useState<((value: unknown) => CorrectionState) | null>(null);
  const [inputValidator, setInputValidator] = useState<((value: unknown) => void) | null>(null);
  const [saving, setSaving] = useState(false);
  const [unknownResult, setUnknownResult] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [composing, setComposing] = useState(false);
  const [familyConfirmed, setFamilyConfirmed] = useState(false);
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [voiceText, setVoiceText] = useState<string | null>(null);
  const pending = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const currentVersion = !!record && !!form && form.version === record.version;
  const canEdit = canCorrect && config.data?.state === 'available' && !config.loading && !config.error && currentVersion;
  const payload = form ?? { recordId, fields: record?.fields ?? [], kind: record?.kind ?? null, reason: '', version: record?.version ?? null, timeInputs: {} };
  const draft = useFormDraft('correction', payload, value => {
    if (!record || !draftValidator) throw new Error('尚未取得当前记录及草稿校验依据。');
    const restored = draftValidator(value);
    if (restored.recordId !== record.record_id || restored.version !== record.version || restored.kind !== record.kind
      || restored.fields.length !== record.fields.length || restored.fields.some((field, index) => field.field_id !== record.fields[index].field_id || field.field_code !== record.fields[index].field_code
        || field.value_type !== record.fields[index].value_type || field.concept_code !== record.fields[index].concept_code
        || JSON.stringify(field.source_anchor) !== JSON.stringify(record.fields[index].source_anchor))) {
      throw new Error('更正草稿与当前记录、字段或原版本不一致，请查看当前记录。');
    }
    if (Object.keys(restored.timeInputs ?? {}).some(id => !record.fields.some(field => field.field_id === id))) throw new Error('更正草稿包含其他字段的时间。');
    setForm(restored);
    setFamilyConfirmed(false);
  }, canEdit && !saving && !unknownResult && !voiceBusy && voiceText === null, [], canCorrect && draftValidator !== null);
  useEffect(() => {
    if (!canRead) return;
    let current = true;
    void import('../../api/recordSearchSchema').then(module => {
      if (current) {
        setDraftValidator(() => (value: unknown) => module.validateCorrectionValue<CorrectionState>('CorrectionDraft', value));
        setInputValidator(() => (value: unknown) => { module.validateCorrectionValue('RecordCorrectionInput', value); });
      }
    });
    return () => { current = false; };
  }, [canRead]);
  useEffect(() => {
    if (!record || form || hasDraft) return;
    const initial = { recordId: record.record_id, fields: record.fields, kind: record.kind, reason: '', version: record.version, timeInputs: {} };
    setForm(initial); draft.initialize(initial);
  }, [record, form, hasDraft]);
  useEffect(() => {
    mounted.current = true;
    const changed = () => {
      if (document.visibilityState !== 'visible') {
        pending.current?.abort(); setVoiceOpen(false); setVoiceText(null); setVoiceBusy(false);
      }
    };
    document.addEventListener('visibilitychange', changed);
    return () => { mounted.current = false; pending.current?.abort(); document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => { if (!canCorrect) { setVoiceOpen(false); setVoiceText(null); setVoiceBusy(false); } }, [canCorrect]);
  const incompleteTime = Object.values(form?.timeInputs ?? {}).some(value => value.precision !== 'unknown' && !value.value);
  const blocked = !canEdit || !inputValidator || saving || unknownResult || draft.restoring || draft.saving || !!draft.error || voiceBusy || voiceText !== null;
  const fields = record && currentVersion && form ? form.fields : record?.fields ?? [];
  const temperature = fields.find(field => field.field_code === 'temp' && field.value_type === 'decimal');
  const note = fields.find(field => field.field_code === 'observation_note' && field.value_type === 'text');
  const fieldAllowed = (field: RecordFieldView | undefined) => !!field && config.data?.fields.some(value => value.writable && value.field_code === field.field_code && value.value_type === field.value_type) === true;
  const editableTemperature = !blocked && fieldAllowed(temperature);
  const update = (field: RecordFieldView) => {
    if (blocked || !form || !fieldAllowed(field)) throw new Error('当前字段不符合更正条件。');
    setForm(current => { if (!current) throw new Error('尚未取得当前更正内容。'); return { ...current, fields: current.fields.map(value => value.field_id === field.field_id ? field : value) }; }); setFamilyConfirmed(false); setError(null);
  };
  const updateTime = (field: RecordFieldView, value: TimeInput) => {
    if (blocked || !form || !fieldAllowed(field)) throw new Error('当前字段时间不符合更正条件。');
    const complete = value.precision === 'unknown' || !!value.value;
    setFamilyConfirmed(false);
    setForm({ ...form, timeInputs: { ...form.timeInputs, [field.field_id]: value }, fields: complete ? form.fields.map(item => item.field_id === field.field_id ? { ...item,
      medical_time: timePayload(value), sample_time_origin: value.precision === 'unknown' ? 'unknown' : item.sample_time_origin === 'unknown' ? form.kind === 'report' ? 'item' : form.kind === 'therapy' ? 'therapy' : 'observation' : item.sample_time_origin,
      ...(item.value_type === 'time' ? { value_state: value.precision === 'unknown' ? 'unknown' : 'known' } : {}) } : item) : form.fields }); setError(null);
  };
  const closeVoice = () => { setVoiceOpen(false); setVoiceBusy(false); setVoiceText(null); };
  const canVoice = !blocked && record?.kind === 'observation' && fieldAllowed(note) && relation?.actions.includes('write_record') === true;
  const applyVoice = (value: ConfirmedVoice) => {
    if (!form || !record || !canCorrect || !note || !fieldAllowed(note) || !voiceOpen) throw new Error('语音候选不符合当前更正范围。');
    if (value.temperature && (!temperature || !fieldAllowed(temperature))) throw new Error('当前记录没有获准更正的体温字段。');
    const next = form.fields.map(field => {
      if (field.field_id === note.field_id) return { ...field,
        ...(value.description ? { value_state: 'known' as const,
          raw_text: field.raw_text ? `${field.raw_text}\n${value.description}` : value.description } : {}),
        ...(value.time && value.timeField === 'observation_note' ? { medical_time: timePayload(value.time), sample_time_origin: 'observation' } : {}) };
      if (value.temperature && field.field_id === temperature?.field_id) return { ...field, value_state: 'known', raw_numeric: value.temperature.value, raw_text: value.temperature.value, raw_unit: value.temperature.unit || null,
        ...(value.time && value.timeField === 'temp' ? { medical_time: timePayload(value.time), sample_time_origin: 'observation' } : {}) };
      return field;
    });
    const timeTarget = value.timeField === 'temp' ? temperature : note;
    setForm({ ...form, fields: next, timeInputs: value.time && timeTarget
      && (value.timeField !== 'temp' || value.temperature) ? { ...form.timeInputs, [timeTarget.field_id]: value.time } : form.timeInputs }); closeVoice();
  };
  const submit = () => {
    if (blocked || composing || incompleteTime || !inputValidator || !form || !record || !access.patientId || !recordId || !form.reason.trim()
      || record.kind === 'therapy' && !familyConfirmed) throw new Error('请完成当前记录、更正原因、本人确认和医学时间核验。');
    const submittedFields = correctionFields(form.fields);
    const original = record;
    const controller = new AbortController(); pending.current = controller;
    let dispatched = false;
    const patientId = access.patientId;
    setSaving(true); setError(null);
    void Promise.resolve().then(() => {
      inputValidator({ reason: form.reason, fields: submittedFields, expected_version: form.version, ...draft.submission,
        ...(record.kind === 'therapy' ? { family_confirmation: true } : {}) });
      if (controller.signal.aborted) throw new Error('请求已取消，当前更正尚未提交。');
      dispatched = true; setUnknownResult(true);
      return correctRecord(patientId, recordId, form.reason, submittedFields, form.version, access.csrfToken ?? undefined, draft.submission, controller.signal,
        record.kind === 'therapy' ? true : undefined);
    }).then(async receipt => {
      const saved = await readRecord(recordId, controller.signal);
      if (saved.patient_id !== receipt.patient_id || saved.record_id !== receipt.record_id || saved.record_version_id !== receipt.record_version_id || saved.version !== receipt.version
        || saved.correction_reason !== form.reason || !correctionContentMatches(submittedFields, saved.fields)
        || JSON.stringify(saved.attachments) !== JSON.stringify(original.attachments)
        || saved.fields.some((field, index) => JSON.stringify(field.source_anchor) !== JSON.stringify(original.fields[index].source_anchor))) {
        throw new Error('更正回执与重新读取的内容或出处尚未一致，结果需要核验。');
      }
      if (!mounted.current || controller.signal.aborted) return;
      setUnknownResult(false); draft.markSubmitted(); navigate(`/records/${recordId}`, { replace: true });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!mounted.current) return;
      setSaving(false); pending.current = null;
      setError(controller.signal.aborted && dispatched ? new Error('更正保存结果尚待核验，请查看当前记录，不能重复提交未知结果。') : failure);
      if (!dispatched) setUnknownResult(false);
      if (failure instanceof ApiError && failure.status < 500) {
        setUnknownResult(false);
        if (failure.status === 409) resource.reload();
        if ([401, 403].includes(failure.status)) void access.refresh();
      }
    });
  };
  const symptomRows = [
    { title: '发热表现', field: fields.find(field => field.original_name === '发热表现' && field.value_type === 'categorical') },
    { title: '皮疹', field: fields.find(field => field.field_code === 'rash' && field.value_type === 'categorical') },
    { title: '关节疼痛或肿胀', field: fields.find(field => field.field_code === 'joint_discomfort' && field.value_type === 'categorical') },
  ];
  const temperatureTime = temperature ? form?.timeInputs?.[temperature.field_id] ?? recordTimeInput(temperature) : emptyTime;
  return <UI2FamilyFrame screenId="UI2-08" title="更正记录" note="更正原因与版本检查是必要操作。" backTo={recordId ? `/records/${recordId}` : '/records'}
    overlay={voiceOpen && <UI2Sheet title="语音记录与逐项核对" onClose={closeVoice}>{voiceText === null
      ? <VoiceEntry ui2 structured onApply={text => { setVoiceText(text); setVoiceBusy(false); }} onBusyChange={setVoiceBusy} />
      : <VoiceCandidateReview ui2 text={voiceText} csrfToken={access.csrfToken ?? undefined} onCancel={closeVoice} onApply={applyVoice} />}</UI2Sheet>}>
    <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={resource.loading || access.loading} error={resource.error} onRetry={resource.reload} />
    {canCorrect && <UI2ReadState loading={config.loading} error={config.error ? new Error(config.error) : null} onRetry={config.reload} />}
    <div className="note amber"><ShieldCheck size={17} /><p>更正产生新版本，专业核对需重新完成。</p></div>
    {record && !canCorrect && <section className="status-panel permission" role="status"><h2>当前未取得更正权限</h2><p>请核验本人、当前记录及完整字段范围。</p></section>}
    {record && form && !currentVersion && <section className="status-panel stale" role="status"><h2>原版本已有更新</h2><p>当前输入未提交，请查看当前记录后重新打开更正页面。</p><ContextLink className="text-action" to={`/records/${recordId}`}>查看当前记录<ChevronRight size={17} /></ContextLink></section>}
    {canCorrect && config.data && config.data.state !== 'available' && <p role="status">当前字段配置不具备写入条件：{config.data.reason_code}</p>}
    <div style={{ display: 'contents' }} onCompositionStartCapture={() => setComposing(true)} onCompositionEndCapture={() => setComposing(false)}>
      <button className="voice-entry" disabled={!canVoice} onClick={() => setVoiceOpen(true)}><Mic size={32} /><span><strong>说一说观察到的情况</strong><small>转成文字后，由本人确认</small></span><ChevronRight size={19} /></button>
      <section className="card temperature"><div className="section-heading"><h2>测量体温</h2><span className="badge neutral">{record?.origin_type === 'report_transcription' ? '报告转录' : record?.origin_type === 'authorized_clinical' ? '获准临床资料' : '监护人自报'}</span></div><label className="temperature-value"><input aria-label="体温原值" inputMode="decimal" placeholder="待填写" disabled={!editableTemperature} value={temperature?.raw_numeric ?? ''} onChange={event => { if (!temperature) throw new Error('当前体温字段尚未取得。'); update({ ...temperature, raw_numeric: event.target.value || null, raw_text: event.target.value || null, value_state: event.target.value ? 'known' : 'unknown' }); }} /><span>{temperature ? temperature.raw_unit === 'C' ? '°C' : temperature.raw_unit === 'F' ? '°F' : temperature.raw_unit ?? '未知' : '°C'}</span></label><span className="muted">保留原始读数，不补充未知内容</span></section>
      <section className="card form-card"><MedicalTimeInput ui2 disabled={!editableTemperature} label="测量时间" value={temperatureTime} onChange={value => { if (!temperature) throw new Error('当前体温字段尚未取得。'); updateTime(temperature, value); }} /><details><summary>测量方式与补充信息</summary>
        <label className="field"><span>测量方式</span><select aria-label="测量方式" disabled={!editableTemperature} value={temperature?.method ?? ''} onChange={event => { if (!temperature) throw new Error('当前体温字段尚未取得。'); update({ ...temperature, method: event.target.value || null }); }}>{['腋下', '耳温', '额温', '其他'].map(value => <option key={value}>{value}</option>)}<option value="">未知</option>{temperature?.method && !['腋下', '耳温', '额温', '其他'].includes(temperature.method) && <option>{temperature.method}</option>}</select></label>
        {temperature && <p>当前时间：{timeLabel(temperature.medical_time)}；原始单位：{temperature.raw_unit ?? '待补充'}。</p>}
      </details></section>
      <div className="section-heading"><h2>观察到的症状</h2></div><section className="card symptoms">{symptomRows.map(({ title, field }) => <div key={title}><h3>{title}</h3><div className="segments" role="tablist" aria-label={title}>{[{ label: '观察到', value: 'yes' }, { label: '未观察到', value: 'no' }, { label: '未知', value: null }].map(option => {
        const selected = field ? field.value_state === 'unknown' ? option.value === null : field.categorical_value === option.value : option.value === null;
        const definition = config.data?.fields.find(value => value.field_code === field?.field_code);
        return <button key={option.label} type="button" role="tab" aria-selected={selected} className={selected ? 'selected' : ''} disabled={blocked || !fieldAllowed(field) || (option.value !== null && !definition?.allowed_values.includes(option.value))} onClick={() => { if (!field) throw new Error('当前症状字段尚未取得。'); update({ ...field, value_state: option.value === null ? 'unknown' : 'known', categorical_value: option.value }); }}>{option.label}</button>;
      })}</div>{field && <small>发生时间：{timeLabel(field.medical_time)}；逐项时间见完整字段。</small>}</div>)}</section>
      {record && fields.length > 0 && <details><summary>当前记录全部字段与独立时间</summary>{fields.map(field => <RecordFieldEditor ui2 key={field.field_id} field={field} disabled={blocked || !fieldAllowed(field)} timeInput={form?.timeInputs?.[field.field_id]} onTimeChange={value => updateTime(field, value)} onChange={update} />)}</details>}
      <section className="card form-card"><label className="field"><span>更正原因</span><textarea disabled={blocked} placeholder="说明需要更正的内容与依据" value={record && currentVersion ? form?.reason ?? '' : ''} onChange={event => { if (!form || blocked) throw new Error('当前更正内容尚未取得。'); setForm({ ...form, reason: event.target.value }); setError(null); }} /></label></section>
      {error && <p role="alert">{error.message}</p>}{incompleteTime && <p role="status">所选时间精度尚未填写完整，可以继续编辑或保存草稿。</p>}
      {record?.kind === 'therapy' && <label className="checkbox-row"><input type="checkbox" disabled={blocked} checked={familyConfirmed}
        onChange={event => setFamilyConfirmed(event.target.checked)} /><span>本人已核对患儿、治疗资料来源、实际使用状态及独立起止时间</span></label>}
      <button className="button" disabled={blocked || composing || incompleteTime || !form?.reason.trim() || record?.kind === 'therapy' && !familyConfirmed} onClick={submit}>{saving ? '正在保存' : '保存更正'}</button>
      <button className="text-action center" disabled={blocked || composing} onClick={() => void draft.save()}>{draft.saving ? '正在保存草稿' : '保存为草稿'}</button>
      {draft.restoring && <p role="status">正在核验更正草稿及原版本</p>}{draft.error && <section role="alert"><p>{draft.error}</p><button className="text-action" disabled={!canCorrect || !draftValidator} onClick={draft.retryRestore}>重新读取更正草稿</button></section>}
      {record && draft.draft && <p role="status">草稿保存时间：{new Date(draft.draft.saved_at).toLocaleString('zh-CN')} · 版本 {draft.draft.version}{draft.dirty ? ' · 当前修改尚未保存' : ''}</p>}
      {record && <details><summary>查看原记录与版本</summary><div className="pair"><span>当前记录版本</span><strong>{record.version}</strong></div><ContextLink className="text-action" to={`/records/${record.record_id}`}>查看当前记录<ChevronRight size={17} /></ContextLink><p>逐项字段、出处及医学时间保留。专业核对需要针对新版本重新完成。</p><ul>{record.fields.map(field => <li key={field.field_id}>{fieldLabel(field)} · {timeLabel(field.medical_time)}</li>)}</ul></details>}
    </div>
  </UI2FamilyFrame>;
}

export { UI2HistoryPage as HistoryPage } from './UI2HistoryPage';
