import { eventTimeLabel } from '../../components/recordTimePresentation';
import { checkRequestGuard, type RequestGuard } from '../../api/client';
import { RecordRecoveryPanel } from '../../components/RecordRecoveryPanel';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { Plus } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { useAccess } from '../../state/access';
import { recognizeTherapy, type TherapyCandidate, type FieldConfigView } from '../../api/endpoints';
import { useResource } from '../../api/useResource';
import { MedicalTimeInput, emptyTime, timePayload } from '../../components/MedicalTimeInput';
import { UploadedPhotos, type UploadedPhoto } from '../../components/UploadedPhotos';
import { useRecordSubmission } from '../../state/useRecordSubmission';
import { useFormDraft } from '../../state/useFormDraft';
import { RecentMaterials } from '../../components/RecentMaterials';
import { RegistrationPatient, RegistrationSteps } from './RichRegistration';

const itemFieldSpecs = [
  { field_code: 'dose', label: '单次剂量', value_type: 'decimal' },
  { field_code: 'dose_unit', label: '剂量单位', value_type: 'text' },
  { field_code: 'concentration_text', label: '制剂浓度', value_type: 'text' },
  { field_code: 'route_text', label: '给药途径', value_type: 'text' },
  { field_code: 'frequency_text', label: '原文频次', value_type: 'text' },
  { field_code: 'interval_value', label: '间隔数值', value_type: 'decimal' },
  { field_code: 'interval_unit', label: '间隔单位', value_type: 'categorical' },
] as const;

