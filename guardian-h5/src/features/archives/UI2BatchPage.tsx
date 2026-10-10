import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import { Layers, ScanLine, FileText, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ArchiveUpload } from './ArchiveUpload';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { ApiError } from '../../api/client';
import { readDeltaBatches } from '../../api/archiveDelta';
import { batchInputs, batchRequest, prepareBatch, readBatch, retryBatchRequest, submitBatch, type BatchIdentity } from '../../api/archiveBatches';
import type { BatchInputsView, BatchList, BatchView, PageView, RequestView } from '../../../../frontend-design/v2-types';
import type { IntentView } from '../../../../frontend-design/types';

const stages = [['接收原件', '按文书保留页面与顺序'], ['识别与归组', '每页完成后保存候选'], ['本人确认', '核对时间、原文和来源'], ['正式保存', '专业核对状态单独显示']];
const pageLabels: Record<PageView['status'], string> = { declared: '等待原件', receiving: '正在接收', uploaded: '原件已接收', queued: '等待处理',
  processing: '正在处理', awaiting_confirmation: '等待本人确认', confirmed: '本人确认完成', archived: '资料已保存', failed: '处理失败',
  uncertain: '外部结果待核验', cancel_requested: '等待在途取消', cancelled: '已取消', superseded: '已有后续版本' };
const labels: Record<BatchView['status'], string> = { receiving: '原件已接收', processing: '正在处理', awaiting_confirmation: '等待本人确认',
  partial: '部分页面已完成', archived: '资料已保存', cancelled: '已取消', failed: '处理失败' };

export function UI2BatchPage() {
  const access = useAccess();
  return <BatchContent key={JSON.stringify([access.principalId, access.patientId])} />;
}

