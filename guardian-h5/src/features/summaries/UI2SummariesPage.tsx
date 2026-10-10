import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ArrowRight, ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react';
import { FileText, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useSummaryResource } from '../../api/useSummaryResource';
import { prepareSummary, submitSummary, readSummary, readSummaryRequest, readSummaryTask, retrySummary,
  verifySummaryScope, summaryTarget, type SummaryInput, type SummaryIntent, type SummaryKind,
  type SummaryList, type SummaryRequest, type SummaryRequestList, type SummaryView } from '../../api/summaries';
import { fieldLabel, fieldValue, originLabels, timeLabel, verificationLabel } from '../../api/records';

const kinds: Array<[SummaryKind, string, string]> = [
  ['observation', '体温与可观察症状', '体温与症状'], ['report', '检验原值与单位', '检验报告'], ['therapy', '实际治疗记录', '治疗资料'],
];

function SummaryPatient() {
  const access = useAccess(), navigate = useNavigate();
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  return access.principalId && relation && !access.loading && !access.error ? <button className="patient" onClick={() => navigate('/associations')}>
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
  const controller = useRef<AbortController | null>(null), composing = useRef(false), running = useRef(false);
  const requests = canRead ? directory.data?.requests : undefined;
  const pending = requests?.filter(request => request.state !== 'committed');
  const frozen = !canRead || busy || attempt !== null || !!pending?.length;
  useEffect(() => {
    if (!canRead) { controller.current?.abort(); setAttempt(null); setError(null); setBusy(false); }
    const visibility = () => {
      if (document.visibilityState !== 'visible') { controller.current?.abort(); setBusy(false); }
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
    const task = await submitSummary(access.patientId, intent, current.input, access.csrfToken, signal);
    if (task.status !== 'completed' || task.result_type !== 'summary' || !task.result_ref) throw new Error('生成结果尚未确认，请查询原请求。');
    await complete(task.result_ref, current.input, current.requestId, signal, task.task_id);
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
      await complete(request.receipt.resource_id, input, request.request_id, signal);
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
  return <UI2FamilyFrame title="准备就医摘要" screenId="UI2-13" note="按照患儿与时间范围选择当前可见资料。" backTo="/records">
    <SummaryPatient /><UI2ReadState loading={directory.loading} error={directory.error} onRetry={directory.reload} />
    <section className="summary-intro"><FileText size={48} /><h2>带着清楚的记录去沟通</h2><p>原始内容、时间与来源，一起保留。</p></section>
    <form style={{ display: 'contents' }} onSubmit={event => { event.preventDefault(); if (!composing.current) generate(); }}
      onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
      <section className="card form-card"><div className="field-grid">
        <label className="field"><span>开始日期</span><input type="date" autoComplete="off" placeholder="尚未填写" value={from} required disabled={frozen} onChange={event => setFrom(event.target.value)} /></label>
        <label className="field"><span>结束日期</span><input type="date" autoComplete="off" placeholder="尚未填写" value={to} min={from || undefined} required disabled={frozen} onChange={event => setTo(event.target.value)} /></label>
      </div><div className="section-heading"><h2>包含的资料</h2></div>
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
    {canRead && attempt && <section className="card form-card"><h2>查询当前生成请求</h2><p>本次范围和请求保持一致。</p>
      <button className="button secondary" disabled={busy || !access.csrfToken} onClick={() => void operate(async signal => {
        if (attempt.intent) {
          const current = await readSummaryRequest(access.patientId!, attempt.intent.intent_id, signal);
          await recover(current, signal);
        } else await send(attempt, signal);
      })}>查询原请求并继续</button></section>}
    {canRead && requests && requests.length > 0 && <details className="card form-card" open={!!pending?.length}><summary>已保存的生成请求</summary>
      {requests.map(request => <section key={request.intent_id} className="summary-section"><h3>{request.state === 'committed' ? '已经保存结果' : '尚待核验结果'}</h3>
        <p>{new Date(request.prepared_at).toLocaleString('zh-CN')}</p>
        {request.summary_input && <p>{request.summary_input.from_date} 至 {request.summary_input.to_date} · {request.summary_input.scope_timezone}</p>}
        <button className="text-action" disabled={busy || !access.csrfToken || request.summary_input === null || (attempt !== null && attempt.requestId !== request.request_id)}
          onClick={() => void operate(signal => recover(request, signal))}>查询原请求并继续<ArrowRight size={15} /></button>
      </section>)}
    </details>}
    {canRead && <details className="card form-card"><summary>已生成的摘要</summary>
      <UI2ReadState loading={list.loading} error={list.error} onRetry={list.reload} />
      {list.data?.summaries.length === 0 && <p>尚无已生成的摘要</p>}
      {list.data?.summaries.map(summary => <ContextLink className="list-row" key={summary.summary_id} to={`/summaries/${summary.summary_id}`}>
        <span className="row-text"><strong>{summary.from_date} 至 {summary.to_date}</strong><small>{new Date(summary.generated_at).toLocaleString('zh-CN')} · {summary.needs_regeneration ? '需要重新生成' : '查看摘要'}</small></span><ChevronRight size={17} />
      </ContextLink>)}
    </details>}
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
    <section className="card summary-paper"><div className="eyebrow">就医沟通资料</div><h2>就医摘要</h2>
      <p className="muted">{summary ? available ? '按生成时的原始资料与版本阅读' : '当前内容需要重新生成' : '尚未生成有效内容'}</p>
      <div className="pair"><span>患儿与时间范围</span><strong>{summary ? `${summary.display_id} · ${summary.from_date} 至 ${summary.to_date}` : '待选择'}</strong></div>
      <div className="pair"><span>生成时间</span><strong>{summary ? new Date(summary.generated_at).toLocaleString('zh-CN') : '尚未生成'}</strong></div>
      {summary && <><div className="pair"><span>范围时区</span><strong>{summary.scope_timezone}</strong></div>
        <div className="pair"><span>未知医学时间</span><strong>{summary.include_unknown_time ? '纳入并保留未知状态' : '未选择纳入'}</strong></div></>}
      {kinds.map(([kind, , title]) => <div className="summary-section" key={kind}><h3>{title}</h3>
        {available ? <SummaryFields summary={summary} kind={kind} /> : <p>尚无当前可见资料</p>}
      </div>)}
      {available && summary.items.some(item => item.snapshot.kind === 'professional') && <div className="summary-section"><h3>获准临床资料</h3><SummaryFields summary={summary} kind="professional" /></div>}
      <div className="summary-section"><h3>资料来源与缺项</h3>
        {!available ? <p>尚无当前可见资料</p> : <>
          <p>{summary.items.some(item => item.snapshot.field.value_state !== 'known' || !item.snapshot.field.medical_time
            || item.snapshot.field.medical_time.precision === 'unknown') ? '部分原值或医学时间尚未确定，具体项目保留原有状态。' : '具体依据版本随各项资料保留。'}</p>
          {summary.include_source_status === false && <p>本次未选择来源与核对状态说明；具体依据版本仍可查询。</p>}
          {summary.items.some(item => item.snapshot.confirmation_snapshot_state === 'not_recorded') && <p>部分历史项目未保存本人确认证明，当前状态不能推断。</p>}
          {summary.items.some(item => item.snapshot.source_anchor_snapshot_state === 'not_recorded') && <p>部分历史项目未保存原件页码，具体记录版本仍可查询。</p>}
        </>}
      </div>
    </section>
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

function SummaryFields({ summary, kind }: { summary: SummaryView; kind: string }) {
  const items = summary.items.filter(item => item.snapshot.kind === kind);
  if (items.length === 0) return <p>{summary.record_kinds && kind !== 'professional' && !summary.record_kinds.includes(kind as SummaryKind)
    ? '本次未选择此类资料' : '此范围没有当前可见的资料'}</p>;
  return <>{items.map(item => {
    const { field } = item.snapshot;
    return <article className="summary-section" key={item.field_id}><h4>{fieldLabel(field)}</h4><p>{fieldValue(field)}</p>
      <div className="pair"><span>医学时间</span><strong>{timeLabel(field.medical_time)}</strong></div>
      {field.raw_text !== null && field.raw_numeric !== null && <div className="pair"><span>原始文本</span><strong>{field.raw_text}</strong></div>}
      {field.method !== null && <div className="pair"><span>检验方法</span><strong>{field.method}</strong></div>}
      {field.reference_interval !== null && <div className="pair"><span>参考区间</span><strong>{field.reference_interval}</strong></div>}
      {field.report_flag !== null && <div className="pair"><span>报告标记</span><strong>{field.report_flag}</strong></div>}
      {summary.include_source_status !== false && <>
        <div className="pair"><span>资料来源</span><strong>{originLabels[item.snapshot.origin_type] ?? item.snapshot.origin_type}</strong></div>
        <div className="pair"><span>来源机构</span><strong>{item.snapshot.source_organization ?? '未提供'}</strong></div>
        <div className="pair"><span>专业核对</span><strong>{verificationLabel(field)}</strong></div>
        <div className="pair"><span>本人确认证明</span><strong>{item.snapshot.confirmation_snapshot_state === 'not_recorded' ? '原摘要未保存' : field.confirmation_id ? '已保存确认证明' : '尚无确认证明'}</strong></div>
        {field.source_anchor && <div className="pair"><span>来源页码</span><strong>第 {field.source_anchor.page_ordinal} 页</strong></div>}
      </>}
      <ContextLink className="text-action" to={`/records/versions/${item.record_version_id}`}>查看摘要依据版本<ArrowRight size={15} /></ContextLink>
    </article>;
  })}</>;
}
