import { eventTimeLabel } from '../../components/recordTimePresentation';
import { checkRequestGuard, type RequestGuard } from '../../api/client';
import { RecordRecoveryPanel } from '../../components/RecordRecoveryPanel';
import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { ChevronRight, Images, Layers, Plus, ScanLine } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { PrivateSourceGallery } from '../../components/PrivateSourceGallery';
import { MedicalTimeInput, emptyTime, timePayload } from '../../components/MedicalTimeInput';
import { UploadedPhotos, type UploadedPhoto } from '../../components/UploadedPhotos';
import { useAccess } from '../../state/access';
import { recognizeReport, type FieldConfigView } from '../../api/endpoints';
import { useResource } from '../../api/useResource';
import { useRecordSubmission } from '../../state/useRecordSubmission';
import { useFormDraft } from '../../state/useFormDraft';
import { reportConfirmationSource } from './reportConfirmationSource';
import { reportFieldValue, type ReportItemInput } from './reportFieldValue';
import { ReportTimeSuggestion } from '../../components/ReportTimeSuggestion';
import { DictionarySearchField } from '../../components/DictionarySearchField';
import { RegistrationPatient, RegistrationSteps } from './RichRegistration';

const reportPaths = ['/register/reports', '/register/reports/confirm'];

export function ReportsPage() {
  const access = useAccess();
  const { patientId, csrfToken } = access;
  const navigate = useNavigate();
  const location = useLocation();
  const [message, setMessage] = useState('');
  const [candidates, setCandidates] = useState<ReportItemInput[]>([]);
  const [recognizing, setRecognizing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [photos, setPhotos] = useState<UploadedPhoto[]>([]);
  const [organization, setOrganization] = useState('');
  const [reportRef, setReportRef] = useState('');
  const [sampleTime, setSampleTime] = useState(emptyTime);
  const [resultTime, setResultTime] = useState(emptyTime);
  const [reportStatus, setReportStatus] = useState('unknown');
  const relation = access.relations.find(item => item.patient_id === patientId);
  const canWrite = Boolean(access.principalId && patientId && csrfToken && !access.loading && !access.error
    && relation?.status === 'active' && relation.actions.includes('write_record')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now()));
  const canUpload = canWrite && Boolean(relation?.actions.includes('read_file'));
  const canDraft = canWrite && Boolean(relation?.actions.includes('manage_draft'));
  const config = useResource<FieldConfigView>(canWrite ? `/api/patients/${patientId}/field-config?kind=report` : null);
  const reportAllowed = config.data?.state === 'available' && config.data.fields.some(field => field.field_code === 'report_item' && field.writable);
  const draft = useFormDraft('report', { candidates, photos, organization, reportRef, sampleTime, resultTime, reportStatus }, value => {
    setCandidates(value.candidates); setPhotos(value.photos); setOrganization(value.organization); setReportRef(value.reportRef);
    setSampleTime(value.sampleTime); setResultTime(value.resultTime); setReportStatus(value.reportStatus); setConfirmed(false);
  }, canDraft && !recognizing && !saving, reportPaths);
  const submission = useRecordSubmission(draft, 'report');
  const inputBlocked = submission.pending || !canWrite || recognizing || saving || draft.restoring || draft.saving;
  const incomplete = (sampleTime.precision !== 'unknown' && !sampleTime.value) || (resultTime.precision !== 'unknown' && !resultTime.value)
    || candidates.some(item => item.sampleTime && item.sampleTime.precision !== 'unknown' && !item.sampleTime.value);
  const confirmationSource = reportConfirmationSource(candidates, photos);
  const submissionBlocked = confirmationSource.incompleteImageSource || inputBlocked || !reportAllowed || !confirmed || !candidates.length || incomplete
    || candidates.some(item => !item.name.trim() || Boolean(item.comparator && !['=', '<', '≤', '<=', '>', '≥', '>='].includes(item.comparator))
      || Boolean(item.photo_id && !item.source_text?.trim()));

  const onPhotoUploaded = async (photo: UploadedPhoto, operation: RequestGuard) => {
    checkRequestGuard(operation);
    if (!canUpload || !access.hasCurrentAccess(access.principalId, patientId, access.scopeVersion)) throw new Error('请核验当前患儿的原件与登记权限');
    setError(null); setConfirmed(false);
    const result = await recognizeReport(photo.file_id, csrfToken ?? undefined, operation);
    checkRequestGuard(operation);
    const list = result.candidates.items;
    setCandidates(current => [...current.filter(item => item.photo_id !== photo.file_id), ...list.map(item => ({ ...item, photo_id: photo.file_id, sampleTime: emptyTime,
      ...(result.candidates.schema_version === 'report-recognition-3' ? { timeCandidates: {
        item: item.sample_time ?? null, reportSample: result.candidates.sample_time ?? null,
        reportResult: result.candidates.result_time ?? null, sourceText: result.candidates.source_text ?? '' } } : {}) }))]);
    setMessage(list.length > 0 ? `已识别 ${list.length} 个检验项目，请核对后保存。` : '未识别到检验项目，可手动填写。');
    return list.length;
  };

  function changePhotos(next: UploadedPhoto[]) {
    setPhotos(next);
    const retained = new Set(next.map(photo => photo.file_id));
    setCandidates(current => current.filter(item => !item.photo_id || retained.has(item.photo_id)));
    setConfirmed(false);
  }

  function changeCandidate(index: number, value: Partial<ReportItemInput>) {
    if (inputBlocked) return;
    setCandidates(current => current.map((item, position) => position === index ? { ...item, ...value } : item));
    setConfirmed(false);
  }

  const submit = async () => {
    if (!patientId || (!submission.pending && submissionBlocked)) throw new Error('请完成逐项核对并核验登记权限');
    const fields = candidates.map(reportFieldValue);
    const payload = { kind: 'report', field_config_version: config.data?.config_version, capture_method: candidates.some(item => item.photo_id) ? 'ocr_confirmed' : 'manual', fields,
      file_ids: photos.map(photo => photo.file_id), source_organization: organization || null,
      report: { external_ref: reportRef || null, report_status: reportStatus, sample_time: timePayload(sampleTime), result_time: timePayload(resultTime) } };
    setError(null); setSaving(true);
    await submission.submit(payload).then(() => {
      navigate('/records', { replace: true });
    }, failure => { if (!(failure instanceof Error)) throw failure; setError(failure.message); setSaving(false); });
  };

  const review = <>
    <UI2ReadState loading={config.loading} error={config.error ? new Error(config.error) : null} onRetry={config.reload} />
    {config.data && config.data.state !== 'available' && <section className="status-panel permission" role="status"><h2>检验项目配置暂时不可用</h2><p>{config.data.reason_code}</p></section>}
    <RecordRecoveryPanel submission={submission} onRecovered={() => navigate('/records', { replace: true })} />
    <fieldset className="registration-form" disabled={inputBlocked} style={{ minWidth: 0, margin: 0, padding: 0, border: 0 }}>
      <section id="recognition-candidates" className="card form-card registration-candidates"><div className="section-heading"><h2>报告指标与识别候选</h2><span className="muted">{candidates.length} 项</span></div>
        {recognizing && <p role="status">正在识别报告，请稍候。</p>}
        {!recognizing && candidates.length === 0 && <p>{message || '选择照片后请求识别，也可以按实际资料手动填写。'}</p>}
        {candidates.map((item, index) => <section className="registration-candidate form-card" key={index} aria-label={`检验项目 ${index + 1}`}><h3>项目 {index + 1}</h3>
          {item.photo_id && <><PrivateSourceGallery files={[{ file_id: item.photo_id, ordinal: photos.findIndex(photo => photo.file_id === item.photo_id) + 1 }]} /><p>来源原文：{item.source_text}</p></>}
          {(['name', 'comparator', 'value', 'unit', 'reference'] as const).map(key => <label className="field" key={key}><span>{({ name: '原项目名称', comparator: '原报告比较符', value: '原始数值', unit: '原始单位', reference: '参考区间原文' })[key]}</span><input value={item[key] ?? ''} onChange={event => changeCandidate(index, { [key]: event.target.value, ...(key === 'name' ? { dictionaryEntry: null } : {}) })} /></label>)}
          <DictionarySearchField category="laboratory" disabled={inputBlocked} selected={item.dictionaryEntry ?? null} onSelect={value => changeCandidate(index, { dictionaryEntry: value })} />
          <label className="field"><span>检测方法</span><input value={item.method ?? ''} onChange={event => changeCandidate(index, { method: event.target.value })} /></label>
          <ReportTimeSuggestion label={`项目 ${index + 1} 采样时间`} candidate={item.timeCandidates?.item}
            onAdopt={value => changeCandidate(index, { sampleTime: value })} />
          <MedicalTimeInput ui2 label={`项目 ${index + 1} 采样时间`} value={item.sampleTime ?? emptyTime} onChange={value => changeCandidate(index, { sampleTime: value })} />
          <button type="button" className="text-action" onClick={() => { setCandidates(current => current.filter((_, position) => position !== index)); setConfirmed(false); }}>移除此项目</button>
        </section>)}
        <button type="button" className="button secondary registration-add-item" onClick={() => { setCandidates(current => [...current, { name: '', value: null, comparator: null, unit: null, reference: null, sampleTime: emptyTime }]); setConfirmed(false); }}><Plus size={23} />手动添加检验项目</button>
      </section>
      <details className="registration-disclosure"><summary>时间与报告来源<Plus size={19} /></summary><section className="form-card">
        <label className="field"><span>报告标识或来源引用</span><input value={reportRef} onChange={event => { setReportRef(event.target.value); setConfirmed(false); }} /></label>
        <label className="field"><span>报告机构</span><input value={organization} onChange={event => { setOrganization(event.target.value); setConfirmed(false); }} /></label>
        <label className="field"><span>报告结果状态</span><select aria-label="报告结果状态" value={reportStatus} onChange={event => { setReportStatus(event.target.value); setConfirmed(false); }}><option value="unknown">无法确定</option><option value="preliminary">初步结果</option><option value="final">最终结果</option><option value="corrected">更正结果</option></select></label>
        <MedicalTimeInput ui2 label="报告记载的采样时间" value={sampleTime} onChange={value => { setSampleTime(value); setConfirmed(false); }} />
        <MedicalTimeInput ui2 label="报告出具时间" value={resultTime} onChange={value => { setResultTime(value); setConfirmed(false); }} />
        <small>以上时间单独保存为报告资料；每个项目分别确认自己的采样时间。</small>
        {candidates.filter((item, index, all) => item.photo_id && item.timeCandidates
          && all.findIndex(other => other.photo_id === item.photo_id) === index).map(item => <div key={item.photo_id}>
          <p>报告时间候选出处：{item.timeCandidates!.sourceText}</p>
          <ReportTimeSuggestion label="报告采样时间" candidate={item.timeCandidates!.reportSample}
            onAdopt={value => { setSampleTime(value); setConfirmed(false); }} />
          <ReportTimeSuggestion label="报告出具时间" candidate={item.timeCandidates!.reportResult}
            onAdopt={value => { setResultTime(value); setConfirmed(false); }} />
        </div>)}
      </section></details>

      {candidates.length > 0 && <><label className="checkbox-row registration-confirmation"><input type="checkbox" checked={confirmed} disabled={confirmationSource.incompleteImageSource} aria-describedby="report-confirmation-source-note" onChange={event => setConfirmed(event.target.checked)} />{confirmationSource.label}</label><p id="report-confirmation-source-note" className="muted">{confirmationSource.note}</p></>}
    </fieldset>
    <section className="card registration-tools"><button className="list-row" onClick={() => navigate('/archives/grouping')}><span className="row-text"><strong>文书与就诊归组</strong></span><ChevronRight size={17} /></button>
      <button className="list-row" onClick={() => navigate('/archives/duplicates')}><span className="row-text"><strong>处理重复资料</strong></span><ChevronRight size={17} /></button></section>
    {error && <p role="alert">{error}</p>}
    <button className="button" disabled={submission.pending ? !canWrite || saving : submissionBlocked} onClick={() => void submit()}>{saving ? '正在保存' : submission.pending ? '重试本次保存' : '确认并保存'}</button>
    <button className="text-action center" disabled={inputBlocked || !canDraft} onClick={() => void draft.save()}>保存当前草稿</button>
  </>;

  if (location.pathname === '/register/reports/confirm') return <UI2FamilyFrame screenId="REPORT-CONFIRMATION" title="拍照添加报告" note="逐项医学时间、语境与出处不能被共同时间覆盖。" backTo="/register/reports">
    <RegistrationPatient displayId={relation?.display_id} onClick={() => navigate('/associations')} />
    {access.principalId && !canWrite && <div className="note amber" role="status"><p>当前未取得登记权限。登记前需要核验当前患儿的写入权限。</p></div>}
    <RegistrationSteps current={3} />
    <section className="card form-card registration-originals"><div className="section-heading"><h2>原始照片</h2><button className="text-action" disabled={inputBlocked} onClick={() => navigate({ pathname: '/register/reports', search: location.search })}>添加照片</button></div>
      {photos.length > 0 ? <PrivateSourceGallery files={photos.map((photo, index) => ({ file_id: photo.file_id, ordinal: index + 1 }))} /> : <p>对照原图填写，尚未登记的内容保持空白。</p>}
    </section>
    {review}
    {draft.restoring && <p role="status">正在恢复草稿</p>}{draft.error && <p role="alert">{draft.error}</p>}
  </UI2FamilyFrame>;

  return <UI2FamilyFrame screenId="UI2-03" title="拍照添加报告" note="原件、识别和本人确认分别呈现。" backTo="/home">
    <RegistrationPatient displayId={relation?.display_id} onClick={() => navigate('/associations')} />
    <UI2ReadState />
    <RecordRecoveryPanel submission={submission} onRecovered={() => navigate('/records', { replace: true })} />
    {access.principalId && !canWrite && <section className="status-panel permission" role="status"><h2>当前未取得登记权限</h2><p>登记前需要核验当前患儿的写入权限。</p></section>}
    <div className="registration-photo-flow"><UploadedPhotos ui2 photos={photos} onChange={changePhotos} onUploaded={onPhotoUploaded} busy={!canUpload || inputBlocked} onBusyChange={setRecognizing} /></div>
    {photos.length > 0 && <RegistrationSteps current={2} />}
    {submission.pending && <section className="status-panel" role="status"><p>{error || '正在核对本次保存结果。'}</p><button className="button" disabled={!canWrite || saving} onClick={() => void submit()}>{saving ? '正在保存' : '重试本次保存'}</button></section>}
    <section className="card registration-overview"><div className="section-heading"><h2>本次报告资料</h2><Images size={24} /></div><p>{photos.length > 0 ? `已保留 ${photos.length} 份原件，${candidates.length} 个项目待逐项核对。` : '添加完整报告原件，或按实际资料手动登记。'}</p><button className="list-row" disabled={inputBlocked} onClick={() => navigate({ pathname: '/register/reports/confirm', search: location.search })}><span className="row-icon"><ScanLine size={30} /></span><span className="row-text"><strong>逐项确认内容</strong><small>每个项目保留自己的采样时间</small></span><ChevronRight size={17} /></button>
      <button className="list-row" onClick={() => navigate('/archives/batches/new')}><span className="row-icon"><Layers size={30} /></span><span className="row-text"><strong>添加长期资料</strong><small>多次住院与门诊可分批整理</small></span><ChevronRight size={17} /></button></section>
    <button className="text-action center" disabled={inputBlocked || !canDraft} onClick={() => void draft.save()}>{draft.saving ? '正在保存草稿' : '保存当前草稿'}</button>
    {draft.restoring && <p role="status">正在恢复草稿</p>}{draft.error && <p role="alert">{draft.error}</p>}
    {draft.draft && <p role="status">草稿保存时间：{eventTimeLabel(draft.draft.saved_at)} · 版本 {draft.draft.version}{draft.dirty ? ' · 当前修改尚未保存' : ''}</p>}
  </UI2FamilyFrame>;
}