export function TherapiesPage() {
  const access = useAccess();
  const { patientId, csrfToken } = access;
  const navigate = useNavigate();
  const [medication, setMedication] = useState('');
  const [status, setStatus] = useState('无法确定');
  const [note, setNote] = useState('');
  const [itemDetails, setItemDetails] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const [startTime, setStartTime] = useState(emptyTime);
  const [endTime, setEndTime] = useState(emptyTime);
  const [photos, setPhotos] = useState<UploadedPhoto[]>([]);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [source, setSource] = useState('self_report');
  const [sourceReference, setSourceReference] = useState('');
  const [sourceConfirmed, setSourceConfirmed] = useState(false);
  const [candidates, setCandidates] = useState<Array<TherapyCandidate & { photo_id: string }>>([]);
  const [selectedSource, setSelectedSource] = useState<{ photo_id: string; source_text: string } | null>(null);
  const relation = access.relations.find(item => item.patient_id === patientId);
  const canWrite = Boolean(access.principalId && patientId && csrfToken && !access.loading && !access.error
    && relation?.status === 'active' && relation.actions.includes('write_record')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now()));
  const config = useResource<FieldConfigView>(canWrite && relation?.actions.includes('read_raw') ? `/api/patients/${patientId}/field-config?kind=therapy` : null);
  if (config.data && (config.data.patient_id !== patientId || config.data.kind !== 'therapy')) throw new Error('治疗配置与当前患儿或表单不一致');
  const writable = config.data?.state === 'available' ? config.data.fields.filter(field => field.writable) : [];
  const allowed = (code: string, type: string) => writable.some(field => field.field_code === code && field.value_type === type);
  const medicationAllowed = allowed('medication_name', 'text');
  const noteAllowed = allowed('therapy_note', 'text');
  const statusAllowed = allowed('therapy_status', 'categorical');
  const startAllowed = allowed('therapy_start', 'time');
  const endAllowed = allowed('therapy_end', 'time');
  const sourceAllowed = allowed('therapy_source', 'text');
  const canUpload = canWrite && !config.loading && !config.error && medicationAllowed && Boolean(relation?.actions.includes('read_file'));
  const canDraft = canWrite && Boolean(relation?.actions.includes('manage_draft'));
  const draft = useFormDraft('therapy', { medication, status, note, itemDetails, startTime, endTime, photos, source, sourceReference, candidates, selectedSource }, value => {
    setMedication(value.medication); setStatus(value.status); setNote(value.note); setStartTime(value.startTime); setEndTime(value.endTime);
    setItemDetails(value.itemDetails ?? {});
    setPhotos(value.photos); setSource(value.source); setSourceReference(value.sourceReference); setSourceConfirmed(false);
    setCandidates(value.candidates ?? []); setSelectedSource(value.selectedSource ?? null);
  }, canDraft && !saving && !photoBusy);
  const submission = useRecordSubmission(draft, 'therapy');
  const inputBlocked = submission.pending || !canWrite || config.loading || Boolean(config.error) || config.data?.state !== 'available'
    || photoBusy || saving || draft.restoring || draft.saving;
  const invalidTime = (startTime.precision !== 'unknown' && !startTime.value) || (endTime.precision !== 'unknown' && !endTime.value)
    || Boolean(startTime.value && endTime.value && startTime.precision === endTime.precision && startTime.value > endTime.value);
  const statusValue = ({ '持续使用': 'ongoing', '已结束': 'ended', '尚未使用': 'not_started', '无法确定': null } as Record<string, string | null>)[status];
  const statusValues = writable.find(field => field.field_code === 'therapy_status')?.allowed_values;
  const itemFields = itemFieldSpecs.filter(field => allowed(field.field_code, field.value_type)
    && ((field.field_code !== 'dose' && field.field_code !== 'dose_unit') || (allowed('dose', 'decimal') && allowed('dose_unit', 'text')))
    && ((field.field_code !== 'interval_value' && field.field_code !== 'interval_unit') || (allowed('interval_value', 'decimal') && allowed('interval_unit', 'categorical'))));
  const incompleteDose = Boolean(itemDetails.dose?.trim()) !== Boolean(itemDetails.dose_unit?.trim());
  const incompleteInterval = Boolean(itemDetails.interval_value?.trim()) !== Boolean(itemDetails.interval_unit?.trim());
  const submissionBlocked = inputBlocked || !medicationAllowed || !statusAllowed || !startAllowed || !endAllowed || !sourceAllowed
    || (Boolean(note.trim()) && !noteAllowed) || !sourceConfirmed || !medication.trim() || invalidTime || incompleteDose || incompleteInterval
    || statusValue === undefined || (statusValue !== null && !statusValues?.includes(statusValue))
    || (source === 'report_transcription' && !sourceReference.trim()) || (selectedSource !== null && !selectedSource.source_text.trim());

  function clearPhotoAdoption() {
    setMedication(''); setNote(''); setSource('self_report'); setSourceReference('');
    setSelectedSource(null); setSourceConfirmed(false); setItemDetails({});
    setStatus('无法确定'); setStartTime(emptyTime); setEndTime(emptyTime);
  }

  function changePhotos(value: UploadedPhoto[]) {
    const retained = new Set(value.map(photo => photo.file_id));
    setPhotos(value); setSourceConfirmed(false);
    setCandidates(current => current.filter(item => retained.has(item.photo_id)));
    if (selectedSource && !retained.has(selectedSource.photo_id)) {
      clearPhotoAdoption(); setMessage('已移除原件来源，请按家庭自报重新填写原药名与说明。');
    }
  }

  async function recognize(photo: UploadedPhoto, operation: RequestGuard) {
    checkRequestGuard(operation);
    if (!canUpload || !access.hasCurrentAccess(access.principalId, patientId, access.scopeVersion)) throw new Error('请核验当前患儿的原件与登记权限');
    setSourceConfirmed(false);
    const result = await recognizeTherapy(photo.file_id, csrfToken ?? undefined, operation);
    checkRequestGuard(operation);
    setCandidates(current => [...current.filter(item => item.photo_id !== photo.file_id), ...result.candidates.items.map(item => ({ ...item, photo_id: photo.file_id }))]);
    if (selectedSource?.photo_id === photo.file_id) {
      clearPhotoAdoption(); setMessage('本页识别候选已更新，请重新选择原文并确认资料出处。');
    } else setMessage(result.candidates.items.length ? '请选择本次登记的药物原文，并确认实际使用情况。' : '本页未取得药物候选，可以根据实际家庭记录补充。');
    return result.candidates.items.length;
  }

  function adopt(item: TherapyCandidate & { photo_id: string }) {
    if (inputBlocked || !medicationAllowed || !noteAllowed || !sourceAllowed) throw new Error('当前未取得药物原文采用权限');
    const photo = photos.find(value => value.file_id === item.photo_id);
    if (!photo || !item.name.trim() || !item.source_text.trim()) throw new Error('药物候选缺少当前原件或来源原文');
    setItemDetails({});
    setMedication(item.name); setNote(item.source_text); setSelectedSource({ photo_id: item.photo_id, source_text: item.source_text });
    setSource('report_transcription'); setSourceReference(photo.name); setStatus('无法确定'); setStartTime(emptyTime); setEndTime(emptyTime);
    setSourceConfirmed(false); setMessage('已采用药物原文，使用状态与起止时间需要本人确认。');
  }

  const submit = async () => {
    if (!patientId || (!submission.pending && submissionBlocked)) throw new Error('请完成治疗资料核对并核验登记权限');
    const fields: Array<Record<string, unknown>> = [
      { field_code: 'medication_name', value_type: 'text', value_state: 'known', raw_text: medication, sample_time_origin: 'unknown' },
      { field_code: 'therapy_status', value_type: 'categorical', value_state: statusValue === null ? 'unknown' : 'known', categorical_value: statusValue, sample_time_origin: 'unknown' },
      { field_code: 'therapy_source', value_type: 'text', value_state: sourceReference.trim() ? 'known' : 'unknown', raw_text: sourceReference.trim() ? sourceReference : null, sample_time_origin: 'unknown' },
    ];
    if (note.trim()) fields.push({ field_code: 'therapy_note', value_type: 'text', value_state: 'known', raw_text: note, sample_time_origin: 'unknown' });
    for (const [fieldCode, time] of [['therapy_start', startTime], ['therapy_end', endTime]] as const) {
      fields.push({ field_code: fieldCode, value_type: 'time', value_state: time.precision === 'unknown' ? 'unknown' : 'known', medical_time: timePayload(time), sample_time_origin: time.precision === 'unknown' ? 'unknown' : 'therapy' });
    }
    for (const field of itemFields) {
      const value = itemDetails[field.field_code] ?? '';
      const known = value.trim().length > 0;
      fields.push({
        field_code: field.field_code,
        value_type: field.value_type,
        value_state: known ? 'known' : 'unknown',
        raw_numeric: field.value_type === 'decimal' && known ? value : null,
        raw_text: field.value_type === 'text' && known ? value : null,
        categorical_value: field.value_type === 'categorical' && known ? value : null,
        sample_time_origin: 'unknown',
      });
    }
    if (selectedSource) {
      if (!photos.some(photo => photo.file_id === selectedSource.photo_id)) throw new Error('药物原文缺少当前原件关联');
      for (const field of fields) if (field.field_code === 'medication_name' || field.field_code === 'therapy_note') {
        field.source_file_id = selectedSource.photo_id; field.source_text = selectedSource.source_text;
      }
    }
    const payload = { kind: 'therapy', family_confirmation: true, field_config_version: config.data?.config_version, capture_method: selectedSource ? 'ocr_confirmed' : 'manual', origin_type: source, fields, file_ids: photos.map(photo => photo.file_id) };
    setError(null); setSaving(true);
    await submission.submit(payload).then(() => {
      navigate('/records', { replace: true });
    }, failure => { if (!(failure instanceof Error)) throw failure; setError(failure.message); setSaving(false); });
  };

  return <UI2FamilyFrame screenId="UI2-04" title="治疗资料" note="原药名、实际使用状态和时间分别确认。" backTo="/home">
    <RegistrationPatient displayId={relation?.display_id} onClick={() => navigate('/associations')} />
    <UI2ReadState />
    {access.principalId && !canWrite && <section className="status-panel permission" role="status"><h2>当前未取得登记权限</h2><p>登记前需要核验当前患儿的写入权限。</p></section>}
    {canWrite && config.loading && <p role="status">正在读取治疗字段权限</p>}
    {config.error && <section className="status-panel error" role="alert"><h2>治疗字段读取失败</h2><p>{config.error}</p><button className="text-action" onClick={config.reload}>重新读取治疗字段</button></section>}
    {canWrite && !config.loading && !config.error && config.data?.state !== 'available' && <p role="status">当前未取得可用的治疗字段配置。</p>}
    <div className="registration-photo-flow"><UploadedPhotos ui2 therapy photos={photos} onChange={changePhotos} onUploaded={recognize} busy={!canUpload || inputBlocked} onBusyChange={setPhotoBusy} /></div>
    {photos.length > 0 && <RegistrationSteps current={3} />}
    <RecordRecoveryPanel submission={submission} onRecovered={() => navigate('/records', { replace: true })} />
    <fieldset className="registration-form" disabled={inputBlocked} style={{ minWidth: 0, margin: 0, padding: 0, border: 0 }}>
    <section className="card form-card registration-therapy-information"><h2>治疗信息</h2>
        <label className="field"><span>原药名</span><input disabled={!medicationAllowed} placeholder="按原文填写" value={medication} onChange={event => { setMedication(event.target.value); setSourceConfirmed(false); }} /></label>
        <label className="field"><span>实际使用状态</span><select aria-label="实际使用状态" disabled={!statusAllowed} value={status} onChange={event => { setStatus(event.target.value); setSourceConfirmed(false); }}><option>持续使用</option><option>已结束</option><option>无法确定</option>{(statusValues?.includes('not_started') || status === '尚未使用') && <option>尚未使用</option>}</select></label>
        <details className="registration-inner-disclosure"><summary>使用时间与补充说明<Plus size={19} /></summary><div className="registration-detail-body"><div className="field-grid"><MedicalTimeInput ui2 precisionDetails disabled={!startAllowed} label="开始日期" value={startTime} onChange={value => { setStartTime(value); setSourceConfirmed(false); }} /><MedicalTimeInput ui2 precisionDetails disabled={!endAllowed} label="结束日期" value={endTime} onChange={value => { setEndTime(value); setSourceConfirmed(false); }} /></div>
        <label className="field"><span>资料说明</span><textarea disabled={!noteAllowed} placeholder="保留原文与实际使用情况" value={note} onChange={event => { setNote(event.target.value); setSourceConfirmed(false); }} /></label>
        {itemFields.length > 0 && <section aria-label="用药分项" className="registration-medication-items"><h3>用药分项</h3><p>只填写当前资料中可确认的内容，未知项目留空。</p>
          {itemFields.map(field => field.value_type === 'categorical'
            ? <label className="field" key={field.field_code}><span>{field.label}</span><select aria-label={field.label} disabled={inputBlocked} value={itemDetails[field.field_code] ?? ''} onChange={event => { setItemDetails(current => ({ ...current, [field.field_code]: event.target.value })); setSourceConfirmed(false); }}><option value="">无法确定</option>{writable.find(item => item.field_code === field.field_code)?.allowed_values.map(value => <option key={value} value={value}>{value}</option>)}</select></label>
            : <label className="field" key={field.field_code}><span>{field.label}</span><input aria-label={field.label} disabled={inputBlocked} type={field.value_type === 'decimal' ? 'number' : 'text'} inputMode={field.value_type === 'decimal' ? 'decimal' : undefined} step={field.value_type === 'decimal' ? 'any' : undefined} value={itemDetails[field.field_code] ?? ''} onChange={event => { setItemDetails(current => ({ ...current, [field.field_code]: event.target.value })); setSourceConfirmed(false); }} /></label>)}
          {(incompleteDose || incompleteInterval) && <p role="alert">剂量及单位、间隔数值及单位需要成对填写，未知内容请同时留空。</p>}
        </section>}
        </div></details>
        {invalidTime && <p role="alert">请完整填写所选时间，结束时间不能早于开始时间。</p>}
      </section>
      {candidates.length > 0 && <section id="recognition-candidates" className="card form-card"><h2>治疗识别候选</h2><p>每次记录一种药物。请选择本次登记的原文，并核对实际使用情况。</p>
        {candidates.map((item, index) => <section key={`${item.photo_id}-${index}`} aria-label={`治疗候选 ${index + 1}`}><h3>{item.name}</h3><p>{item.source_text}</p><small>来自第 {photos.findIndex(photo => photo.file_id === item.photo_id) + 1} 页</small><button className="text-action" disabled={!medicationAllowed || !noteAllowed || !sourceAllowed} onClick={() => adopt(item)}>采用此药物原文</button></section>)}
        <small>处方内容不代表已经使用，请本人确认使用状态与时间。</small></section>}
      <details className="registration-disclosure"><summary>资料出处与确认<Plus size={19} /></summary><section className="form-card">
        <label className="field"><span>资料来源</span><select aria-label="资料来源" disabled={!sourceAllowed} value={source} onChange={event => { setSource(event.target.value); setSelectedSource(null); setSourceConfirmed(false); }}><option value="self_report">监护人自报</option><option value="report_transcription">处方或治疗资料转录</option></select></label>
        <label className="field"><span>来源描述或引用</span><input disabled={!sourceAllowed} value={sourceReference} onChange={event => { setSourceReference(event.target.value); setSourceConfirmed(false); }} /></label>
        {selectedSource && <p>采用的来源原文：{selectedSource.source_text}</p>}
        <small>无照片时可按家庭记录补充并保留自报属性。药物专业分类由授权人员核对，本功能不产生用药建议。</small>
      </section></details>
        <label className="checkbox-row registration-confirmation"><input type="checkbox" checked={sourceConfirmed} onChange={event => setSourceConfirmed(event.target.checked)} />我已核对患儿和资料来源，并确认实际使用状态与独立起止时间；不确定项目保持未知</label>
    </fieldset>
    {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
    <button className="button" disabled={submission.pending ? !canWrite || saving : submissionBlocked} onClick={() => void submit()}>{saving ? '正在保存资料' : submission.pending ? '重试本次保存' : '确认并保存资料'}</button>
    <details className="registration-disclosure registration-recent"><summary>草稿与近期资料<Plus size={19} /></summary>
      <button className="button secondary" disabled={!canDraft || inputBlocked} onClick={() => void draft.save()}>{draft.saving ? '正在保存草稿' : '保存当前草稿'}</button>
      {canWrite && !medication && <RecentMaterials kind="therapy" />}
    </details>
    {draft.restoring && <p role="status">正在恢复草稿</p>}{draft.error && <p role="alert">{draft.error}</p>}
    {draft.draft && <p role="status">草稿保存时间：{eventTimeLabel(draft.draft.saved_at)} · 版本 {draft.draft.version}{draft.dirty ? ' · 当前修改尚未保存' : ''}</p>}
  </UI2FamilyFrame>;
}
