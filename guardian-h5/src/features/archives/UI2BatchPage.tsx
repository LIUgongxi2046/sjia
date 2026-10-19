import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight, Plus } from '../../components/ThreeDIcon';
import { Layers, ScanLine, FileText, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ArchiveUpload } from './ArchiveUpload';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useEndpointAccess } from '../../state/useEndpointAccess';
import { readDeltaBatches } from '../../api/archiveDelta';
import { ApiError } from '../../api/client';
import { batchInputs, batchPollingInterval, batchRequest, prepareBatch, readBatch, retryBatchRequest, submitBatch, type BatchIdentity } from '../../api/archiveBatches';
import { automaticPollingBudgetMs, canContinueTaskPolling } from '../../api/taskPolling';
import type { BatchInputsView, BatchList, BatchView, PageView, RequestView } from '../../../../frontend-design/v2-types';
import type { IntentView } from '../../../../frontend-design/types';

const stages = [['接收原件', '按文书保留页面与顺序'], ['识别与归组', '每页完成后保存候选'], ['本人确认', '核对时间、原文和来源'], ['正式保存', '专业核对状态单独显示']];
const manualStages = [['接收原件', '保留原始页面与顺序'], ['手工整理', '对照原件填写文书内容'], ['家庭补充', '填写就诊时间和说明'], ['保存文书', '家庭自报，待专业核对']];
const pageLabels: Record<PageView['status'], string> = { declared: '等待原件', receiving: '正在接收', uploaded: '原件已接收', queued: '等待处理',
  processing: '正在处理', awaiting_confirmation: '等待本人确认', confirmed: '本人确认完成', archived: '资料已保存', failed: '处理失败',
  uncertain: '外部结果待核验', cancel_requested: '等待在途取消', cancelled: '已取消', superseded: '已有后续版本' };
export const batchStatusLabels: Record<BatchView['status'], string> = { receiving: '原件已接收', processing: '正在处理', awaiting_confirmation: '等待本人确认',
  partial: '各页状态不同', archived: '资料已保存', cancelled: '已取消', failed: '处理失败' };
const processingLimits = ['PROVIDER_CONCURRENCY_LIMIT', 'ARCHIVE_PAGE_CONCURRENCY_EXCEEDED',
  'PROVIDER_DAILY_BUDGET_EXCEEDED', 'ARCHIVE_DAILY_BUDGET_EXCEEDED', 'ARCHIVE_BATCH_BUDGET_EXCEEDED'];


export function BatchPageCounts({ counts }: { counts: BatchView['counts'] }) {
  const states = (Object.keys(pageLabels) as PageView['status'][])
    .filter(state => counts[state] > 0 || state === 'confirmed' || state === 'archived');
  return <p role="status">{states.map(state => `${pageLabels[state]} ${counts[state]} 页`).join('；')}。</p>;
}

export function UI2BatchPage() {
  const access = useAccess();
  return <BatchContent key={JSON.stringify([access.principalId, access.patientId])} />;
}

