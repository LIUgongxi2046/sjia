import { useCallback, useEffect, useRef, useState } from 'react';
import { useBlocker, useNavigate, useSearchParams } from 'react-router';
import { LockKeyhole, ShieldCheck } from 'lucide-react';
import type { IntentView } from '../../../../frontend-design/types';
import type { DatasetList, DatasetView, EvaluationIntentInput, EvaluationRuleProof, EvaluationRunInput, EvaluationRunIntentInput, RequestView, SupportSessionView, WriteReceipt } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { evaluationPayloadMatches, evaluationTarget, findEvaluationRequest, prepareEvaluationRequest,
  readEvaluationDataset, readEvaluationRequest, retryEvaluationRequest, searchEvaluationDatasets, submitEvaluationRequest } from '../../api/evaluationDatasets';
import { UI2Frame, UI2Sheet } from '../../components/UI2Frame';
import { DatasetEditor, datasetOperationNames } from './DatasetEditor';
import type { DatasetOperation } from './DatasetEditor';
import type { DatasetPayload } from './DatasetEditor';
import { evaluationRunTarget, findEvaluationRunRequest, prepareEvaluationRun, readEvaluationRun, readEvaluationRunRequest,
  retryEvaluationRunRequest, submitEvaluationRun } from '../../api/evaluationRuns';
import { EvaluationRunPanel } from './EvaluationRunPanel';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';

const tabs = ['用途清单', '独立标签', '评价结果'];
const labels = { positive: '阳性', negative: '阴性', unknown: '未知' };
const purposes = { format_evaluation: '格式评价', workflow_evaluation: '流程评价', clinical_evaluation: '临床评价' };
const operationIds: DatasetOperation[] = ['dataset_create', 'dataset_partition', 'dataset_label', 'dataset_freeze', 'dataset_run_create'];
type EvaluationIdentity = EvaluationIntentInput | EvaluationRunIntentInput;
function target(operation: DatasetOperation, datasetId: string | null) {
  if (operation === 'dataset_create') return '/support/datasets';
  if (!datasetId) throw new Error('当前操作缺少准确评价集合标识。');
  return `/support/datasets/${datasetId}/${{ dataset_partition: 'partitions', dataset_label: 'labels',
    dataset_freeze: 'freeze', dataset_run_create: 'runs' }[operation]}`;
}
function prepare(identity: EvaluationIdentity, signal: AbortSignal) {
  return identity.operation_id === 'dataset_run_create' ? prepareEvaluationRun(identity, signal) : prepareEvaluationRequest(identity, signal);
}
function send(identity: EvaluationIdentity, intent: IntentView, signal: AbortSignal) {
  return identity.operation_id === 'dataset_run_create' ? submitEvaluationRun(identity, intent, signal) : submitEvaluationRequest(identity, intent, signal);
}
function count(value: number | null) { return value === null ? '未统计' : String(value); }

