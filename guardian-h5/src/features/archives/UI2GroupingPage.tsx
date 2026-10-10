import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ArrowDown, ArrowUp, ChevronDown, ShieldCheck } from 'lucide-react';
import { UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useArchiveDocument } from '../../api/useArchiveDocument';
import { ApiError } from '../../api/client';
import { validateApiValue } from '../../api/schema';
import { documentPayloadMatches, findDocumentRequest, prepareDocumentRequest, readArchiveDocument, readDocumentRequest,
  readDocumentInputs, retryDocumentRequest, submitDocumentRequest } from '../../api/archiveDocuments';
import { timePayload } from '../../components/medicalTimeValue';
import { timeLabel } from '../../api/records';
import { UI2DocumentCreatePage } from './UI2DocumentCreatePage';
import type { IntentView, MedicalTimeInput } from '../../../../frontend-design/types';
import type { DocumentIntentInput, DocumentType, DocumentView, GroupingInput, ProposedEpisode,
  DocumentEpisodeOption, RequestView, WriteReceipt } from '../../../../frontend-design/v2-types';

const types: [DocumentType, string][] = [['laboratory', '检验报告'], ['inpatient', '住院病历'], ['discharge', '出院记录'],
  ['outpatient', '门诊记录'], ['therapy', '治疗资料'], ['imaging_report', '影像报告文字'], ['imaging_asset', '影像附件'], ['unknown', '待分类']];
const episodeTypes: [ProposedEpisode['kind'], string][] = [['inpatient', '住院'], ['outpatient', '门诊'], ['unknown', '未知']];
const groupingStates = { pending: '待归组', confirmed: '已确认归组', needs_correction: '需要更正' };

function timeInput(value: DocumentView['documented_time']): MedicalTimeInput | null {
  if (value === null) return null;
  const { id, patient_id, instant, resolution_status, ...input } = value;
  return validateApiValue<MedicalTimeInput>('protocol.schema.json#/$defs/MedicalTimeInput', { ...input, instant: null,
    resolution_status: resolution_status === 'resolved' ? 'local_only' : resolution_status });
}

function initialInput(value: DocumentView): GroupingInput {
  return { batch_id: value.batch_id, page_ids: [...value.page_ids], document_type: value.document_type,
    organization: value.organization, documented_time: timeInput(value.documented_time), episode_ids: [...value.episode_ids],
    confirmed_text: value.confirmed_text, proposed_metadata: value.proposed_metadata, expected_version: value.version, reason: '' };
}

function dateInput(value: string): MedicalTimeInput | null {
  return value ? validateApiValue<MedicalTimeInput>('protocol.schema.json#/$defs/MedicalTimeInput',
    timePayload({ precision: 'date', value, timezone: '' })) : null;
}

export function UI2GroupingPage() {
  const access = useAccess();
  const [params] = useSearchParams();
  const documentId = params.get('document');
  const sourceVersionId = params.get('version');
  if (!documentId) return <UI2DocumentCreatePage />;
  return <GroupingContent key={JSON.stringify([access.principalId, access.patientId, documentId])}
    documentId={documentId} sourceVersionId={sourceVersionId} />;
}