function BatchContent() {
  const boundary = useEndpointAccess();
  const access = useAccess(), navigate = useNavigate(), [params, setParams] = useSearchParams();
  const route = useParams();
  const [list, setList] = useState<BatchList | null>(null), [value, setValue] = useState<BatchView | null>(null);
  const [inputs, setInputs] = useState<BatchInputsView | null>(null), [selected, setSelected] = useState<string[]>([]);
  const [selectedPages, setSelectedPages] = useState<string[]>([]);
  const [intakeMode, setIntakeMode] = useState<BatchView['intake_mode']>('manual');
  const [choosing, setChoosing] = useState(() => params.has('upload_group')), [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null), [reload, setReload] = useState(0), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [identity, setIdentity] = useState<BatchIdentity | null>(null), [intent, setIntent] = useState<IntentView | null>(null);
  const [request, setRequest] = useState<RequestView | null>(null);
  const [pollingPaused, setPollingPaused] = useState(false);
  const polling = useRef<{ batchId: string; startedAt: number } | null>(null);
  const mounted = useRef(true), running = useRef(false), pending = useRef<AbortController | null>(null), restored = useRef('');
  const preparing = useRef(false);
  const batchId = params.get('batch') ?? route.batchId ?? null, intentId = params.get('request'), clientId = params.get('client_request');
  const ready = !!access.principalId && !!access.patientId && !access.loading && !access.error && visible;
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  function refreshProgress() { polling.current = null; setPollingPaused(false); setReload(current => current + 1); }
  function wipe() { setValue(null); setList(null); setInputs(null); setSelected([]); setSelectedPages([]); setIdentity(null); setIntent(null); setRequest(null); }
  useEffect(() => {
    setSelectedPages(current => current.filter(id => value?.pages.some(page => page.page_id === id && page.status === 'uploaded')));
  }, [value]);

  useEffect(() => {
    mounted.current = true;
    const visibility = () => {
      const shown = document.visibilityState === 'visible'; setVisible(shown);
      if (!shown) { pending.current?.abort(); restored.current = ''; wipe(); }
      else { void access.refresh(); setReload(current => current + 1); }
    };
    const freeze = () => { pending.current?.abort(); restored.current = ''; wipe(); setVisible(false); };
    const resume = () => { void access.refresh(); setVisible(document.visibilityState === 'visible'); setReload(current => current + 1); };
    document.addEventListener('visibilitychange', visibility); document.addEventListener('freeze', freeze); document.addEventListener('resume', resume);
    return () => { mounted.current = false; pending.current?.abort(); restored.current = '';
      document.removeEventListener('visibilitychange', visibility); document.removeEventListener('freeze', freeze); document.removeEventListener('resume', resume); };
  }, []);
  useEffect(() => {
    if (!ready) { pending.current?.abort(); pending.current = null; running.current = false; setBusy(false); wipe(); restored.current = ''; return; }
    if (boundary.blocked.current) return;
    const controller = new AbortController(); setLoading(true); setError(null);
    setValue(current => current?.batch_id === batchId ? current : null);
    queueMicrotask(() => {
      if (controller.signal.aborted || !access.patientId) return;
      const reading = batchId ? readBatch(batchId, access.patientId, controller.signal).then(saved => {
        if (boundary.current(controller.signal)) setValue(saved);
      }) : readDeltaBatches(access.patientId, undefined, controller.signal).then(directory => {
        if (boundary.current(controller.signal)) { setList(directory); setValue(null); }
      });
      void reading.then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (boundary.current(controller.signal)) { setError(failure); setLoading(false); boundary.deny(failure); wipe(); } })
        .finally(() => { if (boundary.current(controller.signal)) setLoading(false); });
    });
    return () => controller.abort();
  }, [ready, access.patientId, batchId, reload]);

  function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (!ready || running.current || !boundary.current()) return;
    const controller = new AbortController(); pending.current = controller; running.current = true; setBusy(true); setError(null);
    void Promise.resolve().then(() => { boundary.guard(controller.signal); return operation(controller.signal); }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (boundary.current(controller.signal) && mounted.current) {
        setError(failure);
        if (preparing.current && failure instanceof ApiError && failure.status === 409 && processingLimits.includes(failure.code)) {
          setIdentity(null); setIntent(null); setRequest(null);
          setParams(current => { const next = new URLSearchParams(current);
            for (const name of ['request', 'client_request', 'operation', 'target']) next.delete(name);
            return next;
          }, { replace: true });
        }
        if (boundary.deny(failure)) wipe();
      }
    }).finally(() => { if (pending.current === controller) { preparing.current = false; running.current = false; pending.current = null; if (mounted.current) setBusy(false); } });
  }
  function marker(original: BatchIdentity, prepared?: IntentView) {
    restored.current = prepared?.intent_id ?? original.request_id;
    setParams(current => { const next = new URLSearchParams(current); next.set('client_request', original.request_id);
      next.set('operation', original.operation_id); next.set('target', original.target_key);
      if (prepared) next.set('request', prepared.intent_id); return next; }, { replace: true });
  }
  async function finish(original: BatchIdentity, prepared: IntentView, signal: AbortSignal) {
    const saved = await submitBatch(original, prepared, signal);
    boundary.guard(signal);
    const query = await batchRequest(prepared.intent_id, undefined, signal);
    boundary.guard(signal);
    if (!query.request.receipt || query.request.state !== 'committed' || query.request.receipt.resource_id !== saved.resource_id)
      throw new Error('保存回执与原请求重新读取结果不一致。');
    const chosen = original.operation_id === 'archive_batch_create' ? saved.resource_id : batchId;
    if (!chosen || !access.patientId) throw new Error('保存结果缺少当前批次身份。');
    const savedBatch = await readBatch(chosen, access.patientId, signal);
    if (original.operation_id === 'archive_batch_create' && savedBatch.intake_mode !== (original.payload.intake_mode ?? 'ocr'))
      throw new Error('保存回执中的整理方式与原请求不一致。');
    if (!boundary.current(signal)) return;
    setValue(savedBatch); setIdentity(null); setIntent(null); setRequest(null); setInputs(null); setSelected([]); setChoosing(false);
    restored.current = prepared.intent_id; navigate(`/archives/batches/${chosen}`, { replace: true }); setReload(current => current + 1);
  }
  function submit(original: BatchIdentity) { run(async signal => {
    preparing.current = true; setIdentity(original); marker(original);
    const prepared = await prepareBatch(original, signal);
    preparing.current = false;
    if (!boundary.current(signal)) return;
    setIntent(prepared); marker(original, prepared); await finish(original, prepared, signal);
  }); }
  function create() {
    if (!access.patientId || !selected.length) return;
    submit({ operation_id: 'archive_batch_create', target_key: `/patients/${access.patientId}/archive-batches`,
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { file_group_ids: selected, intake_mode: intakeMode } });
  }
  function process() {
    if (!value || value.intake_mode === 'manual') return;
    const pages = value.pages.filter(page => page.status === 'uploaded' && selectedPages.includes(page.page_id)).map(page => page.page_id);
    if (!pages.length) return;
    submit({ operation_id: 'archive_batch_process', target_key: `/archive-batches/${value.batch_id}/process`, request_id: crypto.randomUUID(),
      schema_version: '2.0.0', payload: { expected_version: value.version, page_ids: pages } });
  }
  function pageAction(page: PageView, operation: 'archive_page_retry' | 'archive_page_cancel') {
    if (value?.intake_mode === 'manual') return;
    submit({ operation_id: operation, target_key: `/archive-pages/${page.page_id}/${operation === 'archive_page_retry' ? 'retry' : 'cancel'}`,
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { expected_status_version: page.status_version,
        reason: operation === 'archive_page_retry' ? '本人明确重试失败页面' : '本人取消页面处理' } });
  }
  function inspect() { run(async signal => {
    const operation = params.get('operation') as BatchIdentity['operation_id'] | null, target = params.get('target');
    if (!intentId && (!clientId || !operation || !target)) throw new Error('原请求缺少准确恢复身份。');
    const saved = await batchRequest(intentId ?? clientId!, intentId ? undefined : { operation: operation!, target: target! }, signal);
    if (!boundary.current(signal)) return;
    setIdentity(saved.identity); setRequest(saved.request); marker(saved.identity, { intent_id: saved.request.intent_id } as IntentView);
    if (saved.request.state === 'committed') {
      const chosen = saved.identity.operation_id === 'archive_batch_create' ? saved.request.receipt?.resource_id : batchId;
      if (!chosen || !access.patientId) throw new Error('原请求缺少已保存批次身份。');
      const current = await readBatch(chosen, access.patientId, signal);
      if (boundary.current(signal)) { setValue(current); setIdentity(null); setIntent(null); setRequest(null); restored.current = saved.request.intent_id;
        navigate(`/archives/batches/${chosen}`, { replace: true }); setReload(count => count + 1); }
    } else if (saved.request.state === 'pending' && !saved.request.may_retry) {
      const prepared = await prepareBatch(saved.identity, signal); if (boundary.current(signal)) setIntent(prepared);
    }
  }); }
  useEffect(() => {
    const key = intentId ?? clientId;
    if (!ready || busy || identity || !key || restored.current === key) return;
    let active = true;
    queueMicrotask(() => { if (active && restored.current !== key) { restored.current = key; inspect(); } });
    return () => { active = false; };
  }, [ready, intentId, clientId, busy, identity]);
  useEffect(() => { polling.current = null; setPollingPaused(false); }, [batchId, ready]);
  useEffect(() => {
    if (!ready || busy || identity || !value || pollingPaused) return;
    const delay = batchPollingInterval(value);
    if (delay === null) { polling.current = null; return; }
    if (polling.current?.batchId !== value.batch_id) polling.current = { batchId: value.batch_id, startedAt: performance.now() };
    if (!canContinueTaskPolling(performance.now() - polling.current.startedAt, delay)) { setPollingPaused(true); return; }
    const timer = window.setTimeout(() => {
      if (!polling.current || performance.now() - polling.current.startedAt >= automaticPollingBudgetMs) { setPollingPaused(true); return; }
      setReload(current => current + 1);
    }, delay);
    return () => window.clearTimeout(timer);
  }, [ready, busy, identity, value, pollingPaused]);
  const blocked = !ready || boundary.blocked.current || loading || busy || !!identity || !!intentId || !!clientId;
  const manual = value?.intake_mode === 'manual';
  const processingLimit = error instanceof ApiError && processingLimits.includes(error.code);
  const failed = value?.pages.filter(page => page.allowed_actions.includes('archive_page_retry')) ?? [];
  const cancellable = value?.pages.filter(page => page.allowed_actions.includes('archive_page_cancel') && ['uploaded', 'queued'].includes(page.status)) ?? [];
  return <UI2FamilyFrame title="批次进度" screenId="UI2-26" note="逐页保存进度，失败和完成内容分别呈现。" backTo="/archives">
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <section className="blue-feature"><Layers size={46} /><span className="eyebrow">长期资料整理</span><h2>分批提供，<br />逐页保留。</h2><p>原件接收后，可选择手工整理或自动识别。</p><button className="button" disabled={blocked} onClick={() => {
      setChoosing(true); run(async signal => { const available = await batchInputs(access.patientId!, signal); if (boundary.current(signal)) setInputs(available); });
    }}>选择已有资料<Plus size={17} /></button></section>
    {choosing && !boundary.blocked.current && <section className="card form-card"><h2>选择已接收原件</h2>{inputs?.items.map((item, index) => <label className="check-line" key={item.file_group_id}><input type="checkbox" disabled={blocked} checked={selected.includes(item.file_group_id)} onChange={event => setSelected(current => event.target.checked ? [...current, item.file_group_id] : current.filter(id => id !== item.file_group_id))} />原件组 {index + 1} · {item.page_count} 页 · {eventTimeLabel(item.created_at)}</label>)}
      {inputs?.items.length === 0 && <p>尚无未接收的可用原件。</p>}<label className="field"><span>整理方式</span><select disabled={blocked} value={intakeMode} onChange={event => setIntakeMode(event.target.value as BatchView['intake_mode'])}><option value="manual">手工整理</option><option value="ocr">自动识别</option></select></label><p>{intakeMode === 'manual' ? '保留原件，由家庭对照填写，未使用自动识别。' : '自动识别后，仍需本人对照原件确认。'}</p><button className="button" disabled={blocked || !selected.length} onClick={create}>接收所选资料</button>
      <ArchiveUpload key={JSON.stringify([access.principalId, access.patientId])} onSaved={id => { run(async signal => { const available = await batchInputs(access.patientId!, signal); if (boundary.current(signal)) { setInputs(available); setSelected(current => current.includes(id) ? current : [...current, id]); } }); }} /></section>}
    <div className="section-heading"><h2>当前处理进度</h2><button className="text-action" disabled={busy || !ready} onClick={refreshProgress}>刷新进度</button></div>
    {pollingPaused && <section className="status-panel pending" role="status"><p>页面自动查询已经结束，可以刷新进度继续读取原批次。</p></section>}
    <UI2ReadState loading={access.loading || loading && !value} error={processingLimit ? null : error} onRetry={() => boundary.revalidate(() => { restored.current = ''; setError(null); refreshProgress(); })} />
    {processingLimit && <section className="status-panel error rich-read-state" role="alert"><h2>当前识别额度已占用</h2>
      <p>{['PROVIDER_CONCURRENCY_LIMIT', 'ARCHIVE_PAGE_CONCURRENCY_EXCEEDED'].includes(error.code)
        ? '本次选择超过当前可用并发额度。请减少选择的页数，或在已有请求核验完成后继续提交。'
        : '当前识别预算不足。预算允许后，可以继续提交所选页面。'}</p>
      <details><summary>查看处理详情</summary><p>错误代码：{error.code}</p></details>
      <button className="text-action" disabled={loading || busy} onClick={() => { setError(null); setReload(count => count + 1); }}>刷新处理状态</button>
    </section>}
    <section className="card form-card"><div className="stage-status"><span className="badge">{value ? batchStatusLabels[value.status] : '尚未接收批次'}</span><span>{value ? `${value.counts.pages} 页原件` : '等待提供资料'}</span></div>
      <ol className="process-list">{(manual ? manualStages : stages).map(([title, text], index) => <li key={title}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{title}</strong><small>{text}</small></div></li>)}</ol>
      {value && <><BatchPageCounts counts={value.counts} />{manual ? <><p>手工整理 · 未使用自动识别。补充内容为家庭自报，待专业核对。</p><ContextLink className="button" to={`/archives/grouping?batch=${value.batch_id}`}>手工整理文书</ContextLink></> : value.allowed_actions.includes('archive_batch_process') && <><p>已选择 {selectedPages.length} 页。展开待处理页面，选择本次需要识别的内容。</p><button className="button" disabled={blocked || !selectedPages.length} onClick={process}>处理所选页面</button></>}</>}
    </section>
    {ready && !boundary.blocked.current && (list || batchId) && <details onToggle={event => {
      if (event.currentTarget.open && batchId) run(async signal => {
        const directory = await readDeltaBatches(access.patientId!, undefined, signal);
        if (boundary.current(signal)) setList(directory);
      });
    }}><summary>选择已经接收的批次</summary>{!list && <p>正在读取批次目录。</p>}{list?.items.map(batch => <button className="list-row" key={batch.batch_id} disabled={blocked} onClick={() => navigate(`/archives/batches/${batch.batch_id}`)}><span className="row-text"><strong>{eventTimeLabel(batch.received_at)}</strong><small>{batch.counts.pages} 页 · {batchStatusLabels[batch.status]}{batch.intake_mode === 'manual' ? ' · 手工整理' : ''}</small></span><ChevronRight size={17} /></button>)}
      {list?.next_cursor && <button className="text-action" disabled={blocked} onClick={() => run(async signal => { const more = await readDeltaBatches(access.patientId!, list.next_cursor!, signal); if (boundary.current(signal)) setList(current => current ? { ...more, items: [...current.items, ...more.items] } : more); })}>读取更多批次</button>}</details>}
    {value?.pages.map((page, index) => <details key={page.page_id} className="card form-card" style={{ overflowWrap: 'anywhere' }}><summary>页面 {index + 1} · {pageLabels[page.status]}</summary><p>原件版本 {page.file_object_version}；页面版本 {page.version}；状态版本 {page.status_version}。</p>
      {page.status === 'uncertain' && <p role="status">这页请求的受理或结果尚未确认，暂时不能重新提交。已完成页面可以继续核对。</p>}
      {page.error_code === 'ARK_RATE_LIMITED' && <p role="status">识别服务正在限制请求频率。这页尚未完成识别，已完成页面可以继续核对。</p>}
      {page.error_code && <details><summary>查看处理详情</summary><p>错误代码：{page.error_code}</p></details>}
      {!manual && page.status === 'uploaded' && value.allowed_actions.includes('archive_batch_process') && <label className="check-line"><input type="checkbox" disabled={blocked} checked={selectedPages.includes(page.page_id)} onChange={event => setSelectedPages(current => event.target.checked ? [...current, page.page_id] : current.filter(id => id !== page.page_id))} />选择本页进行识别</label>}
      <ContextLink className="text-action" to={`/archives/source?page=${page.page_id}&batch=${value.batch_id}`}>读取本页原件</ContextLink>
      {page.attempts.map(attempt => <div key={attempt.attempt_id} style={{ overflowWrap: 'anywhere' }}><p>处理尝试 {attempt.attempt_number} · {attempt.model_version} · {attempt.status}</p><p>耗时：{attempt.duration_ms === null ? '尚未取得' : `${attempt.duration_ms} 毫秒`}；{attempt.pricing_scope === 'simulation' ? '模拟计价' : '费用范围待核验'}：{attempt.cost === null ? '尚未取得准确费用' : `${attempt.cost} ${attempt.currency}`}。</p><p>请求标识：{attempt.provider_request_id ?? '尚未取得外部请求标识'}；原提交：{attempt.request_id ?? '未关联提交'}。</p></div>)}
      {!manual && page.allowed_actions.includes('archive_page_retry') && <button className="button secondary" disabled={blocked} onClick={() => pageAction(page, 'archive_page_retry')}>重试本页</button>}{!manual && page.allowed_actions.includes('archive_page_cancel') && <button className="button secondary" disabled={blocked} onClick={() => pageAction(page, 'archive_page_cancel')}>{page.status === 'processing' ? '请求取消在途处理' : '取消本页'}</button>}
      {page.candidate_set_id && <ContextLink className="text-action" to={`/archives/confirm?page=${page.page_id}&batch=${value.batch_id}`}>确认本页候选</ContextLink>}</details>)}
    <section className="card">{!manual && <ContextLink className="list-row" to={`/archives/confirm${value ? `?batch=${value.batch_id}` : ''}`}><span className="row-icon"><ScanLine size={30} /></span><span className="row-text"><strong>确认逐项内容</strong><small>每项事实保留自己的时间</small></span><ChevronRight size={17} /></ContextLink>}<ContextLink className="list-row" to={`/archives/changes${value ? `?batch=${value.batch_id}` : ''}`}><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看这次新增内容</strong><small>明确比较基准与事实出处</small></span><ChevronRight size={17} /></ContextLink></section>
    {!manual && <div className="two-buttons"><button className="button secondary" disabled={blocked || !failed.length} onClick={() => pageAction(failed[0], 'archive_page_retry')}>重试失败页面</button><button className="button secondary" disabled={blocked || !cancellable.length} onClick={() => pageAction(cancellable[0], 'archive_page_cancel')}>取消未开始页面</button></div>}
    {identity && <section className="card form-card"><h2>查询原请求保存结果</h2><p>原请求身份已经保存，继续操作使用原有正文。</p><button className="text-action" disabled={busy} onClick={inspect}>查询原请求</button>{request?.may_retry && <button className="button secondary" disabled={busy} onClick={() => run(async signal => { const prepared = await retryBatchRequest(request, signal); if (boundary.current(signal)) { setIntent(prepared); marker(identity, prepared); } })}>重新准备原请求</button>}{intent && <button className="button" disabled={busy} onClick={() => run(signal => finish(identity, intent, signal))}>继续原请求提交</button>}</section>}
  </UI2FamilyFrame>;
}