export function DatasetPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const [parameters, setParameters] = useSearchParams();
  const datasetId = parameters.get('dataset_id'), version = parameters.get('version');
  const intentId = parameters.get('intent_id'), requestId = parameters.get('request_id');
  const operationId = parameters.get('operation_id');
  const available = accessState === 'default' && session !== null
    && session.capabilities.some(item => item.capability_id === 'manage_data_use' && item.state === 'available');
  const [dataset, setDataset] = useState<DatasetView | null>(null);
  const [directory, setDirectory] = useState<DatasetList | null>(null);
  const [rules, setRules] = useState<EvaluationRuleProof[]>([]);
  const [tab, setTab] = useState(parameters.get('run_id') ? '评价结果' : tabs[0]);
  const [state, setState] = useState<SupportReadState>('default');
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<'choose' | DatasetOperation | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const [phase, setPhase] = useState<'ready' | 'pending' | 'unknown' | 'failed' | 'saved'>('ready');
  const [request, setRequest] = useState<RequestView | null>(null);
  const [receipt, setReceipt] = useState<WriteReceipt | null>(null);
  const readSequence = useRef(0), writeSequence = useRef(0);
  const reader = useRef<AbortController>(), writer = useRef<AbortController>();
  const busy = useRef(false);
  const loaded = useRef<string | null>(null);
  const prepared = useRef<{ identity: EvaluationIdentity; intent: IntentView | null } | null>(null);
  const leavingNeedsConfirmation = available && (sheet !== null && sheet !== 'choose' || phase === 'pending' || phase === 'unknown');
  const blocker = useBlocker(({ currentLocation, nextLocation }) => leavingNeedsConfirmation && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => { setRules([]); }, [dataset?.version_id, parameters.get('run_id'), available]);
  useEffect(() => {
    function leaving(event: BeforeUnloadEvent) {
      if (!leavingNeedsConfirmation) return;
      event.preventDefault(); event.returnValue = '';
    }
    window.addEventListener('beforeunload', leaving);
    return () => window.removeEventListener('beforeunload', leaving);
  }, [leavingNeedsConfirmation]);

  function readFailure(failure: unknown) {
    setDataset(null); setDirectory(null); loaded.current = null;
    const denied = failure instanceof ApiError && [401, 403].includes(failure.status);
    setState(denied ? 'permission' : navigator.onLine ? 'error' : 'offline');
    setError(failure instanceof Error ? failure.message : '评价资料读取失败。');
    if (denied) { setSheet(null); prepared.current = null; setRequest(null); onRefresh(); }
  }
  const load = useCallback(async () => {
    if (!available) return;
    const current = ++readSequence.current;
    reader.current?.abort(); reader.current = new AbortController();
    setState('loading'); setError(null);
    try {
      const result = datasetId
        ? await readEvaluationDataset(datasetId, version ?? undefined, reader.current.signal)
        : null;
      if (current !== readSequence.current || document.visibilityState !== 'visible') return;
      setDataset(result); loaded.current = datasetId ? `${datasetId}:${version ?? 'current'}` : null; setState('default');
    } catch (failure) {
      if (current === readSequence.current && !reader.current.signal.aborted) readFailure(failure);
    }
  }, [available, datasetId, version, onRefresh]);
  useEffect(() => {
    if (!available) {
      readSequence.current += 1; writeSequence.current += 1; reader.current?.abort(); writer.current?.abort();
      busy.current = false; loaded.current = null; prepared.current = null;
      setDataset(null); setDirectory(null); setSheet(null); setRequest(null); setReceipt(null); setPhase('ready');
      return;
    }
    if (loaded.current !== (datasetId ? `${datasetId}:${version ?? 'current'}` : null)) void load();
  }, [available, datasetId, version, load]);
  useEffect(() => () => {
    readSequence.current += 1; writeSequence.current += 1; reader.current?.abort(); writer.current?.abort();
  }, []);

  async function choose(cursor?: string) {
    if (!available || busy.current || phase === 'unknown' || phase === 'failed' && prepared.current !== null) return;
    setSheet('choose'); setState('loading'); setError(null);
    const current = ++readSequence.current;
    reader.current?.abort(); reader.current = new AbortController();
    try {
      const result = await searchEvaluationDatasets(cursor ? { cursor } : {}, reader.current.signal);
      if (current !== readSequence.current || document.visibilityState !== 'visible') return;
      setDirectory(result); setState('default');
    } catch (failure) {
      if (current === readSequence.current && !reader.current.signal.aborted) readFailure(failure);
    }
  }
  function select(value: DatasetView) {
    reader.current?.abort(); readSequence.current += 1;
    setDataset(value); loaded.current = `${value.dataset_id}:current`; setDirectory(null); setSheet(null);
    setPhase('ready'); setReceipt(null); setRequest(null); prepared.current = null;
    setParameters({ dataset_id: value.dataset_id });
  }
  async function verifySaved(identity: EvaluationIdentity, saved: WriteReceipt, signal: AbortSignal, current: number) {
    setReceipt(saved);
    if (identity.operation_id === 'dataset_run_create') {
      const run = await readEvaluationRun(saved.resource_id, signal);
      if (run.dataset_id !== evaluationRunTarget(identity) || run.dataset_version !== identity.payload.expected_version
          || run.label_version_ref !== identity.payload.label_version_ref || run.task.task_id !== saved.task_id)
        throw new Error('评价运行与准确提交版本或任务不一致。');
      const result = await readEvaluationDataset(run.dataset_id, run.dataset_version, signal);
      if (current !== writeSequence.current || document.visibilityState !== 'visible') return;
      setDataset(result); setState('default'); setPhase('saved'); setRequest(null); setSheet(null); setTab('评价结果');
      loaded.current = `${result.dataset_id}:${result.version}`; prepared.current = null;
      setParameters({ dataset_id: result.dataset_id, version: result.version, run_id: run.run_id, request_id: saved.request_id });
      return;
    }
    const result = await readEvaluationDataset(saved.resource_id, saved.version, signal);
    if (!evaluationPayloadMatches(result, identity)) throw new Error('评价保存内容与提交正文不一致。');
    if (current !== writeSequence.current || document.visibilityState !== 'visible') return;
    setDataset(result); setState('default'); setPhase('saved'); setRequest(null); setSheet(null);
    loaded.current = `${result.dataset_id}:${result.version}`; prepared.current = null;
    setParameters({ dataset_id: result.dataset_id, version: result.version, request_id: saved.request_id });
  }
  async function submit(payload: DatasetPayload) {
    if (!available || busy.current || !sheet || sheet === 'choose') return;
    const operation = sheet;
    if (operation !== 'dataset_create' && !dataset?.allowed_actions.includes(operation)) throw new Error('当前集合不允许此项操作。');
    const base = { request_id: crypto.randomUUID(), schema_version: '2.0.0' as const, target_key: target(operation, dataset?.dataset_id ?? null) };
    const identity: EvaluationIdentity = operation === 'dataset_run_create'
      ? { ...base, operation_id: operation, payload: payload as EvaluationRunInput }
      : { ...base, operation_id: operation, payload: payload as EvaluationIntentInput['payload'] };
    const current = ++writeSequence.current;
    writer.current?.abort(); writer.current = new AbortController(); const signal = writer.current.signal;
    busy.current = true; setError(null); setReceipt(null); setRequest(null);
    let writeAttempted = false;
    try {
      if (identity.operation_id === 'dataset_run_create') evaluationRunTarget(identity); else evaluationTarget(identity);
      prepared.current = { identity, intent: null }; setPhase('pending');
      const next = new URLSearchParams(parameters);
      next.delete('intent_id'); next.set('request_id', identity.request_id); next.set('operation_id', operation);
      setParameters(next, { replace: true });
      const intent = await prepare(identity, signal);
      if (current !== writeSequence.current || document.visibilityState !== 'visible') return;
      prepared.current = { identity, intent }; next.set('intent_id', intent.intent_id); setParameters(next, { replace: true });
      writeAttempted = true;
      const saved = await send(identity, intent, signal);
      if (current !== writeSequence.current) return;
      await verifySaved(identity, saved, signal, current);
    } catch (failure) {
      if (current === writeSequence.current && !signal.aborted) {
        setPhase(writeAttempted ? 'unknown' : prepared.current ? 'failed' : 'ready');
        setError(failure instanceof Error ? failure.message : '评价保存结果尚未核验。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) readFailure(failure);
      }
    } finally { if (current === writeSequence.current) busy.current = false; }
  }
  async function recover() {
    if (!available || busy.current || (!intentId && !requestId)) return;
    const current = ++writeSequence.current;
    writer.current?.abort(); writer.current = new AbortController(); const signal = writer.current.signal;
    busy.current = true; setPhase('pending'); setError(null);
    try {
      if (!operationIds.includes(operationId as DatasetOperation)) throw new Error('恢复评价请求需要准确操作身份。');
      const operation = operationId as DatasetOperation;
      const result = operation === 'dataset_run_create'
        ? intentId ? await readEvaluationRunRequest(intentId, signal) : await findEvaluationRunRequest(requestId!, datasetId!, signal)
        : intentId ? await readEvaluationRequest(intentId, signal) : await findEvaluationRequest(requestId!, operation, target(operation, datasetId), signal);
      if (current !== writeSequence.current || document.visibilityState !== 'visible') return;
      if (result.operation_id !== operation || result.target_key !== target(operation, datasetId)) throw new Error('评价原请求与当前操作身份不一致。');
      const base = { target_key: result.target_key, request_id: result.request_id, schema_version: '2.0.0' as const };
      if (operation === 'dataset_run_create' ? !result.evaluation_run_input : !result.dataset_input) throw new Error('评价原请求正文不可读取。');
      const identity: EvaluationIdentity = operation === 'dataset_run_create'
        ? { ...base, operation_id: operation, payload: result.evaluation_run_input! }
        : { ...base, operation_id: operation, payload: result.dataset_input! };
      prepared.current = { identity, intent: null }; setRequest(result);
      if (result.receipt) await verifySaved(identity, result.receipt, signal, current);
      else {
        setPhase(result.state === 'failed' ? 'failed' : 'unknown');
        setError(result.error_code ? `原请求状态：${result.error_code}` : '原请求尚未取得保存回执。');
      }
    } catch (failure) {
      if (current === writeSequence.current && !signal.aborted) {
        setPhase(prepared.current?.intent === null && failure instanceof ApiError && failure.status === 404 ? 'failed' : 'unknown');
        setError(failure instanceof Error ? failure.message : '评价原请求查询失败。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) readFailure(failure);
      }
    } finally { if (current === writeSequence.current) busy.current = false; }
  }
  useEffect(() => {
    if (available && phase === 'ready' && (intentId || requestId && operationId)) void recover();
  }, [available, intentId, requestId, operationId]);
  async function retry() {
    if (!available || busy.current || !request?.may_retry || !prepared.current) return;
    const current = ++writeSequence.current, identity = prepared.current.identity;
    writer.current?.abort(); writer.current = new AbortController(); const signal = writer.current.signal;
    busy.current = true; setPhase('pending'); setError(null);
    try {
      const intent = identity.operation_id === 'dataset_run_create'
        ? await retryEvaluationRunRequest(request, signal) : await retryEvaluationRequest(request, signal);
      if (current !== writeSequence.current || document.visibilityState !== 'visible') return;
      prepared.current = { identity, intent };
      const saved = await send(identity, intent, signal);
      if (current === writeSequence.current) await verifySaved(identity, saved, signal, current);
    } catch (failure) {
      if (current === writeSequence.current && !signal.aborted) {
        setPhase('unknown'); setError(failure instanceof Error ? failure.message : '原评价请求重试结果尚未核验。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) readFailure(failure);
      }
    } finally { if (current === writeSequence.current) busy.current = false; }
  }
  async function resume() {
    const mayPrepare = phase === 'failed' && request === null && prepared.current?.intent === null;
    if (!available || busy.current || !prepared.current || !mayPrepare && (request?.state !== 'pending' || request.may_retry)) return;
    const current = ++writeSequence.current, identity = prepared.current.identity;
    writer.current?.abort(); writer.current = new AbortController(); const signal = writer.current.signal;
    busy.current = true; setPhase('pending'); setError(null);
    let writeAttempted = false;
    try {
      const intent = await prepare(identity, signal);
      if (current !== writeSequence.current || document.visibilityState !== 'visible') return;
      prepared.current = { identity, intent };
      writeAttempted = true;
      const saved = await send(identity, intent, signal);
      if (current === writeSequence.current) await verifySaved(identity, saved, signal, current);
    } catch (failure) {
      if (current === writeSequence.current && !signal.aborted) {
        setPhase(writeAttempted ? 'unknown' : 'failed'); setError(failure instanceof Error ? failure.message : '继续原提交的结果尚未核验。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) readFailure(failure);
      }
    } finally { if (current === writeSequence.current) busy.current = false; }
  }
  const close = useCallback(() => {
    if (busy.current) return;
    if (sheet && sheet !== 'choose' && phase === 'ready') setConfirmClose(true);
    else { setSheet(null); setConfirmClose(false); }
  }, [sheet, phase]);
  const visible = available && state === 'default';
  const pageState = accessState !== 'default' ? accessState : !available ? 'permission' : state;
  const restricted = visible && dataset && dataset.coverage.unrecorded_units.length > 0;
  const metrics = dataset && visible ? [
    `${count(dataset.counts.files)} / ${count(dataset.counts.pages)}`, count(dataset.counts.patients),
    `${count(dataset.counts.sampling_events)} / ${count(dataset.counts.mas_events)}`, count(dataset.counts.observation_periods),
  ] : ['未统计', '未统计', '未统计', '未统计'];
  const facts = dataset && visible ? [purposes[dataset.coverage.purpose],
    dataset.coverage.units.map(item => `${item.unit}：${item.state === 'complete' ? '完整' : item.state === 'partial' ? '部分' : '未记录'}`).join('；'),
    `开发集合 ${dataset.partitions.filter(item => item.partition === 'development').length}；独立验证集合 ${dataset.partitions.filter(item => item.partition === 'validation').length}`,
    dataset.label_version_ref ?? '待确认', rules.length ? rules.map(rule => `${rule.config_key} · 版本 ${rule.version}`).join('；') : '待确认',
    dataset.coverage.units.flatMap(item => item.limitations).join('；') || (restricted ? '仍有未记录统计单位' : '未登记额外范围'),
  ] : Array<string>(6).fill('待确认');
  return <UI2Frame title="资料与评价集合" screenId="UI2-37" note="真实来源、独立答案与完整分母共同保留。"
    onBack={() => navigate('/support/data-use')} overlay={available && (sheet || blocker.state === 'blocked') ? <UI2Sheet title={blocker.state === 'blocked' ? '离开当前操作' : sheet === 'choose' ? '当前资料范围' : datasetOperationNames[sheet!]}
      onClose={blocker.state === 'blocked' ? () => blocker.reset() : close}>
      {blocker.state === 'blocked' && <><p>{phase === 'pending' || phase === 'unknown' ? '原请求记录继续保留。离开页面不会停止已提交操作，可以通过当前链接查询结果。' : '离开当前编辑将清除尚未提交的输入。'}</p>
        <button className="button secondary" onClick={() => blocker.reset()}>继续当前操作</button>
        <button className="text-action" onClick={() => blocker.proceed()}>确认离开</button></>}
      <div hidden={blocker.state === 'blocked'}>
      {sheet === 'choose' ? <>
        <SupportReadStatus state={state} error={error} onRefresh={() => void choose()} />
        {state === 'default' && directory && <>
          {directory.items.length === 0 && <p>当前获准范围内尚无评价集合。</p>}
          {directory.items.map(item => <button className="text-action" key={item.dataset_id} onClick={() => select(item)}>
            {item.protocol_ref} · 版本 {item.version} · {item.status === 'frozen' ? '已冻结' : '编辑中'}
          </button>)}
          {directory.next_cursor && <button className="text-action" onClick={() => void choose(directory.next_cursor!)}>下一页</button>}
          <button className="text-action" onClick={() => { loaded.current = null; setDataset(null); setParameters({}); setSheet('dataset_create'); setPhase('ready'); setConfirmClose(false); }}>登记新的用途清单</button>
        </>}
        {dataset && phase !== 'unknown' && phase !== 'pending' && dataset.allowed_actions.map(action => operationIds.includes(action as DatasetOperation)
          ? <button className="text-action" key={action} onClick={() => { setSheet(action as DatasetOperation); setPhase('ready'); setConfirmClose(false); }}>{datasetOperationNames[action as DatasetOperation]}</button> : null)}
      </> : sheet ? <>
        {confirmClose && <><p>离开当前编辑将清除尚未提交的输入。</p>
          <button className="button secondary" onClick={() => setConfirmClose(false)}>继续编辑</button>
          <button className="text-action" onClick={() => { setSheet(null); setConfirmClose(false); }}>确认离开</button></>}
        <div hidden={confirmClose}><DatasetEditor key={`${sheet}:${dataset?.version_id ?? 'new'}`} operation={sheet} dataset={dataset}
          pending={phase === 'pending' || phase === 'unknown' || phase === 'failed' && prepared.current !== null} onSubmit={payload => void submit(payload)} /></div>
        {error && <p role="alert">{error}</p>}
        {(phase === 'unknown' || phase === 'failed') && <button className="text-action" onClick={() => void recover()}>查询原请求</button>}
        {request?.may_retry && <button className="text-action" disabled={busy.current} onClick={() => void retry()}>重试原请求</button>}
        {request?.state === 'pending' && !request.may_retry && <button className="text-action" disabled={busy.current} onClick={() => void resume()}>继续原提交</button>}
        {phase === 'failed' && request === null && prepared.current?.intent === null && <button className="text-action" disabled={busy.current} onClick={() => void resume()}>重新准备原请求</button>}
      </> : null}
      </div>
    </UI2Sheet> : undefined}>
    <SupportReadStatus state={pageState} error={accessState !== 'default' ? accessError : error} onRefresh={available ? () => void load() : onRefresh} />
    <div className="support-banner"><LockKeyhole size={16} />受控支持 · 需独立岗位授权</div>
    <section className="card form-card"><div className="eyebrow">指定范围内操作</div><h2>资料与评价集合</h2>
      <p className="muted">真实来源、独立答案与完整分母共同保留。</p>
      <div className="pair"><span>当前资料范围</span><strong>{available
        ? <button className="text-action" disabled={phase === 'pending' || phase === 'unknown' || phase === 'failed' && prepared.current !== null} onClick={() => void choose()}>{visible && dataset ? `${dataset.protocol_ref} · 版本 ${dataset.version}` : '选择获准资料'}</button>
        : '尚未授权选择'}</strong></div>
    </section>
    <div className="segments" role="tablist">{tabs.map(value => <button role="tab" aria-selected={tab === value} className={tab === value ? 'selected' : ''}
      key={value} onClick={() => setTab(value)}>{value}</button>)}</div>
    <div className="metric-grid">{['文件与页面', '独立患儿', '采样与事件', '观察区间'].map((value, index) => <div key={value}><span>{value}</span><strong>{metrics[index]}</strong></div>)}</div>
    <section className="card form-card">{['可用用途', '资料形态', '患儿集合划分', '独立参考答案', '固定规则版本', '无法评估范围'].map((value, index) =>
      <div className="pair" key={value}><span>{value}</span><strong>{facts[index]}</strong></div>)}</section>
    {visible && dataset && tab === '独立标签' && <section className="card form-card">
      {dataset.labels.length === 0 ? <p>当前集合尚未确认独立标签。</p> : dataset.labels.map(item => <div className="pair" key={item.label_id}>
        <span>{item.patient_id} · {item.observation_period_id}</span><strong>{labels[item.label_state]}</strong>
      </div>)}
    </section>}
    {visible && dataset && tab === '评价结果' && <EvaluationRunPanel dataset={dataset} runId={parameters.get('run_id')} onPermission={onRefresh} onRules={setRules} />}
    {restricted && <section className="status-panel partial" role="status"><h2>资料覆盖尚未完整</h2><p>未记录统计量保持未知，评价范围按准确来源及独立答案核验。</p></section>}
    {visible && version && <section className="status-panel stale" role="status"><h2>正在查看准确保存版本</h2>
      <p>当前页面绑定版本 {version}。继续编辑前需要读取最新资料。</p><button className="text-action" onClick={() => { loaded.current = null; setParameters({ dataset_id: datasetId! }); }}>读取最新版本</button></section>}
    {available && (phase === 'pending' || phase === 'unknown' || phase === 'failed') && <section className="status-panel" role="status">
      <h2>{phase === 'pending' ? '正在核验提交' : receipt ? '保存已确认，资料读取尚未完成' : phase === 'failed' && prepared.current?.intent === null ? '提交准备未完成' : '保存结果尚未确认'}</h2><p>{error}</p>
      {phase !== 'pending' && <button className="text-action" onClick={() => void recover()}>查询原请求</button>}
      {request?.may_retry && <button className="text-action" onClick={() => void retry()}>重试原请求</button>}
      {request?.state === 'pending' && !request.may_retry && <button className="text-action" onClick={() => void resume()}>继续原提交</button>}
      {phase === 'failed' && request === null && prepared.current?.intent === null && <button className="text-action" onClick={() => void resume()}>重新准备原请求</button>}
    </section>}
    {available && phase === 'saved' && <section className="status-panel success" role="status"><h2>{receipt?.resource_type === 'evaluation_run' ? '评价运行已受理并重新读取' : '资料已保存并重新读取'}</h2><p>当前显示准确资料版本 {dataset?.version}。</p></section>}
    <div className="note blue"><ShieldCheck size={17} /><p>未记录 MAS 不自动标为阴性。资料数量不能代替临床样本量。</p></div>
  </UI2Frame>;
}
