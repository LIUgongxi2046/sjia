import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { FileText, UsersRound } from '../../components/ThreeDIcon';
import { ContextLink } from '../../components/ContextLink';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { useAccess } from '../../state/access';
import { ApiError } from '../../api/client';
import { searchArchives } from '../../api/archiveSearch';
import { checkDeltaReceipt, prepareDelta, readDelta, readDeltaBatches, readDeltaDocumentHistory, readDeltaRequest, retryDelta, submitDelta } from '../../api/archiveDelta';
import type { IntentView } from '../../../../frontend-design/types';
import type { BatchList, DocumentList, DocumentView, WriteReceipt } from '../../../../frontend-design/v2-types';
import type { DeltaFact, DeltaIdentity, DeltaRequest, DeltaView } from '../../../../frontend-design/archive-delta-types';
import type { ArchiveSearchView } from '../../../../frontend-design/archive-search-types';
import { documentTimeLabel } from './documentTimeLabel';
import { documentLabels } from './documentLabels';

const tabs = ['新增事实', '重复资料', '待核对'] as const;
const verification = { unverified: '尚未核对', pending: '等待核对', verified: '专业核对有效', needs_correction: '需要更正', revoked: '核对已撤销' };
const reasonLabels: Record<string, string> = {
  BASELINE_NOT_SELECTED: '尚未指定比较基准，仅整理本批资料。', FILE_CHECKSUM_MATCH_REQUIRES_CONFIRMATION: '文件校验值相同，仍需确认资料关系。',
  FIELD_VERIFICATION_REQUIRED: '需要有效专业核对。', FACT_MEDICAL_TIME_UNKNOWN: '事实发生时间尚未明确。', DOCUMENT_TIME_UNKNOWN: '文书形成时间尚未明确。',
  DOCUMENT_GROUPING_UNCONFIRMED: '文书与就诊归组尚未确认。', BASELINE_FACT_AMBIGUOUS: '比较基准包含多个同名项目，需要明确前值。',
  SAMPLING_RELATION_UNRESOLVED: '采样关系尚未确认。', SAME_SAMPLING_OR_FACT: '同次采样或同一事实引用分别保留。',
  SOURCE_CORRECTION: '展示资料更正前后内容。', INDEPENDENT_SAMPLING_REQUIRED: '两次检查的独立采样关系尚未确认。',
  SAMPLING_VERIFICATION_REQUIRED: '独立采样关系尚无有效核对依据。', MEDICAL_TIME_ORDER_NOT_CONFIRMED: '前后医学时间的顺序尚未确认。',
  CURRENT_AFFIRMED_CONTEXT_REQUIRED: '实际发生及当前语境尚未确认。', EXACT_NUMERIC_VALUES_REQUIRED: '需要完整、准确的原始数值。',
  FIELD_FIELD_CODE_NOT_COMPARABLE: '项目尚不满足比较条件。', FIELD_RAW_UNIT_NOT_COMPARABLE: '原始单位尚不满足比较条件。', FIELD_METHOD_NOT_COMPARABLE: '检验方法尚不满足比较条件。',
  FIELD_CONCEPT_CODE_NOT_COMPARABLE: '项目含义尚不满足比较条件。', SOURCE_RECORD_NOT_CURRENT: '原记录已有更正或撤回。', BASELINE_DOCUMENT_AMBIGUOUS: '需要选择准确的文书比较版本。',
  RAW_VALUES_ONLY_NO_CLINICAL_CLASSIFICATION: '仅展示两次检查原值，不生成临床等级。', DOCUMENT_RELATION_CONFIRMED: '已保存资料重复关系。',
};

function FactContent({ value, label }: { value: DeltaFact; label: string }) {
  return <section className="form-card" aria-label={label}><h3>{label} · {value.original_name ?? value.field_code}</h3>
    <p>{value.value_state === 'known' ? value.value_type === 'decimal' ? `${value.qualifier ?? '比较符号未知'} ${value.raw_numeric ?? '数值未知'} ${value.raw_unit ?? '单位未知'}`
      : value.raw_text ?? value.categorical_value ?? '尚未提供原文' : '原值尚不能确认'}</p>
    <p>{documentTimeLabel(value.medical_time)} · {verification[value.verification_status]}</p>
    <p>检验方法：{value.method ?? '尚未提供'}；语境：{value.context_type}；发生状态：{value.assertion_state}</p>
  </section>;
}

export function UI2DeltaPage() {
  const access = useAccess();
  return <DeltaContent key={`${access.principalId}:${access.patientId}`} />;
}

