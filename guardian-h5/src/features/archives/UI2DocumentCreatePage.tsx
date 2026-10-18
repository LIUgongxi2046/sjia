import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ArrowDown, ArrowUp, ArrowDown as ChevronDown, ShieldCheck } from '../../components/ThreeDIcon';
import { UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useEndpointAccess } from '../../state/useEndpointAccess';
import { ApiError } from '../../api/client';
import { readBatch } from '../../api/archiveBatches';
import { readCandidates, readPageOriginal } from '../../api/archiveFacts';
import { documentPayloadMatches, findDocumentRequest, prepareDocumentRequest, readArchiveDocument,
  readDocumentInputs, readDocumentRequest, retryDocumentRequest, submitDocumentRequest } from '../../api/archiveDocuments';
import { validateApiValue } from '../../api/schema';
import { MedicalTimeInput, emptyTime, timePayload, type TimeInput } from '../../components/MedicalTimeInput';
import type { IntentView, MedicalTimeInput as MedicalTimeValue } from '../../../../frontend-design/types';
import type { BatchView, DocumentInput, DocumentIntentInput, DocumentType, DocumentView,
  DocumentEpisodeOption, FactCandidateSet, PageView, ProposedEpisode, RequestView } from '../../../../frontend-design/v2-types';

const types: [DocumentType, string][] = [['laboratory', '检验报告'], ['inpatient', '住院病历'], ['discharge', '出院记录'],
  ['outpatient', '门诊记录'], ['therapy', '治疗资料'], ['imaging_report', '影像报告文字'], ['imaging_asset', '影像附件'], ['unknown', '待分类']];
const episodeTypes: [ProposedEpisode['kind'], string][] = [['inpatient', '住院'], ['outpatient', '门诊'], ['unknown', '未知']];
const unknownEpisode: ProposedEpisode = { kind: 'unknown', start_time: null, end_time: null, description: null };
function confirmedTime(value: TimeInput): MedicalTimeValue | null {
  if (value.precision === 'unknown' || !value.value) return null;
  return validateApiValue<MedicalTimeValue>('protocol.schema.json#/$defs/MedicalTimeInput', timePayload(value));
}


function selectableOriginal(page: PageView, manual = false) {
  if (page.file_status !== 'available') return false;
  if (manual) return page.status === 'uploaded' && page.task === null && page.candidate_set_id === null && page.attempts.length === 0;
  if (page.status === 'failed') return page.task?.status === 'failed'
    && page.attempts.every(attempt => !['running', 'uncertain'].includes(attempt.status) && attempt.finished_at !== null);
  return ['uploaded', 'awaiting_confirmation', 'confirmed', 'archived'].includes(page.status);
}

export function UI2DocumentCreatePage() {
  const access = useAccess(), [params] = useSearchParams();
  return <CreateContent key={JSON.stringify([access.principalId, access.patientId, params.get('page'), params.get('batch')])} />;
}

