import { SummaryTrend } from './SummaryTrend';
import { SummarySnapshotPresentation } from './SummarySnapshotPresentation';
import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ChevronRight as ArrowRight, ArrowDown as ChevronDown, ChevronRight, ShieldCheck } from '../../components/ThreeDIcon';
import { FileText, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useSummaryResource } from '../../api/useSummaryResource';
import { prepareSummary, submitSummary, readSummary, readSummaryRequest, readSummaryTask, retrySummary,
  waitForSummaryTask, cancelSummaryTask, type SummaryTask,
  verifySummaryScope, summaryTarget, type SummaryInput, type SummaryIntent, type SummaryKind,
  type SummaryList, type SummaryRequest, type SummaryRequestList, type SummaryView } from '../../api/summaries';

const kinds: Array<[SummaryKind, string, string]> = [
  ['observation', '体温与可观察症状', '体温与症状'], ['report', '检验原值与单位', '检验报告'], ['therapy', '实际治疗记录', '治疗资料'],
];

function SummaryPatient() {
  const access = useAccess(), navigate = useNavigate();
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  return access.principalId && relation && !access.loading && !access.error ? <button className="patient rich-record-patient" onClick={() => navigate('/associations')}>
    <span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation.display_id}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} />
  </button> : null;
}

export function UI2SummariesPage() {
  const access = useAccess();
  return <SummaryForm key={`${access.principalId}:${access.patientId}`} />;
}