function DeltaContent() {
  const access = useAccess(), navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [reload, setReload] = useState(0), [tab, setTab] = useState<typeof tabs[number]>('新增事实');
  const [batches, setBatches] = useState<BatchList | null>(null), [documents, setDocuments] = useState<ArchiveSearchView | null>(null);
  const [history, setHistory] = useState<Record<string, DocumentList>>({});
  const [batch, setBatch] = useState(params.get('batch') ?? ''), [baseline, setBaseline] = useState('');
  const [baselineVersions, setBaselineVersions] = useState<string[]>([]);
  const [value, setValue] = useState<DeltaView | null>(null), [request, setRequest] = useState<DeltaRequest | null>(null);
  const [identity, setIdentity] = useState<DeltaIdentity | null>(null), [intent, setIntent] = useState<IntentView | null>(null);
  const [error, setError] = useState<Error | null>(null), [directoryError, setDirectoryError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null), mounted = useRef(true), running = useRef(false), automatic = useRef(''), composing = useRef(false);
  const deltaId = params.get('delta'), intentId = params.get('request'), clientRequestId = params.get('client_request');
  const currentDocuments = documents?.items.map(row => row.document).filter(row => row.batch_id === batch) ?? [];
  const baselineDocuments = documents?.items.map(row => row.document).filter(row => row.batch_id === baseline) ?? [];
  const edit = canRead && visible && !busy && !value && !identity && !deltaId && !intentId && !clientRequestId;

  useEffect(() => {
    mounted.current = true;
    const change = () => {
      const shown = document.visibilityState === 'visible'; setVisible(shown);
      if (!shown) { pending.current?.abort(); setValue(null); setRequest(null); setIntent(null); setHistory({}); automatic.current = ''; }
    };
    document.addEventListener('visibilitychange', change);
    return () => { mounted.current = false; automatic.current = ''; pending.current?.abort(); document.removeEventListener('visibilitychange', change); };
  }, []);
  useEffect(() => {
    if (!canRead) { pending.current?.abort(); setValue(null); setRequest(null); setIntent(null); automatic.current = ''; }
    const controller = new AbortController(); setBatches(null); setDocuments(null); setHistory({}); setDirectoryError(null);
    setLoading(canRead && visible);
    if (canRead && visible && access.patientId) void Promise.all([readDeltaBatches(access.patientId, undefined, controller.signal),
      searchArchives(access.patientId, { limit: 20 }, controller.signal)]).then(([batchList, documentList]) => {
      if (!controller.signal.aborted) { setBatches(batchList); setDocuments(documentList); }
    }, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setDirectoryError(failure); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [canRead, visible, access.patientId, reload]);

  function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (!canRead || !visible || running.current) return;
    const controller = new AbortController(); pending.current = controller; running.current = true; setBusy(true); setError(null);
    void operation(controller.signal).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted && mounted.current) {
        setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setValue(null); setRequest(null); void access.refresh(); }
      }
    }).finally(() => { if (pending.current === controller) {
      running.current = false; pending.current = null;
      if (mounted.current) { setBusy(false); if (controller.signal.aborted) { automatic.current = ''; setReload(current => current + 1); } }
    } });
  }

  async function adoptReceipt(receipt: WriteReceipt, original: DeltaIdentity, signal: AbortSignal, originalIntentId: string) {
    if (!access.patientId) throw new Error('摘要保存结果尚未取得当前患儿身份。');
    const saved = await readDelta(receipt.resource_id, access.patientId, signal); checkDeltaReceipt(saved, receipt, original);
    if (!signal.aborted && mounted.current) {
      setValue(saved); setBatch(saved.batch_id); setBaseline(saved.baseline_batch_id ?? ''); setBaselineVersions(saved.baseline_document_version_ids);
      automatic.current = `delta:${saved.delta_id}`;
      setParams(current => { const next = new URLSearchParams(current); next.set('batch', saved.batch_id); next.set('client_request', original.request_id);
        next.set('request', originalIntentId); next.set('delta', saved.delta_id); return next; }, { replace: true });
    }
  }

  function inspect() {
    run(async signal => {
      setValue(null); setRequest(null);
      if (!access.patientId) throw new Error('尚未取得当前患儿身份。');
      if (deltaId) {
        const saved = await readDelta(deltaId, access.patientId, signal);
        if (!signal.aborted) { setValue(saved); setBatch(saved.batch_id); setBaseline(saved.baseline_batch_id ?? ''); setBaselineVersions(saved.baseline_document_version_ids); }
        return;
      }
      if (!intentId && !clientRequestId) throw new Error('尚无需要读取的原请求。');
      if (!intentId && !batch) throw new Error('原请求尚未提供准确批次。');
      const saved = await readDeltaRequest(intentId ?? clientRequestId!, signal,
        intentId ? undefined : { request_id: clientRequestId!, target_key: `/archive-batches/${batch}/delta-summaries` });
      const original: DeltaIdentity = { operation_id: 'archive_delta_create', target_key: saved.target_key,
        request_id: saved.request_id, schema_version: '2.0.0', payload: saved.delta_input };
      const currentBatch = /^\/archive-batches\/([0-9a-f-]{36})\/delta-summaries$/.exec(saved.target_key);
      if (!currentBatch) throw new Error('摘要原请求没有准确批次。');
      if (!signal.aborted) {
        setIdentity(original); setRequest(saved); setBatch(currentBatch[1]); setBaseline(saved.delta_input.baseline_batch_id ?? '');
        setBaselineVersions(saved.delta_input.baseline_document_version_ids);
        setIntent({ intent_id: saved.intent_id, request_id: saved.request_id, generation: saved.generation, expires_at: saved.expires_at,
          payload_sha256: saved.payload_sha256, status: saved.state === 'committed' ? 'committed' : saved.state === 'failed' ? 'failed' : 'prepared' });
      }
      if (saved.receipt) await adoptReceipt(saved.receipt, original, signal, saved.intent_id);
    });
  }
  useEffect(() => {
    const marker = deltaId ? `delta:${deltaId}` : intentId ? `intent:${intentId}` : clientRequestId ? `client:${clientRequestId}` : '';
    if (value && (!deltaId || value.delta_id === deltaId)) { automatic.current = marker; return; }
    let active = true;
    queueMicrotask(() => {
      if (active && marker && canRead && visible && !running.current && automatic.current !== marker) { automatic.current = marker; inspect(); }
    });
    return () => { active = false; };
  }, [deltaId, intentId, clientRequestId, canRead, visible, reload, busy, value]);

  function submit() {
    run(async signal => {
      if (!batch || !access.patientId || !access.csrfToken) throw new Error('摘要提交尚未取得准确批次及当前权限。');
      const original: DeltaIdentity = identity ?? { operation_id: 'archive_delta_create', target_key: `/archive-batches/${batch}/delta-summaries`,
        request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { baseline_batch_id: baseline || null, baseline_document_version_ids: baselineVersions } };
      setIdentity(original); automatic.current = `client:${original.request_id}`;
      setParams(current => { const next = new URLSearchParams(current); next.set('batch', batch); next.set('client_request', original.request_id); return next; }, { replace: true });
      const prepared = intent ?? await prepareDelta(original, signal);
      if (signal.aborted) return;
      setIntent(prepared); automatic.current = `intent:${prepared.intent_id}`;
      setParams(current => { const next = new URLSearchParams(current); next.set('batch', batch); next.set('client_request', original.request_id);
        next.set('request', prepared.intent_id); return next; }, { replace: true });
      const receipt = await submitDelta(original, prepared, signal);
      await adoptReceipt(receipt, original, signal, prepared.intent_id);
    });
  }

  function retry() {
    run(async signal => {
      if (!request || !identity) throw new Error('尚未取得准确原请求。');
      const prepared = await retryDelta(request, signal);
      if (!signal.aborted) { setIntent(prepared); setRequest({ ...request, generation: prepared.generation, expires_at: prepared.expires_at, state: 'pending', may_retry: false }); }
    });
  }

  function more(kind: 'batches' | 'documents') {
    run(async signal => {
      if (!access.patientId) throw new Error('尚未选择患儿。');
      if (kind === 'batches') {
        if (!batches?.next_cursor) throw new Error('没有后续批次页面。');
        const next = await readDeltaBatches(access.patientId, batches.next_cursor, signal);
        if (next.snapshot_version !== batches.snapshot_version || next.items.some(item => batches.items.some(old => old.batch_id === item.batch_id))) throw new Error('批次目录已有更新，请重新读取。');
        if (!signal.aborted) setBatches({ ...next, items: [...batches.items, ...next.items] });
      } else {
        if (!documents?.next_cursor) throw new Error('没有后续文书页面。');
        const next = await searchArchives(access.patientId, { limit: 20, cursor: documents.next_cursor }, signal);
        if (next.snapshot_version !== documents.snapshot_version || next.items.some(item => documents.items.some(old => old.document.document_id === item.document.document_id))) throw new Error('文书目录已有更新，请重新读取。');
        if (!signal.aborted) setDocuments({ ...next, items: [...documents.items, ...next.items] });
      }
    });
  }

  function loadHistory(document: DocumentView) {
    run(async signal => {
      if (!access.patientId) throw new Error('尚未选择患儿。');
      const old = history[document.document_id];
      const next = await readDeltaDocumentHistory(document.document_id, access.patientId, old?.next_cursor ?? undefined, signal);
      if (old && (old.snapshot_version !== next.snapshot_version || next.items.some(value => old.items.some(saved => saved.version_id === value.version_id)))) throw new Error('文书历史已有更新，请重新读取。');
      if (!signal.aborted) setHistory(current => ({ ...current, [document.document_id]: { ...next, items: [...(old?.items ?? []), ...next.items] } }));
    });
  }

  function restart() {
    if (busy || request?.state === 'unknown' || identity && !value && request?.state !== 'failed') throw new Error('原请求结果尚需核验，不能重复建立摘要。');
    setValue(null); setRequest(null); setIntent(null); setIdentity(null); setError(null);
    automatic.current = deltaId ? `delta:${deltaId}` : intentId ? `intent:${intentId}` : clientRequestId ? `client:${clientRequestId}` : '';
    setParams(batch ? { batch } : {}, { replace: true }); setReload(current => current + 1);
  }
  const rows = visible && canRead ? value?.items.filter(item => tab === '新增事实' ? ['document', 'fact', 'change'].includes(item.kind)
    : tab === '重复资料' ? item.kind === 'duplicate' : ['conflict', 'unknown_time', 'unverified'].includes(item.kind)) ?? [] : [];
  const baselineBatch = batches?.items.find(row => row.batch_id === baseline), currentBatch = batches?.items.find(row => row.batch_id === batch);
  const readyToSubmit = canRead && visible && !!batch && !value && !busy && (identity ? request?.state === 'pending' && !request.may_retry : edit && !!currentDocuments.length);

  return <UI2FamilyFrame title="这次新增了什么" screenId="UI2-30" note="明确比较基准，逐条变化可以查看来源。" backTo="/archives/batches">
    <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <section className="delta-hero"><FileText size={47} /><span className="eyebrow">本次资料更新</span><h2>看清新增，<br />也看清依据。</h2><p>每项变化，都能返回原始文书。</p></section>
    <UI2ReadState loading={loading || busy} error={error ?? directoryError} onRetry={() => { automatic.current = ''; setReload(current => current + 1); if (deltaId || intentId || clientRequestId) inspect(); }} />
    <section className="card form-card"><div className="pair"><span>比较基准</span><strong>{baseline ? identity && !value ? '原请求已保存的比较基准' : baselineBatch ? `${new Date(baselineBatch.received_at).toLocaleString('zh-CN')} · ${baselineVersions.length || baselineDocuments.length} 份文书版本` : '读取已保存基准' : '尚未选择'}</strong></div>
      <div className="pair"><span>当前批次</span><strong>{batch ? currentBatch ? `${new Date(currentBatch.received_at).toLocaleString('zh-CN')} · ${currentBatch.counts.pages} 个原页` : '读取已保存批次' : '尚未提供'}</strong></div>
      <details><summary>选择批次及比较基准</summary><form onSubmit={event => { event.preventDefault(); if (!composing.current) submit(); }}
        onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
        onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}>
        <label className="field"><span>当前批次</span><select aria-label="当前批次" disabled={!edit || loading} value={batch} onChange={event => { setBatch(event.target.value); setBaseline(''); setBaselineVersions([]); }}>
          <option value="">请选择已保存批次</option>{batches?.items.map(row => <option key={row.batch_id} value={row.batch_id}>{new Date(row.received_at).toLocaleString('zh-CN')} · {row.counts.pages} 个原页</option>)}</select></label>
        <label className="field"><span>比较基准</span><select aria-label="比较基准" disabled={!edit || !batch || loading} value={baseline} onChange={event => { setBaseline(event.target.value);
          setBaselineVersions(documents?.items.filter(row => row.document.batch_id === event.target.value && row.document.batch_id !== batch).map(row => row.document.version_id) ?? []); }}>
          <option value="">没有基准，仅整理本批内容</option>{batches?.items.map(row => <option key={row.batch_id} value={row.batch_id}>{new Date(row.received_at).toLocaleString('zh-CN')} · {row.counts.pages} 个原页</option>)}</select></label>
        {baselineDocuments.map(document => <div className="field" key={document.document_id}><span>{documentLabels[document.document_type]} · {document.organization ?? '来源医院未知'}</span>
          <select aria-label={`比较文书版本 ${document.document_id}`} disabled={!edit || !history[document.document_id]} value={baselineVersions.find(id => history[document.document_id]?.items.some(row => row.version_id === id) || document.version_id === id) ?? ''}
            onChange={event => { const identities = new Set([document.version_id, ...(history[document.document_id]?.items.map(row => row.version_id) ?? [])]); setBaselineVersions(current => [...current.filter(id => !identities.has(id)), ...(event.target.value ? [event.target.value] : [])]); }}>
            <option value="">尚未选取此文书</option>{(history[document.document_id]?.items ?? [document]).map(row => <option key={row.version_id} value={row.version_id}>文书版本 {row.version} · {documentTimeLabel(row.documented_time)}</option>)}</select>
          {(!history[document.document_id] || history[document.document_id].next_cursor) && <button className="text-action" type="button" disabled={!edit} onClick={() => loadHistory(document)}>读取已保存文书版本</button>}
        </div>)}
        {!baseline && <p>没有比较基准时，保留本批内容和待处理事项，不生成临床变化。</p>}
        {baseline === batch && baseline && <p>同一批次需要指定更正前的准确文书版本；当前版本不能与自身比较。</p>}
        <button className="button" type="submit" disabled={!readyToSubmit}>{busy ? '正在核验保存结果' : '生成并保存新增内容摘要'}</button>
      </form>{batches?.next_cursor && <button className="text-action" disabled={!edit} onClick={() => more('batches')}>读取更多批次</button>}{documents?.next_cursor && <button className="text-action" disabled={!edit} onClick={() => more('documents')}>读取更多文书</button>}</details>
    </section>
    <div className="segments" aria-label="摘要内容分类">{tabs.map(label => <button key={label} aria-pressed={tab === label} className={tab === label ? 'active' : ''} onClick={() => setTab(label)}>{label}</button>)}</div>
    {value?.validity === 'invalid' && <section className="status-panel stale" role="status"><h2>已保存摘要当前失效</h2><p>资料版本、采样关系、专业核对或访问权限已有变化，需要重新选择基准并生成内容。</p><button className="text-action" disabled={busy} onClick={restart}>重新选择资料</button></section>}
    {value && !value.baseline_document_version_ids.length && value.validity === 'valid' && <p role="status">尚未指定比较基准，仅整理本批资料和待处理事项。</p>}
    {rows.length ? rows.map(item => <section className="card" key={item.item_id}><h2>{item.title}</h2>
      {item.before && <FactContent label="比较前值" value={item.before} />}{item.after && <FactContent label="本批内容" value={item.after} />}
      {item.reason_codes.map(code => <p key={code}>{reasonLabels[code] ?? code}</p>)}
      {item.source_refs.map((source, index) => <div key={`${source.document_version_id}:${source.fact_version_id}:${source.page_id}:${index}`}><ContextLink className="text-action"
        to={`/archives/source?document=${encodeURIComponent(source.document_id)}&document_version=${encodeURIComponent(source.document_version_id)}`}>查看原文依据 {index + 1}<ChevronRight size={16} /></ContextLink>
        {source.record_version_id && <ContextLink className="text-action" to={`/records/versions/${encodeURIComponent(source.record_version_id)}`}>查看准确记录与字段<ChevronRight size={16} /></ContextLink>}
        <details><summary>文书、原页与字段版本</summary><p>文书版本：{source.document_version_id}；原页：{source.page_id}；原件版本：{source.object_version}；字段：{source.field_id ?? '此项为文书内容'}。</p></details>
      </div>)}</section>) : <section className="card"><div className="empty"><span className="empty-icon"><FileText size={54} /></span><h3>{value ? '当前分类暂无已保存内容' : '尚无可生成的内容'}</h3><p>需要当前资料和明确基准。未知内容不会被补充为临床结论。</p></div></section>}
    {identity && !value && <section className="card"><h2>原请求保存结果需要核验</h2><p>重新读取原请求后，依据实际状态继续提交。</p><button className="text-action" disabled={busy} onClick={inspect}>查询原请求结果</button>
      {request?.may_retry && <button className="text-action" disabled={busy} onClick={retry}>重新准备原请求</button>}{readyToSubmit && <button className="button secondary" onClick={submit}>继续原请求提交</button>}</section>}
    {value?.validity === 'valid' && <section className="card"><p>已保存 {new Date(value.created_at).toLocaleString('zh-CN')}；每条内容引用生成时的准确来源。</p><button className="text-action" disabled={busy} onClick={restart}>选择其他资料生成摘要</button></section>}
    <section className="card"><ContextLink className="list-row" to="/archives/batches"><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>选择当前批次</strong></span><ChevronRight size={17} /></ContextLink>
      <ContextLink className="list-row" to="/summaries"><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看就医摘要</strong><small>按时间范围整理原始资料</small></span><ChevronRight size={17} /></ContextLink></section>
  </UI2FamilyFrame>;
}