function BatchContent() {
  const access = useAccess(), navigate = useNavigate(), [params, setParams] = useSearchParams();
  const route = useParams();
  const [list, setList] = useState<BatchList | null>(null), [value, setValue] = useState<BatchView | null>(null);
  const [inputs, setInputs] = useState<BatchInputsView | null>(null), [selected, setSelected] = useState<string[]>([]);
  const [choosing, setChoosing] = useState(() => params.has('upload_group')), [loading, setLoading] = useState(false), [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null), [reload, setReload] = useState(0), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [identity, setIdentity] = useState<BatchIdentity | null>(null), [intent, setIntent] = useState<IntentView | null>(null);
  const [request, setRequest] = useState<RequestView | null>(null);
  const mounted = useRef(true), running = useRef(false), pending = useRef<AbortController | null>(null), restored = useRef('');
  const batchId = params.get('batch') ?? route.batchId ?? null, intentId = params.get('request'), clientId = params.get('client_request');
  const ready = !!access.principalId && !!access.patientId && !access.loading && !access.error && visible;
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  function wipe() { setValue(null); setList(null); setInputs(null); setSelected([]); setIdentity(null); setIntent(null); setRequest(null); }

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
    if (!ready) { pending.current?.abort(); wipe(); restored.current = ''; return; }
    const controller = new AbortController(); setLoading(true); setError(null);
    queueMicrotask(() => {
      if (controller.signal.aborted || !access.patientId) return;
      void readDeltaBatches(access.patientId, undefined, controller.signal).then(async directory => {
        if (controller.signal.aborted) return;
        setList(directory);
        if (batchId) {
          const saved = await readBatch(batchId, access.patientId!, controller.signal);
          if (!controller.signal.aborted) setValue(saved);
        } else setValue(null);
      }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) { setError(failure); wipe(); } })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    });
    return () => controller.abort();
  }, [ready, access.patientId, batchId, reload]);

  function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (!ready || running.current) return;
    const controller = new AbortController(); pending.current = controller; running.current = true; setBusy(true); setError(null);
    void operation(controller.signal).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted && mounted.current) {
        setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) { wipe(); void access.refresh(); }
      }
    }).finally(() => { if (pending.current === controller) { running.current = false; pending.current = null; if (mounted.current) setBusy(false); } });
  }
  function marker(original: BatchIdentity, prepared?: IntentView) {
    restored.current = prepared?.intent_id ?? original.request_id;
    setParams(current => { const next = new URLSearchParams(current); next.set('client_request', original.request_id);
      next.set('operation', original.operation_id); next.set('target', original.target_key);
      if (prepared) next.set('request', prepared.intent_id); return next; }, { replace: true });
  }
  async function finish(original: BatchIdentity, prepared: IntentView, signal: AbortSignal) {
    const saved = await submitBatch(original, prepared, signal);
    const query = await batchRequest(prepared.intent_id, undefined, signal);
    if (!query.request.receipt || query.request.state !== 'committed' || query.request.receipt.resource_id !== saved.resource_id)
      throw new Error('保存回执与原请求重新读取结果不一致。');
    const chosen = original.operation_id === 'archive_batch_create' ? saved.resource_id : batchId;
    if (!chosen || !access.patientId) throw new Error('保存结果缺少当前批次身份。');
    const savedBatch = await readBatch(chosen, access.patientId, signal);
    if (signal.aborted) return;
    setValue(savedBatch); setIdentity(null); setIntent(null); setRequest(null); setInputs(null); setSelected([]); setChoosing(false);
    restored.current = prepared.intent_id; navigate(`/archives/batches/${chosen}`, { replace: true }); setReload(current => current + 1);
  }
  function submit(original: BatchIdentity) { run(async signal => {
    setIdentity(original); marker(original);
    const prepared = await prepareBatch(original, signal);
    if (signal.aborted) return;
    setIntent(prepared); marker(original, prepared); await finish(original, prepared, signal);
  }); }
  function create() {
    if (!access.patientId || !selected.length) return;
    submit({ operation_id: 'archive_batch_create', target_key: `/patients/${access.patientId}/archive-batches`,
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { file_group_ids: selected } });
  }
  function process() {
    if (!value) return;
    const pages = value.pages.filter(page => page.status === 'uploaded').map(page => page.page_id);
    if (!pages.length) return;
    submit({ operation_id: 'archive_batch_process', target_key: `/archive-batches/${value.batch_id}/process`, request_id: crypto.randomUUID(),
      schema_version: '2.0.0', payload: { expected_version: value.version, page_ids: pages } });
  }
  function pageAction(page: PageView, operation: 'archive_page_retry' | 'archive_page_cancel') {
    submit({ operation_id: operation, target_key: `/archive-pages/${page.page_id}/${operation === 'archive_page_retry' ? 'retry' : 'cancel'}`,
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { expected_status_version: page.status_version,
        reason: operation === 'archive_page_retry' ? '本人明确重试失败页面' : '本人取消页面处理' } });
  }
  function inspect() { run(async signal => {
    const operation = params.get('operation') as BatchIdentity['operation_id'] | null, target = params.get('target');
    if (!intentId && (!clientId || !operation || !target)) throw new Error('原请求缺少准确恢复身份。');
    const saved = await batchRequest(intentId ?? clientId!, intentId ? undefined : { operation: operation!, target: target! }, signal);
    if (signal.aborted) return;
    setIdentity(saved.identity); setRequest(saved.request); marker(saved.identity, { intent_id: saved.request.intent_id } as IntentView);
    if (saved.request.state === 'committed') {
      const chosen = saved.identity.operation_id === 'archive_batch_create' ? saved.request.receipt?.resource_id : batchId;
      if (!chosen || !access.patientId) throw new Error('原请求缺少已保存批次身份。');
      const current = await readBatch(chosen, access.patientId, signal);
      if (!signal.aborted) { setValue(current); setIdentity(null); setIntent(null); setRequest(null); restored.current = saved.request.intent_id;
        navigate(`/archives/batches/${chosen}`, { replace: true }); setReload(count => count + 1); }
    } else if (saved.request.state === 'pending' && !saved.request.may_retry) {
      const prepared = await prepareBatch(saved.identity, signal); if (!signal.aborted) setIntent(prepared);
    }
  }); }
  useEffect(() => {
    const key = intentId ?? clientId;
    if (!ready || busy || identity || !key || restored.current === key) return;
    let active = true;
    queueMicrotask(() => { if (active && restored.current !== key) { restored.current = key; inspect(); } });
    return () => { active = false; };
  }, [ready, intentId, clientId, busy, identity]);
  useEffect(() => {
    if (!ready || busy || identity || !value?.pages.some(page => ['queued', 'processing', 'cancel_requested'].includes(page.status))) return;
    const delay = Math.max(...value.pages.map(page => page.task?.poll_after_ms ?? 1000));
    const timer = window.setTimeout(() => setReload(current => current + 1), delay);
    return () => window.clearTimeout(timer);
  }, [ready, busy, identity, value]);
  const blocked = !ready || busy || !!identity || !!intentId || !!clientId;
  const failed = value?.pages.filter(page => page.allowed_actions.includes('archive_page_retry')) ?? [];
  const cancellable = value?.pages.filter(page => page.allowed_actions.includes('archive_page_cancel') && ['uploaded', 'queued'].includes(page.status)) ?? [];
  return <UI2FamilyFrame title="批次进度" screenId="UI2-26" note="逐页保存进度，失败和完成内容分别呈现。" backTo="/archives">
    <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <section className="blue-feature"><Layers size={46} /><span className="eyebrow">长期资料整理</span><h2>分批提供，<br />逐页保留。</h2><p>手机离开页面后，已接收的处理任务仍由服务端管理。</p><button className="button" disabled={blocked} onClick={() => {
      setChoosing(true); run(async signal => { const available = await batchInputs(access.patientId!, signal); if (!signal.aborted) setInputs(available); });
    }}>选择已有资料<Plus size={17} /></button></section>
    {choosing && <section className="card form-card"><h2>选择已接收原件</h2>{inputs?.items.map((item, index) => <label className="check-line" key={item.file_group_id}><input type="checkbox" disabled={blocked} checked={selected.includes(item.file_group_id)} onChange={event => setSelected(current => event.target.checked ? [...current, item.file_group_id] : current.filter(id => id !== item.file_group_id))} />原件组 {index + 1} · {item.page_count} 页 · {new Date(item.created_at).toLocaleString('zh-CN')}</label>)}
      {inputs?.items.length === 0 && <p>尚无未接收的可用原件。</p>}<button className="button" disabled={blocked || !selected.length} onClick={create}>接收所选资料</button>
      <ArchiveUpload key={JSON.stringify([access.principalId, access.patientId])} onSaved={id => { run(async signal => { const available = await batchInputs(access.patientId!, signal); if (!signal.aborted) { setInputs(available); setSelected(current => current.includes(id) ? current : [...current, id]); } }); }} /></section>}
    <div className="section-heading"><h2>当前处理进度</h2><button className="text-action" disabled={busy || !ready} onClick={() => setReload(count => count + 1)}>刷新进度</button></div>
    <UI2ReadState loading={access.loading || loading && !value} error={error} onRetry={() => { void access.refresh(); setReload(count => count + 1); }} />
    <section className="card form-card"><div className="stage-status"><span className="badge">{value ? labels[value.status] : '尚未接收批次'}</span><span>{value ? `${value.counts.pages} 页原件` : '等待提供资料'}</span></div>
      <ol className="process-list">{stages.map(([title, text], index) => <li key={title}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{title}</strong><small>{text}</small></div></li>)}</ol>
      {value && <><p role="status">等待处理 {value.counts.queued} 页；正在处理 {value.counts.processing} 页；待确认 {value.counts.awaiting_confirmation} 页；失败 {value.counts.failed} 页；外部结果待核验 {value.counts.uncertain} 页。</p>{value.allowed_actions.includes('archive_batch_process') && <button className="button" disabled={blocked} onClick={process}>处理已接收页面</button>}</>}
    </section>
    {list && <details><summary>选择已经接收的批次</summary>{list.items.map(batch => <button className="list-row" key={batch.batch_id} disabled={blocked} onClick={() => navigate(`/archives/batches/${batch.batch_id}`)}><span className="row-text"><strong>{new Date(batch.received_at).toLocaleString('zh-CN')}</strong><small>{batch.counts.pages} 页 · {labels[batch.status]}</small></span><ChevronRight size={17} /></button>)}
      {list.next_cursor && <button className="text-action" disabled={blocked} onClick={() => run(async signal => { const more = await readDeltaBatches(access.patientId!, list.next_cursor!, signal); if (!signal.aborted) setList(current => current ? { ...more, items: [...current.items, ...more.items] } : more); })}>读取更多批次</button>}</details>}
    {value?.pages.map((page, index) => <details key={page.page_id} className="card form-card" style={{ overflowWrap: 'anywhere' }}><summary>页面 {index + 1} · {pageLabels[page.status]}</summary><p>原件版本 {page.file_object_version}；页面版本 {page.version}；状态版本 {page.status_version}。</p>{page.error_code && <p role="status">处理状态：{page.error_code}</p>}
      <ContextLink className="text-action" to={`/archives/source?page=${page.page_id}&batch=${value.batch_id}`}>读取本页原件</ContextLink>
      {page.attempts.map(attempt => <div key={attempt.attempt_id} style={{ overflowWrap: 'anywhere' }}><p>处理尝试 {attempt.attempt_number} · {attempt.model_version} · {attempt.status}</p><p>耗时：{attempt.duration_ms === null ? '尚未取得' : `${attempt.duration_ms} 毫秒`}；{attempt.pricing_scope === 'simulation' ? '模拟计价' : '费用范围待核验'}：{attempt.cost === null ? '尚未取得准确费用' : `${attempt.cost} ${attempt.currency}`}。</p><p>请求标识：{attempt.provider_request_id ?? '尚未取得外部请求标识'}；原提交：{attempt.request_id ?? '未关联提交'}。</p></div>)}
      {page.allowed_actions.includes('archive_page_retry') && <button className="button secondary" disabled={blocked} onClick={() => pageAction(page, 'archive_page_retry')}>重试本页</button>}{page.allowed_actions.includes('archive_page_cancel') && <button className="button secondary" disabled={blocked} onClick={() => pageAction(page, 'archive_page_cancel')}>{page.status === 'processing' ? '请求取消在途处理' : '取消本页'}</button>}
      {page.candidate_set_id && <ContextLink className="text-action" to={`/archives/confirm?page=${page.page_id}&batch=${value.batch_id}`}>确认本页候选</ContextLink>}</details>)}
    <section className="card"><ContextLink className="list-row" to={`/archives/confirm${value ? `?batch=${value.batch_id}` : ''}`}><span className="row-icon"><ScanLine size={30} /></span><span className="row-text"><strong>确认逐项内容</strong><small>每项事实保留自己的时间</small></span><ChevronRight size={17} /></ContextLink><ContextLink className="list-row" to={`/archives/changes${value ? `?batch=${value.batch_id}` : ''}`}><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看这次新增内容</strong><small>明确比较基准与事实出处</small></span><ChevronRight size={17} /></ContextLink></section>
    <div className="two-buttons"><button className="button secondary" disabled={blocked || !failed.length} onClick={() => pageAction(failed[0], 'archive_page_retry')}>重试失败页面</button><button className="button secondary" disabled={blocked || !cancellable.length} onClick={() => pageAction(cancellable[0], 'archive_page_cancel')}>取消未开始页面</button></div>
    {identity && <section className="card form-card"><h2>查询原请求保存结果</h2><p>原请求身份已经保存，继续操作使用原有正文。</p><button className="text-action" disabled={busy} onClick={inspect}>查询原请求</button>{request?.may_retry && <button className="button secondary" disabled={busy} onClick={() => run(async signal => { const prepared = await retryBatchRequest(request, signal); if (!signal.aborted) { setIntent(prepared); marker(identity, prepared); } })}>重新准备原请求</button>}{intent && <button className="button" disabled={busy} onClick={() => run(signal => finish(identity, intent, signal))}>继续原请求提交</button>}</section>}
  </UI2FamilyFrame>;
}