function SummaryForm() {
  const access = useAccess(), navigate = useNavigate();
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const owner = canRead ? `${access.principalId}:${access.patientId}` : '';
  const path = canRead && access.patientId ? summaryTarget(access.patientId) : null;
  const list = useSummaryResource<SummaryList>(path, owner, access.patientId, 'SummaryList');
  const directory = useSummaryResource<SummaryRequestList>(canRead ? `/api/patients/${access.patientId}/requests?operation_id=summary_create` : null,
    owner, access.patientId, 'SummaryRequestList');
  const [from, setFrom] = useState(''), [to, setTo] = useState('');
  const [selected, setSelected] = useState<SummaryKind[]>(['observation', 'report', 'therapy']);
  const [source, setSource] = useState(true), [unknownTime, setUnknownTime] = useState(false);
  const [error, setError] = useState<Error | null>(null), [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState<{ requestId: string; input: SummaryInput; intent: SummaryIntent | null } | null>(null);
  const [activeTask, setActiveTask] = useState<SummaryTask | null>(null);
  const controller = useRef<AbortController | null>(null), composing = useRef(false), running = useRef(false);
  const requests = canRead ? directory.data?.requests : undefined;
  const pending = requests?.filter(request => request.state !== 'committed'
    || (request.task && ['accepted', 'queued', 'running'].includes(request.task.status)));
  const frozen = !canRead || busy || attempt !== null || !!pending?.length;
  useEffect(() => {
    if (!canRead) { controller.current?.abort(); setAttempt(null); setActiveTask(null); setError(null); setBusy(false); }
    const visibility = () => {
      if (document.visibilityState !== 'visible') { controller.current?.abort(); setBusy(false); setActiveTask(null); }
    };
    document.addEventListener('visibilitychange', visibility);
    return () => { controller.current?.abort(); document.removeEventListener('visibilitychange', visibility); };
  }, [canRead]);

  const adopt = (input: SummaryInput) => {
    setFrom(input.from_date); setTo(input.to_date); setSelected(input.record_kinds);
    setSource(input.include_source_status); setUnknownTime(input.include_unknown_time);
  };
  const complete = async (summaryId: string, input: SummaryInput, requestId: string, signal: AbortSignal, taskId?: string) => {
    if (!access.patientId) throw new Error('尚未选择当前患儿。');
    const saved = await readSummary(access.patientId, summaryId, signal);
    verifySummaryScope(saved, input);
    const task = await readSummaryTask(saved.task_id, signal);
    if (task.status !== 'completed' || task.request_id !== requestId || task.result_type !== 'summary' || task.result_ref !== saved.summary_id
      || (taskId && task.task_id !== taskId)) throw new Error('摘要与实际完成任务尚未通过核验。');
    if (!signal.aborted) { setAttempt(null); navigate(`/summaries/${saved.summary_id}`); }
  };
  const send = async (current: { requestId: string; input: SummaryInput; intent: SummaryIntent | null }, signal: AbortSignal) => {
    if (!access.patientId || !access.csrfToken) throw new Error('摘要提交需要当前身份及患儿权限。');
    const intent = current.intent ?? await prepareSummary(access.patientId, current.requestId, current.input, access.csrfToken, signal);
    if (signal.aborted) return;
    setAttempt({ ...current, intent });
    await follow(await submitSummary(access.patientId, intent, current.input, access.csrfToken, signal),
      current.input, current.requestId, signal);
  };
  const follow = async (initial: SummaryTask, input: SummaryInput, requestId: string, signal: AbortSignal) => {
    const task = await waitForSummaryTask(initial, requestId, signal, value => { if (!signal.aborted) setActiveTask(value); });
    if (task.status !== 'completed' || task.result_type !== 'summary' || !task.result_ref) {
      if (['failed', 'cancelled', 'superseded'].includes(task.status)) { setAttempt(null); setActiveTask(null); }
      throw new Error(task.status === 'cancelled' ? '摘要任务已取消，可重新选择范围。' :
        `摘要未完成${task.error_code ? `（${task.error_code}）` : ''}，请核对权限和资料后重新生成。`);
    }
    await complete(task.result_ref, input, requestId, signal, task.task_id);
  };
  const operate = async (operation: (signal: AbortSignal) => Promise<void>) => {
    if (running.current || !canRead || !access.csrfToken || composing.current) return;
    running.current = true; setBusy(true); setError(null);
    controller.current?.abort(); const current = new AbortController(); controller.current = current;
    await operation(current.signal).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!current.signal.aborted) setError(failure);
    }).finally(() => {
      running.current = false;
      if (!current.signal.aborted) { setBusy(false); directory.reload(); list.reload(); }
    });
  };
  const recover = async (original: SummaryRequest, signal: AbortSignal) => {
    if (!access.patientId || !access.csrfToken) throw new Error('摘要查询需要当前身份及患儿权限。');
    const request = await readSummaryRequest(access.patientId, original.intent_id, signal);
    const input = request.summary_input;
    if (input === null) throw new Error('原请求没有保存完整范围，当前不能重新提交。');
    adopt(input);
    if (request.state === 'committed') {
      if (!request.receipt || request.receipt.request_id !== request.request_id) throw new Error('原请求缺少准确保存回执。');
      if (request.receipt.resource_type === 'task') {
        await follow(await readSummaryTask(request.receipt.resource_id, signal), input, request.request_id, signal);
      } else await complete(request.receipt.resource_id, input, request.request_id, signal);
      return;
    }
    const current = { requestId: request.request_id, input, intent: null as SummaryIntent | null };
    setAttempt(current);
    if (request.may_retry) current.intent = await retrySummary(request, access.csrfToken, signal);
    else {
      if (request.state !== 'pending' || Date.parse(request.expires_at) <= Date.now()) throw new Error('原请求当前不允许继续提交。');
      current.intent = { intent_id: request.intent_id, request_id: request.request_id, generation: request.generation,
        status: 'prepared', expires_at: request.expires_at, payload_sha256: request.payload_sha256 };
    }
    await send(current, signal);
  };
  const generate = () => {
    if (running.current || frozen || !directory.data || !access.csrfToken || !from || !to || from > to || !selected.length) return;
    const input: SummaryInput = { from_date: from, to_date: to, scope_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      include_unknown_time: unknownTime, record_kinds: [...selected], include_source_status: source };
    const current = { requestId: crypto.randomUUID(), input, intent: null };
    setAttempt(current); void operate(signal => send(current, signal));
  };
  return <UI2FamilyFrame title="就医摘要" screenId="UI2-13" note="按照患儿与时间范围选择当前可见资料。" backTo="/records">
    <SummaryPatient /><UI2ReadState loading={directory.loading} error={directory.error} onRetry={directory.reload} />
    <section className="summary-intro"><FileText size={48} /><h2>把就诊资料整理好</h2><p>保留原始记录与来源，方便就诊沟通。</p></section>
    <form style={{ display: 'contents' }} onSubmit={event => { event.preventDefault(); if (!composing.current) generate(); }}
      onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
      <section className="card form-card"><div className="field-grid">
        <label className="field"><span>开始日期</span><input type="date" autoComplete="off" placeholder="尚未填写" value={from} required disabled={frozen} onChange={event => setFrom(event.target.value)} /></label>
        <label className="field"><span>结束日期</span><input type="date" autoComplete="off" placeholder="尚未填写" value={to} min={from || undefined} required disabled={frozen} onChange={event => setTo(event.target.value)} /></label>
      </div></section><section className="card form-card"><div className="section-heading"><h2>本次包含</h2></div>
        {kinds.map(([kind, label]) => <label className="checkbox-row" key={kind}><input type="checkbox" checked={selected.includes(kind)} disabled={frozen}
          onChange={event => setSelected(values => event.target.checked ? [...values, kind] : values.filter(value => value !== kind))} /><span>{label}</span></label>)}
        <label className="checkbox-row"><input type="checkbox" checked={source} disabled={frozen} onChange={event => setSource(event.target.checked)} /><span>来源与核对状态</span></label>
      </section>
      <div className="note blue"><ShieldCheck size={17} /><p>仅使用当前有权查看的资料，不自动生成诊断或用药建议。</p></div>
      <button className="button" type="submit" disabled={frozen || !directory.data || !access.csrfToken || !from || !to || from > to || !selected.length}>
        {busy ? '正在核验生成结果' : '生成就医摘要'}</button>
    </form>
    <ContextLink className="text-action center" to="/summaries/structure">查看摘要页面结构<ArrowRight size={15} /></ContextLink>
    <details className="card form-card"><summary>时间与资料范围说明</summary>
      <label className="checkbox-row"><input type="checkbox" checked={unknownTime} disabled={frozen} onChange={event => setUnknownTime(event.target.checked)} /><span>包含医学时间尚未确定的资料</span></label>
      <p>日期按当前浏览器时区核验。获准临床资料按当前权限纳入；原始记录与版本持续保留。</p>
      <p>“来源与核对状态”控制摘要内的说明展示，具体依据版本始终可以查询。</p>
    </details>
    {error && <section className="status-panel error" role="alert"><h2>生成结果尚未确认</h2><p>{error.message}</p><p>通过原请求核验保存结果。</p></section>}
    {activeTask && ['accepted', 'queued', 'running'].includes(activeTask.status) && <section className="card form-card" role="status">
      <h2>{activeTask.status === 'running' ? '正在生成摘要' : '摘要任务已接收'}</h2><p>可以离开后在原请求中继续查询，不必重复提交。</p>
      <button className="button secondary" disabled={!canRead || !access.csrfToken} onClick={() => {
        const signal = controller.current?.signal;
        if (!signal || signal.aborted || !access.csrfToken) return;
        void cancelSummaryTask(activeTask.task_id, access.csrfToken, signal).then(() => {
          if (signal.aborted) return;
          controller.current?.abort(); setBusy(false); setActiveTask(null); setAttempt(null);
          directory.reload(); list.reload();
        }).catch(failure => { if (!signal.aborted) setError(failure instanceof Error ? failure : new Error('取消结果未知，请查询原请求。')); });
      }}>取消本次生成</button></section>}
    {canRead && attempt && <section className="card form-card"><h2>查询当前生成请求</h2><p>本次范围和请求保持一致。</p>
      <button className="button secondary" disabled={busy || !access.csrfToken} onClick={() => void operate(async signal => {
        if (attempt.intent) {
          const current = await readSummaryRequest(access.patientId!, attempt.intent.intent_id, signal);
          await recover(current, signal);
        } else await send(attempt, signal);
      })}>查询原请求并继续</button></section>}
    {canRead && requests && requests.length > 0 && <details className="card form-card" open={!!pending?.length}><summary>已保存的生成请求</summary>
      {requests.map(request => <section key={request.intent_id} className="summary-section"><h3>{request.state === 'committed' ? '请求已接收，可查询处理结果' : '尚待核验结果'}</h3>
        <p>{eventTimeLabel(request.prepared_at)}</p>
        {request.summary_input && <p>{request.summary_input.from_date} 至 {request.summary_input.to_date} · {request.summary_input.scope_timezone}</p>}
        <button className="text-action" disabled={busy || !access.csrfToken || request.summary_input === null || (attempt !== null && attempt.requestId !== request.request_id)}
          onClick={() => void operate(signal => recover(request, signal))}>查询原请求并继续<ArrowRight size={15} /></button>
      </section>)}
    </details>}
    {canRead && <section className="card form-card" aria-labelledby="generated-summaries-heading"><h2 id="generated-summaries-heading">已生成的摘要</h2>
      <UI2ReadState loading={list.loading} error={list.error} onRetry={list.reload} />
      {list.data?.summaries.length === 0 && <p>尚无已生成的摘要</p>}
      {list.data?.summaries.map(summary => <ContextLink className="list-row" key={summary.summary_id} to={`/summaries/${summary.summary_id}`}>
        <span className="row-text"><strong>{summary.from_date} 至 {summary.to_date}</strong><small>{eventTimeLabel(summary.generated_at)} · {summary.needs_regeneration ? '需要重新生成' : '查看摘要'}</small></span><ChevronRight size={17} />
      </ContextLink>)}
    </section>}
  </UI2FamilyFrame>;
}