function CreateContent() {
  const boundary = useEndpointAccess();
  const access = useAccess(), navigate = useNavigate(), [params, setParams] = useSearchParams();
  const pageId = params.get('page'), batchId = params.get('batch'), requestId = params.get('request'), clientId = params.get('client_request');
  const [batch, setBatch] = useState<BatchView | null>(null), [candidate, setCandidate] = useState<FactCandidateSet | null>(null);
  const [episodeOptions, setEpisodeOptions] = useState<DocumentEpisodeOption[]>([]);
  const [form, setForm] = useState<DocumentInput | null>(null), [documented, setDocumented] = useState<TimeInput>(emptyTime);
  const [identity, setIdentity] = useState<DocumentIntentInput | null>(null), [intent, setIntent] = useState<IntentView | null>(null);
  const [request, setRequest] = useState<RequestView | null>(null), [saved, setSaved] = useState<DocumentView | null>(null);
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState<Error | null>(null);
  const [reload, setReload] = useState(0), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const pending = useRef<AbortController | null>(null), mounted = useRef(true), running = useRef(false), restored = useRef('');
  const preparingNew = useRef<string | null>(null), rejectedRequest = useRef<string | null>(null);
  const ready = !!access.principalId && !!access.patientId && !access.loading && !access.error && visible;
  const duplicatePage = ready && error instanceof ApiError && error.status === 409 && error.code === 'DOCUMENT_COPY_CONFIRMATION_REQUIRED';
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const manual = batch?.intake_mode === 'manual';
  const canWrite = ready && !!form && relation?.status === 'active' && relation.actions.includes('write_record');
  const batchBlockedReason = batch?.status === 'receiving' && !manual ? '批次尚未进入可归组状态，暂不能提交新的归组。原始页面仍可查看；这不表示识别已完成。'
    : batch?.status === 'cancelled' ? '该批次已取消，不能提交新的归组。原始页面仍可按现有权限查看。' : null;
  const invalidSelection = !!form?.page_ids.some(id => !batch?.pages.some(page => page.page_id === id && selectableOriginal(page, manual)));
  const newSaveReason = batchBlockedReason ?? (invalidSelection ? '所选原页尚不具备手动归组条件：需要完整可用原件，且页面不处于排队、处理中、结果不确定或取消状态。识别失败页还须核验处理已终止。' : null);
  const edit = canWrite && !batchBlockedReason && !busy && !identity && !requestId && !clientId && !saved;
  const target = `/patients/${access.patientId}/archive-documents`;

  function clear() { setBatch(null); setCandidate(null); setEpisodeOptions([]); setForm(null); setIdentity(null); setIntent(null); setRequest(null); setSaved(null); setDocumented(emptyTime); }
  function stop() { pending.current?.abort(); pending.current = null; running.current = false; preparingNew.current = null; setBusy(false); restored.current = ''; }
  useEffect(() => {
    mounted.current = true;
    const freeze = () => { stop(); clear(); setVisible(false); };
    const resume = () => { void access.refresh(); setVisible(document.visibilityState === 'visible'); setReload(value => value + 1); };
    const change = () => { if (document.visibilityState === 'visible') resume(); else freeze(); };
    document.addEventListener('visibilitychange', change); document.addEventListener('freeze', freeze); document.addEventListener('resume', resume);
    return () => { mounted.current = false; pending.current?.abort(); document.removeEventListener('visibilitychange', change); document.removeEventListener('freeze', freeze); document.removeEventListener('resume', resume); };
  }, []);
  function failed(value: unknown) {
    if (!(value instanceof Error)) throw value;
    setError(value);
    if (boundary.deny(value)) clear();
  }
  useEffect(() => {
    if (!ready || !batchId) { if (!ready) { stop(); clear(); } return; }
    if (boundary.blocked.current) return;
    const controller = new AbortController(); setLoading(true); setError(null);
    void (async () => {
      const [value, inputs] = await Promise.all([readBatch(batchId, access.patientId!, controller.signal), readDocumentInputs(batchId, access.patientId!, controller.signal)]);
      boundary.guard(controller.signal);
      if (pageId && !value.pages.some(page => page.page_id === pageId)) throw new Error('文书原页与当前批次不一致。');
      const original = pageId ? await readPageOriginal(pageId, access.patientId!, controller.signal) : null;
      boundary.guard(controller.signal);
      const proposed = value.intake_mode !== 'manual' && original?.page.status !== 'failed' && original?.page.candidate_set_id ? await readCandidates(original.page, controller.signal) : null;
      if (!boundary.current(controller.signal)) return;
      setBatch(value); setCandidate(proposed); setEpisodeOptions(inputs.episodes);
      setForm(previous => previous ?? { batch_id: value.batch_id, page_ids: pageId ? [pageId] : [],
        document_type: 'unknown', organization: null, documented_time: null, episode_ids: [], confirmed_text: null,
        proposed_metadata: { episodes: [], grouping_note: null } });
      setLoading(false);
    })().then(undefined, value => { if (boundary.current(controller.signal)) { clear(); setLoading(false); failed(value); } });
    return () => controller.abort();
  }, [ready, batchId, pageId, reload]);
  function run(work: (signal: AbortSignal) => Promise<void>) {
    if (!ready || running.current || !boundary.current()) return;
    preparingNew.current = null;
    const controller = new AbortController(); pending.current = controller; running.current = true; setBusy(true); setError(null);
    void Promise.resolve().then(() => { boundary.guard(controller.signal); return work(controller.signal); }).then(() => { if (mounted.current && boundary.current(controller.signal) && pending.current === controller) { preparingNew.current = null; running.current = false; setBusy(false); } }, value => {
      if (mounted.current && boundary.current(controller.signal) && pending.current === controller) {
        if (preparingNew.current && value instanceof ApiError && value.status === 409 && value.code === 'DOCUMENT_COPY_CONFIRMATION_REQUIRED') {
          rejectedRequest.current = preparingNew.current;
          setIdentity(null); setIntent(null); setRequest(null); restored.current = '';
          setParams(current => { const next = new URLSearchParams(current); next.delete('client_request'); next.delete('request'); return next; }, { replace: true });
        }
        preparingNew.current = null; running.current = false; setBusy(false); failed(value);
      }
    });
  }
  function marker(value: DocumentIntentInput, prepared?: IntentView) {
    setParams(current => { const next = new URLSearchParams(current); next.set('client_request', value.request_id); if (prepared) next.set('request', prepared.intent_id); return next; }, { replace: true });
  }
  async function readSaved(value: RequestView, signal: AbortSignal) {
    if (!value.receipt || !value.document_input) throw new Error('文书原请求缺少准确保存回执或正文。');
    const document = await readArchiveDocument(value.receipt.resource_id, access.patientId!, signal);
    if (BigInt(document.version) < BigInt(value.receipt.version) || document.version === value.receipt.version && !documentPayloadMatches(document, value.document_input)) throw new Error('文书回执与重新读取的保存内容不一致。');
    if (boundary.current(signal)) { setSaved(document); setRequest(value); }
  }
  function inspect() {
    const key = requestId ?? clientId;
    if (key && key === rejectedRequest.current) return;
    run(async signal => {
      if (!requestId && !clientId) throw new Error('文书原请求缺少准确恢复身份。');
      const value = requestId ? await readDocumentRequest(requestId, signal) : await findDocumentRequest(clientId!, 'archive_document_create', target, signal);
      if (value.operation_id !== 'archive_document_create' || value.target_key !== target || !value.document_input
          || value.document_input.batch_id !== batchId || value.document_input.page_ids.some(id => !batch?.pages.some(page => page.page_id === id))) throw new Error('文书原请求与当前患儿、批次及原页不一致。');
      if (!boundary.current(signal)) return;
      const original: DocumentIntentInput = { operation_id: 'archive_document_create', target_key: target,
        request_id: value.request_id, schema_version: '2.0.0', payload: value.document_input };
      setIdentity(original); setForm(value.document_input); setRequest(value); marker(original, { intent_id: value.intent_id } as IntentView);
      if (value.state === 'committed') await readSaved(value, signal);
      else if (value.state === 'pending' && !value.may_retry) { const prepared = await prepareDocumentRequest(original, signal); if (boundary.current(signal)) setIntent(prepared); }
    });
  }
  useEffect(() => {
    const key = requestId ?? clientId;
    if (!ready || loading || busy || !batch || identity || !key || key === rejectedRequest.current || restored.current === key) return;
    let active = true; queueMicrotask(() => { if (active && rejectedRequest.current !== key && restored.current !== key) { restored.current = key; inspect(); } });
    return () => { active = false; };
  }, [ready, loading, busy, batch, identity, requestId, clientId]);
  async function finish(original: DocumentIntentInput, prepared: IntentView, signal: AbortSignal) {
    const receipt = await submitDocumentRequest(original, prepared, signal);
    boundary.guard(signal);
    const value = await readDocumentRequest(prepared.intent_id, signal);
    boundary.guard(signal);
    if (value.state !== 'committed' || value.receipt?.request_id !== receipt.request_id || value.receipt.resource_id !== receipt.resource_id
        || value.receipt.version !== receipt.version || value.receipt.saved_at !== receipt.saved_at) throw new Error('文书原回执与重新读取的原请求不一致。');
    await readSaved(value, signal);
  }
  function submit() { run(async signal => {
    if (!form || !canWrite) throw new Error('尚未取得准确文书边界及归组权限。');
    if (!identity && newSaveReason) throw new Error(newSaveReason);
    const payload = validateApiValue<DocumentInput>('v2-protocol.schema.json#/$defs/DocumentInput', form);
    const original = identity ?? { operation_id: 'archive_document_create' as const, target_key: target,
      request_id: crypto.randomUUID(), schema_version: '2.0.0' as const, payload };
    setIdentity(original); marker(original);
    preparingNew.current = !identity && !intent ? original.request_id : null;
    const prepared = intent ?? await prepareDocumentRequest(original, signal);
    preparingNew.current = null;
    if (!boundary.current(signal)) return;
    setIntent(prepared); marker(original, prepared); await finish(original, prepared, signal);
  }); }
  function updateEpisode(index: number, value: ProposedEpisode) {
    if (!edit || !form) throw new Error('当前文书内容不能修改。');
    const episodes = [...(form.proposed_metadata?.episodes ?? [])]; episodes[index] = value;
    setForm({ ...form, proposed_metadata: { episodes, grouping_note: form.proposed_metadata?.grouping_note ?? null } });
  }
  function addEpisode() {
    if (!edit || !form) return;
    const episodes = form.proposed_metadata?.episodes ?? [];
    setForm({ ...form, proposed_metadata: { episodes: [...(episodes.length ? episodes : [{ ...unknownEpisode }]), { ...unknownEpisode }],
      grouping_note: form.proposed_metadata?.grouping_note ?? null } });
  }
  function move(index: number, direction: -1 | 1) {
    if (!edit || !form) throw new Error('当前页序不能修改。');
    const ids = [...form.page_ids]; [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
    setForm({ ...form, page_ids: ids });
  }
  function episodeFields(index: number, episode: ProposedEpisode) { return <div key={index} style={{ display: 'contents' }}>
    <label className="field"><span>就诊类型</span><select disabled={!edit} value={episode.kind} onChange={event => updateEpisode(index, { ...episode, kind: event.target.value as ProposedEpisode['kind'] })}>{episodeTypes.map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>
    <div className="field-grid">{(['start_time', 'end_time'] as const).map((name, position) => <label className="field" key={name}><span>{position === 0 ? '开始日期' : '结束日期'}</span><input type="date" disabled={!edit} value={episode[name]?.local_date ?? ''} onChange={event => updateEpisode(index, { ...episode, [name]: confirmedTime({ precision: 'date', value: event.target.value, timezone: '' }) })} /></label>)}</div>
  </div>; }
  const episodes = form?.proposed_metadata?.episodes ?? [];
  return <UI2FamilyFrame title={manual ? '手工整理文书' : '文书与就诊归组'} screenId="UI2-28" note="一份文书可以包含多次就诊。" backTo={manual ? `/archives/batches/${batchId}` : `/archives/confirm${batchId ? `?batch=${batchId}${pageId ? `&page=${pageId}` : ''}` : ''}`}>
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={loading || access.loading} error={duplicatePage ? null : error} onRetry={() => boundary.revalidate(() => { restored.current = ''; setError(null); setReload(value => value + 1); })} />
    {duplicatePage && <section className="status-panel error" role="alert"><h2>原页已经归入文书</h2>
      <p>存在已被其他文书引用的原页。{edit ? '请核验文书边界和复印关系，重新选择需要整理的原页。' : '请按原请求身份核验保存状态和原页关系。'}</p>
      <p>原文和选择保留在当前页面。</p><ContextLink className="text-action" to="/archives">查看档案目录</ContextLink>
      <details><summary>查看错误详情</summary><p>HTTP 409 · DOCUMENT_COPY_CONFIRMATION_REQUIRED</p></details>
    </section>}
    {manual && <div className="note blue"><ShieldCheck size={17} /><p>手工整理，未使用自动识别。请对照原件填写；就诊说明属于家庭补充，待专业核对。</p></div>}
    <section className="card form-card"><div className="section-heading"><h2>文书归属</h2><span className="badge neutral">{saved ? saved.grouping_status === 'pending' ? '已保存 · 待专业核对' : '已保存' : form ? '待归组' : '待选择原件'}</span></div><div className="pair"><span>原始文书</span><strong>{pageId ? '已接收的准确原页' : batch ? '请选择这份文书的原页' : '尚未提供'}</strong></div><div className="pair"><span>页面与顺序</span><strong>{form ? `${form.page_ids.length} 个原页，按本人选择顺序排列` : '尚未提供'}</strong></div>{pageId ? <ContextLink className="button secondary" to={`/archives/source?page=${pageId}&batch=${batchId}`}>查看原文依据</ContextLink> : <button className="button secondary" disabled>查看原文依据</button>}</section>
    <section className="card form-card"><label className="field"><span>文书类型</span><select disabled={!edit} value={form?.document_type ?? 'unknown'} onChange={event => { if (form) setForm({ ...form, document_type: event.target.value as DocumentType }); }}>{types.map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label><label className="field"><span>来源医院</span><input disabled={!edit} placeholder="尚未填写" value={form?.organization ?? ''} onChange={event => { if (form) setForm({ ...form, organization: event.target.value || null }); }} /></label>
      <h3>家庭补充的就诊说明</h3>{episodeFields(0, episodes[0] ?? unknownEpisode)}
      <label className="field"><span>就诊 1 说明</span><textarea disabled={!edit} placeholder="按原件补充这次就诊的情况" value={episodes[0]?.description ?? ''} onChange={event => updateEpisode(0, { ...(episodes[0] ?? unknownEpisode), description: event.target.value || null })} /></label>
      <label className="field"><span>归属说明</span><textarea disabled={!edit} placeholder="同份文书记载多次就诊时，分别确认每项事实" value={form?.proposed_metadata?.grouping_note ?? ''} onChange={event => { if (form) setForm({ ...form, proposed_metadata: { episodes, grouping_note: event.target.value || null } }); }} /></label>
      <details open={manual || undefined}><summary>原文、页序与全部就诊依据</summary><label className="field"><span>本人确认原文</span><textarea disabled={!edit} value={form?.confirmed_text ?? ''} onChange={event => { if (form) setForm({ ...form, confirmed_text: event.target.value || null }); }} /></label>
        {identity ? <><div className="pair"><span>文书形成时间</span><strong>{form?.documented_time?.raw_text ?? '未知'}</strong></div><div className="pair"><span>时间精度</span><strong>{form?.documented_time?.precision ?? '未知'}</strong></div></> : <MedicalTimeInput ui2 precisionDetails label="文书形成时间" value={documented} disabled={!edit} onChange={value => { if (form) { setDocumented(value); setForm({ ...form, documented_time: confirmedTime(value) }); } }} />}
        {batch?.pages.map((page, index) => <div key={page.page_id}><label className="field"><span>原页 {index + 1} · {page.status === 'failed' ? '识别处理失败' : page.status === 'uploaded' ? '原件已接收' : page.status}</span>{page.status === 'failed' && page.error_code && <small>失败代码：{page.error_code}</small>}<input type="checkbox" disabled={!edit || !selectableOriginal(page, manual)} checked={form?.page_ids.includes(page.page_id) ?? false} onChange={event => { if (form) setForm({ ...form, page_ids: event.target.checked ? [...form.page_ids, page.page_id] : form.page_ids.filter(id => id !== page.page_id) }); }} /></label>{manual && <ContextLink className="text-action" to={`/archives/source?page=${page.page_id}&batch=${batchId}`}>查看原页 {index + 1}</ContextLink>}</div>)}
        <ol>{form?.page_ids.map((id, index) => <li key={id} style={{ overflowWrap: 'anywhere' }}>原页 {index + 1} · {id}<button className="text-action" disabled={!edit || index === 0} onClick={() => move(index, -1)}><ArrowUp size={16} />向前移动</button><button className="text-action" disabled={!edit || index === form.page_ids.length - 1} onClick={() => move(index, 1)}><ArrowDown size={16} />向后移动</button></li>)}</ol>
        {!manual && <><h3>已有专业就诊引用</h3><p>引用来源保留准确字段与原页，采用资格由专业核对决定。</p>{episodeOptions.map(option => <label className="field" key={option.episode_id}><span>{option.label}</span><input type="checkbox" aria-label={option.label} disabled={!edit || !form?.page_ids.includes(option.source_page_id)} checked={form?.episode_ids.includes(option.episode_id) ?? false} onChange={event => { if (form) setForm({ ...form, episode_ids: event.target.checked ? [...form.episode_ids, option.episode_id] : form.episode_ids.filter(id => id !== option.episode_id) }); }} /><small>准确原页与来源字段：{option.source_page_id} · {option.source_field_id}</small></label>)}{!episodeOptions.length && <p>当前原页没有可选择的已有就诊来源</p>}</>}
        {episodes.slice(1).map((episode, offset) => { const index = offset + 1; return <section key={index}>{episodeFields(index, episode)}<label className="field"><span>就诊 {index + 1} 说明</span><textarea disabled={!edit} value={episode.description ?? ''} onChange={event => updateEpisode(index, { ...episode, description: event.target.value || null })} /></label></section>; })}<button className="text-action" disabled={!edit} onClick={addEpisode}>添加另一次就诊说明</button>
      </details></section>
    <div className="note blue"><ShieldCheck size={17} /><p>文书形成时间与事实发生时间分别保留。</p></div>
    {batch?.pages.some(page => page.status === 'failed') && <div className="note blue"><ShieldCheck size={17} /><p>识别处理失败的页面，在原件完整且处理已终止时，可由本人手动补充文书分类、来源、就诊说明和确认原文。保存的是人工填写内容，不会改变识别失败状态，也不会生成识别候选、临床事实或专业核对结论。</p></div>}
    {!identity && newSaveReason && <p id="document-grouping-save-reason" role="status">{newSaveReason}</p>}
    <button className="button" aria-describedby={!identity && newSaveReason ? 'document-grouping-save-reason' : undefined} disabled={!canWrite || busy || saved !== null || !identity && !!newSaveReason || !form?.page_ids.length || !!identity && (!intent || request?.may_retry) || !identity && (!!requestId || !!clientId) || documented.precision !== 'unknown' && !documented.value && !identity} onClick={submit}>{busy ? '正在核验归组保存结果' : '确认归组'}</button>
    {!batchId && ready && <section className="status-panel empty" role="status"><h2>尚未选择原始文书</h2><p>实际接收原件并确认文书边界后，可以保存分类和就诊说明。</p><ContextLink className="text-action" to="/archives/batches">选择已经接收的批次</ContextLink></section>}
    {candidate?.extraction && <details><summary>模型分类候选与原文依据</summary><p>文书类别候选：{candidate.extraction.document_type}；来源医院候选：{candidate.extraction.organization ?? '未知'}；形成时间原文：{candidate.extraction.documented_time_text ?? '未知'}。</p><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{candidate.extraction.page_text}</p></details>}
    {identity && !saved && <section className="status-panel pending" role="status"><h2>原请求结果待核验</h2><p>原请求及正文已经保留，保存结果明确之前不能创建新的归组请求。</p><button className="text-action" disabled={busy} onClick={inspect}>查询原归组请求</button>{request?.may_retry && <button className="button secondary" disabled={busy} onClick={() => run(async signal => { const prepared = await retryDocumentRequest(request, signal); if (boundary.current(signal)) { setIntent(prepared); setRequest(null); marker(identity, prepared); } })}>重新准备原归组请求</button>}</section>}
    {saved && <section className="status-panel success" role="status"><h2>文书归组已经保存</h2>{manual && <p>家庭补充内容已保存，待专业核对；未使用自动识别。</p>}<p>本次保存版本：{request?.receipt?.version}；当前文书版本：{saved.version}；原页数量：{saved.page_ids.length}。</p>{request?.receipt?.version !== saved.version && <p>当前文书包含后续修订，本次提交的原始内容仍保留。</p>}<ContextLink className="text-action" to={`/archives/grouping?document=${saved.document_id}`}>读取文书与全部就诊说明</ContextLink></section>}
  </UI2FamilyFrame>;
}