function GroupingContent({ documentId, sourceVersionId }: { documentId: string | null; sourceVersionId: string | null }) {
  const access = useAccess();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestId = params.get('request');
  const clientRequestId = params.get('client_request');
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const resource = useArchiveDocument(canRead ? documentId : null, access.patientId, access.principalId);
  const documentValue = canRead ? resource.data : null;
  const canWrite = !!documentValue && !!access.csrfToken && documentValue.allowed_actions.includes('archive_document_group')
    && relation?.status === 'active' && relation.actions.includes('write_record')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  const [form, setForm] = useState<GroupingInput | null>(null);
  const [episodeOptions, setEpisodeOptions] = useState<DocumentEpisodeOption[]>([]);
  const [identity, setIdentity] = useState<DocumentIntentInput | null>(null);
  const [intent, setIntent] = useState<IntentView | null>(null);
  const [request, setRequest] = useState<RequestView | null>(null);
  const [saved, setSaved] = useState<{ receipt: WriteReceipt; document: DocumentView } | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const composing = useRef(false);
  const mounted = useRef(true);
  const automaticRequest = useRef<string | null>(null);
  const currentVersion = !!documentValue && !!form && documentValue.version === form.expected_version;
  const edit = canWrite && currentVersion && !busy && !identity && !requestId && !clientRequestId && !saved;

  useEffect(() => {
    const controller = new AbortController(); setEpisodeOptions([]);
    if (documentValue && access.patientId) void readDocumentInputs(documentValue.batch_id, access.patientId, controller.signal).then(value => {
      if (!controller.signal.aborted) setEpisodeOptions(value.episodes);
    }, failure => { if (!controller.signal.aborted) { if (!(failure instanceof Error)) throw failure; setError(failure); } });
    return () => controller.abort();
  }, [documentValue?.batch_id, access.patientId]);

  useEffect(() => {
    if (!documentValue || form || requestId || clientRequestId) return;
    let current = true;
    void Promise.resolve().then(() => initialInput(documentValue)).then(value => { if (current) setForm(value); }, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (current) setError(failure);
    });
    return () => { current = false; };
  }, [documentValue, form, requestId, clientRequestId]);
  useEffect(() => {
    mounted.current = true;
    const changed = () => {
      if (document.visibilityState !== 'visible') { pending.current?.abort(); setSaved(null); setRequest(null); automaticRequest.current = null; }
    };
    document.addEventListener('visibilitychange', changed);
    return () => { mounted.current = false; pending.current?.abort(); document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => { if (!canRead) { pending.current?.abort(); setSaved(null); setRequest(null); automaticRequest.current = null; } }, [canRead]);

  function run(operation: (controller: AbortController) => Promise<void>) {
    if (!canRead || busyRef.current) return;
    const controller = new AbortController(); pending.current = controller;
    busyRef.current = true; setBusy(true); setError(null);
    void operation(controller).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (mounted.current && !controller.signal.aborted) {
        setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) void access.refresh();
        if (failure instanceof ApiError && failure.status === 409) resource.reload();
      }
    }).finally(() => {
      if (pending.current === controller) {
        busyRef.current = false; pending.current = null;
        if (mounted.current) setBusy(false);
      }
    });
  }

  async function adoptReceipt(receipt: WriteReceipt, payload: GroupingInput, controller: AbortController) {
    if (!access.patientId || receipt.resource_id !== documentId) throw new Error('归组回执与当前患儿或文书不一致。');
    const value = await readArchiveDocument(receipt.resource_id, access.patientId, controller.signal);
    if (BigInt(value.version) < BigInt(receipt.version)) throw new Error('重新读取的文书版本早于原保存回执。');
    if (value.version === receipt.version && !documentPayloadMatches(value, payload)) throw new Error('归组回执与重新读取的文书内容不一致，保存结果需要核验。');
    if (!controller.signal.aborted && mounted.current) { setSaved({ receipt, document: value }); resource.reload(); }
  }

  function inspect(id: string, byRequest = false) {
    run(async controller => {
      if (!documentValue || documentValue.patient_id !== access.patientId) throw new Error('尚未取得当前患儿的准确文书，原请求正文不能用于当前页面。');
      setSaved(null); setRequest(null);
      const value = byRequest ? await findDocumentRequest(id, 'archive_document_group', `/archive-documents/${documentId}/grouping`, controller.signal)
        : await readDocumentRequest(id, controller.signal);
      if (value.operation_id !== 'archive_document_group' || value.target_key !== `/archive-documents/${documentId}/grouping`
        || !value.document_input || !value.generation || !value.expires_at || !value.payload_sha256) throw new Error('该保存请求不属于当前文书归组。');
      const payload = validateApiValue<GroupingInput>('v2-protocol.schema.json#/$defs/GroupingInput', value.document_input);
      if (controller.signal.aborted || !mounted.current) return;
      setForm(payload); setRequest(value);
      setIdentity({ operation_id: 'archive_document_group', target_key: value.target_key, request_id: value.request_id,
        schema_version: '2.0.0', payload });
      setIntent({ intent_id: value.intent_id, request_id: value.request_id, generation: value.generation,
        expires_at: value.expires_at, payload_sha256: value.payload_sha256,
        status: value.state === 'committed' ? 'committed' : value.state === 'failed' ? 'failed' : 'prepared' });
      if (value.receipt) await adoptReceipt(value.receipt, payload, controller);
    });
  }

  useEffect(() => {
    const lookup = requestId ? `intent:${requestId}` : clientRequestId ? `request:${clientRequestId}` : null;
    if (canRead && documentValue && lookup && !busyRef.current && automaticRequest.current !== lookup) {
      automaticRequest.current = lookup;
      inspect(requestId ?? clientRequestId!, !requestId);
    }
  }, [canRead, documentValue, requestId, clientRequestId]);

  function submit() {
    run(async controller => {
      if (!canWrite || !form || (!identity && !currentVersion)) throw new Error('当前文书、原版本或归组权限尚未通过核验。');
      const payload = validateApiValue<GroupingInput>('v2-protocol.schema.json#/$defs/GroupingInput', form);
      const current = identity ?? { operation_id: 'archive_document_group' as const, target_key: `/archive-documents/${documentId}/grouping`,
        request_id: crypto.randomUUID(), schema_version: '2.0.0' as const, payload };
      setIdentity(current);
      automaticRequest.current = `request:${current.request_id}`;
      setParams(value => { const next = new URLSearchParams(value); next.set('client_request', current.request_id); return next; }, { replace: true });
      const prepared = intent ?? await prepareDocumentRequest(current, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      setIntent(prepared);
      automaticRequest.current = `intent:${prepared.intent_id}`;
      setParams(value => { const next = new URLSearchParams(value); next.set('request', prepared.intent_id); return next; }, { replace: true });
      if (prepared.status === 'failed' || (prepared.status === 'prepared' && Date.parse(prepared.expires_at) <= Date.now())) throw new Error('原归组请求需要重新核验，当前没有再次提交。');
      await adoptReceipt(await submitDocumentRequest(current, prepared, controller.signal), payload, controller);
    });
  }

  function retry() {
    run(async controller => {
      if (!canWrite || !request?.may_retry || !identity || !form) throw new Error('原归组请求尚不具备重新提交条件。');
      const prepared = await retryDocumentRequest(request, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      setIntent(prepared); setRequest(null);
      await adoptReceipt(await submitDocumentRequest(identity, prepared, controller.signal), form, controller);
    });
  }

  function update(value: GroupingInput) {
    if (!edit) throw new Error('当前文书表单不具备编辑条件。');
    setForm(value); setError(null);
  }

  function updateEpisode(index: number, value: ProposedEpisode) {
    if (!form) throw new Error('尚未读取准确文书表单。');
    const episodes = [...(form.proposed_metadata?.episodes ?? [])];
    if (index > episodes.length) throw new Error('本人就诊说明的顺序不连续。');
    episodes[index] = value;
    update({ ...form, proposed_metadata: { episodes, grouping_note: form.proposed_metadata?.grouping_note ?? null } });
  }

  function movePage(index: number, direction: -1 | 1) {
    if (!form || index + direction < 0 || index + direction >= form.page_ids.length) throw new Error('当前原页顺序不具备调整条件。');
    const pages = [...form.page_ids];
    [pages[index], pages[index + direction]] = [pages[index + direction], pages[index]];
    update({ ...form, page_ids: pages });
  }

  const firstEpisode = documentValue && form?.proposed_metadata?.episodes[0] || { kind: 'unknown' as const, start_time: null, end_time: null, description: null };
  const unknownResult = identity !== null && !saved;
  const canContinue = canWrite && !busy && !saved && !!form?.reason.trim()
    && (identity === null ? currentVersion && !requestId && !clientRequestId : request?.state === 'pending' && !request.may_retry);
  const sourcePath = sourceVersionId && documentId ? `/archives/source?version=${encodeURIComponent(sourceVersionId)}&document=${encodeURIComponent(documentId)}` : null;

  function episodeFields(index: number, value: ProposedEpisode) {
    return <div key={index} style={{ display: 'contents' }}>
      <label className="field"><span>就诊类型</span><select aria-label="就诊类型" disabled={!edit} value={documentValue && form ? value.kind : ''} onChange={event => updateEpisode(index, { ...value, kind: event.target.value as ProposedEpisode['kind'] })}>
        <option value="" disabled>请选择</option>
        {episodeTypes.map(([type, label]) => <option key={type} value={type}>{label}</option>)}
      </select></label>
      <div className="field-grid">{(['start_time', 'end_time'] as const).map((field, position) => <label className="field" key={field}><span>{position === 0 ? '开始日期' : '结束日期'}</span>
        <input type="date" placeholder="尚未填写" autoComplete="off" disabled={!edit} value={value[field]?.precision === 'date' ? value[field]?.local_date ?? '' : ''}
          onChange={event => updateEpisode(index, { ...value, [field]: dateInput(event.target.value) })} />
      </label>)}</div>
      {(value.start_time && value.start_time.precision !== 'date' || value.end_time && value.end_time.precision !== 'date')
        && <p>已保存时间继续保留其原始精度；日期控件仅在本人填写后修改对应时间。</p>}
    </div>;
  }

  return <UI2FamilyFrame title="文书与就诊归组" screenId="UI2-28" note="一份文书可以包含多次就诊。" backTo="/archives/confirm">
    <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    <section className="card form-card"><div className="section-heading"><h2>文书归属</h2><span className="badge neutral">{documentValue ? groupingStates[documentValue.grouping_status] : '待选择原件'}</span></div>
      <div className="pair"><span>原始文书</span><strong>{documentValue ? `版本 ${documentValue.version}` : '尚未提供'}</strong></div>
      <div className="pair"><span>页面与顺序</span><strong>{documentValue ? `${documentValue.page_ids.length} 个原页，按保存顺序排列` : '尚未提供'}</strong></div>
      {sourcePath && documentValue ? <ContextLink className="button secondary" to={sourcePath}>查看原文依据</ContextLink> : <button className="button secondary" disabled>查看原文依据</button>}
    </section>
    <form style={{ display: 'contents' }} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}
      onSubmit={event => { event.preventDefault(); if (!composing.current) submit(); }}>
      <section className="card form-card">
        <label className="field"><span>文书类型</span><select aria-label="文书类型" disabled={!edit} value={documentValue ? form?.document_type ?? '' : ''} onChange={event => { if (!form) throw new Error('尚未读取文书内容。'); update({ ...form, document_type: event.target.value as DocumentType }); }}>
          <option value="" disabled>请选择</option>
          {types.map(([type, label]) => <option key={type} value={type}>{label}</option>)}
        </select></label>
        <label className="field"><span>来源医院</span><input placeholder="尚未填写" autoComplete="off" disabled={!edit} value={documentValue ? form?.organization ?? '' : ''} onChange={event => { if (!form) throw new Error('尚未读取文书内容。'); update({ ...form, organization: event.target.value || null }); }} /></label>
        {episodeFields(0, firstEpisode)}
        <label className="field"><span>归属说明</span><textarea disabled={!edit} placeholder="同份文书记载多次就诊时，分别确认每项事实" value={documentValue ? form?.proposed_metadata?.grouping_note ?? '' : ''}
          onChange={event => { if (!form) throw new Error('尚未读取文书内容。'); update({ ...form, proposed_metadata: { episodes: form.proposed_metadata?.episodes ?? [], grouping_note: event.target.value || null } }); }} /></label>
        {documentValue && form && <details><summary>原文、页序与全部就诊依据</summary>
          <label className="field"><span>本人确认原文</span><textarea disabled={!edit} value={form.confirmed_text ?? ''} onChange={event => update({ ...form, confirmed_text: event.target.value || null })} /></label>
          <div className="pair"><span>文书形成时间</span><strong>{timeLabel(documentValue.documented_time === null ? null : {
            ...documentValue.documented_time, local_year: documentValue.documented_time.local_year ?? null,
            local_month: documentValue.documented_time.local_month ?? null, end_year: documentValue.documented_time.end_year ?? null,
            end_month: documentValue.documented_time.end_month ?? null, relative_text: documentValue.documented_time.relative_text ?? null })}</strong></div>
          <div className="pair"><span>平台取得时间</span><strong>{new Date(documentValue.available_at).toLocaleString('zh-CN')}</strong></div>
          <ol>{form.page_ids.map((id, index) => <li key={id}>原页 {index + 1} · {id}
            <button className="text-action" type="button" disabled={!edit || index === 0} aria-label={`原页 ${index + 1}向前移动`} onClick={() => movePage(index, -1)}><ArrowUp size={16} />向前移动</button>
            <button className="text-action" type="button" disabled={!edit || index === form.page_ids.length - 1} aria-label={`原页 ${index + 1}向后移动`} onClick={() => movePage(index, 1)}><ArrowDown size={16} />向后移动</button>
          </li>)}</ol>
          <h3>已有专业就诊引用</h3><p>已有引用继续保留，逐事实归属与专业核对分别完成。</p>
          {form.episode_ids.length ? <ul>{form.episode_ids.map(id => <li key={id}>{id}</li>)}</ul> : <p>尚无已保存的专业就诊引用</p>}
          {episodeOptions.map(option => <label className="field" key={option.episode_id}><span>{option.label}</span><input type="checkbox" aria-label={option.label} disabled={!edit || !form.page_ids.includes(option.source_page_id)} checked={form.episode_ids.includes(option.episode_id)} onChange={event => update({ ...form, episode_ids: event.target.checked ? [...form.episode_ids, option.episode_id] : form.episode_ids.filter(id => id !== option.episode_id) })} /><small>准确原页与来源字段：{option.source_page_id} · {option.source_field_id}</small></label>)}
          <h3>本人补充的就诊说明</h3>
          {form.proposed_metadata?.episodes.map((value, index) => <section key={index}>
            {index > 0 && episodeFields(index, value)}
            <label className="field"><span>就诊 {index + 1}说明</span><textarea disabled={!edit} value={value.description ?? ''} onChange={event => updateEpisode(index, { ...value, description: event.target.value || null })} /></label>
          </section>)}
          <button className="text-action" type="button" disabled={!edit} onClick={() => updateEpisode(form.proposed_metadata?.episodes.length ?? 0,
            { kind: 'unknown', start_time: null, end_time: null, description: null })}>添加另一次就诊说明</button>
          <label className="field"><span>更正原因</span><textarea disabled={!edit} value={form.reason} onChange={event => update({ ...form, reason: event.target.value })} /></label>
        </details>}
      </section>
      <div className="note blue"><ShieldCheck size={17} /><p>文书形成时间与事实发生时间分别保留。</p></div>
      <button className="button" type="submit" disabled={!canContinue}>{busy ? '正在核验归组保存结果' : '确认归组'}</button>
    </form>
    {documentValue && !canWrite && <section className="status-panel permission" role="status"><h2>当前未取得归组权限</h2><p>当前文书内容只供获准查看，保存仍需核验完整原件与记录范围。</p></section>}
    {documentValue && form && !currentVersion && !saved && <section className="status-panel stale" role="status"><h2>文书版本已有更新</h2><p>当前输入对应版本 {form.expected_version}，保存记录当前为版本 {documentValue.version}。原请求结果需要单独核验。</p></section>}
    {!documentId && canRead && <section className="status-panel empty" role="status"><h2>尚未选择原始文书</h2><p>实际接收原件并确认文书边界后，可以保存分类和就诊说明。</p><ContextLink className="text-action" to="/register/reports">提供原始资料</ContextLink></section>}
    {error && <section className="status-panel error" role="alert"><h2>归组结果需要核验</h2><p>{error.message}</p></section>}
    {unknownResult && <section className="status-panel pending" role="status"><h2>原请求结果待核验</h2><p>原请求及正文已经保留，保存结果明确之前不能创建新的归组请求。</p></section>}
    {canRead && (intent || requestId || clientRequestId) && !saved && <button className="text-action" disabled={busy}
      onClick={() => { const id = intent?.intent_id ?? requestId; inspect(id ?? clientRequestId!, !id); }}>查询原归组请求</button>}
    {canWrite && request?.may_retry && !saved && <button className="button secondary" disabled={busy} onClick={retry}>重新提交原归组请求</button>}
    {documentValue && request && <p role="status">请求状态：{request.state}；请求版本：{request.generation}{request.error_code ? `；${request.error_code}` : ''}</p>}
    {documentValue && saved && <section className="status-panel success" role="status"><h2>文书归组已经保存</h2>
      <div className="pair"><span>保存回执版本</span><strong>{saved.receipt.version}</strong></div><div className="pair"><span>保存时间</span><strong>{new Date(saved.receipt.saved_at).toLocaleString('zh-CN')}</strong></div>
      <p>{saved.document.version === saved.receipt.version ? '保存内容与重新读取的准确文书版本一致。' : `原请求保存事实已核验；文书目前为版本 ${saved.document.version}，当前内容对应后续更正。`}</p>
      <p>本人就诊说明和专业就诊依据分别保留，事实医学时间继续使用其原始版本。</p>
    </section>}
  </UI2FamilyFrame>;
}