export function UI2SummaryDetailPage() {
  const access = useAccess();
  const { summaryId } = useParams(), navigate = useNavigate();
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const structure = summaryId === 'structure';
  const resource = useSummaryResource<SummaryView>(canRead && summaryId && !structure ? `/api/summaries/${summaryId}` : null,
    canRead ? `${access.principalId}:${access.patientId}` : '', access.patientId, 'SummaryView', !structure ? summaryId : undefined);
  const summary = canRead ? resource.data : null;
  const available = summary && summary.validity === 'valid' && !summary.needs_regeneration;
  return <UI2FamilyFrame title="就医摘要" screenId="UI2-14" note="原始事实和来源适合手机当面阅读。" backTo="/summaries">
    <SummaryPatient /><UI2ReadState loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    <SummarySnapshotPresentation summary={summary} trend={available ? <SummaryTrend summary={summary} /> : undefined} />
    {summary && !available && <div className="note blue" role="alert"><ShieldCheck size={17} /><p>依据或可见范围已经变化。重新生成后再用于就医沟通。</p></div>}
    <button className="button secondary" onClick={() => navigate('/summaries')}>重新选择范围</button>
    {summary && <details className="card form-card"><summary>查询生成依据</summary>
      <div className="pair"><span>摘要标识</span><strong>{summary.summary_id}</strong></div>
      <div className="pair"><span>生成任务</span><strong>{summary.task_id}</strong></div>
      <div className="pair"><span>模板版本</span><strong>{summary.template_id}</strong></div>
      <div className="pair"><span>可见策略版本</span><strong>{summary.visibility_policy_id}</strong></div>
      <p>{summary.environment === null ? '原任务未保存生成环境。' : `生成环境：${summary.environment}`}</p>
      <button className="text-action" onClick={resource.reload}>重新读取保存结果<ArrowRight size={15} /></button>
    </details>}
  </UI2FamilyFrame>;
}
