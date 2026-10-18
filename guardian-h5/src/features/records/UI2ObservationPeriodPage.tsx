import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight } from '../../components/ThreeDIcon';
import { CalendarDays, LockKeyhole, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { ApiError } from '../../api/client';
import { preparePeriod, readObservationPeriod, readPeriodRequest, retryPeriod, submitPeriod } from '../../api/observationPeriods';
import { documentTimeLabel } from '../archives/documentTimeLabel';
import { assertionStateLabel, comparisonSymbol, factContextLabel, verificationStatusLabel } from '../../components/factPresentation';
import type { IntentView, MedicalTimeInput } from '../../../../frontend-design/types';
import type { ObservationPeriodView, PeriodIdentity, PeriodRequest } from '../../../../frontend-design/observation-period-types';

const reasons: Record<string, string> = {
  OBSERVATION_PERIOD_NOT_SELECTED: '尚未保存观察范围。', OBSERVATION_PERIOD_TIME_UNKNOWN: '观察范围的时间尚未明确。',
  MEDICAL_TIME_PRECISION_INSUFFICIENT: '时间精度尚不足以判断准确间隔。', MEDICAL_TIME_UNRESOLVED: '医学时间尚未取得明确时区和时刻。',
  FACT_MEDICAL_TIME_UNKNOWN: '该项医学时间尚未明确。', FACT_OUTSIDE_OBSERVATION_PERIOD: '该项医学时间位于观察范围之外。',
  FACT_OVERLAPS_OBSERVATION_BOUNDARY: '该项时间范围与观察边界相交，尚不能完整采用。',
  SAMPLING_FREQUENCY_POLICY_UNAVAILABLE: '尚无当前获准的采样间隔依据。', SAMPLING_FREQUENCY_POLICY_AMBIGUOUS: '采样间隔依据尚需明确。',
  SAMPLING_GAP_EXCEEDS_APPROVED_INTERVAL: '实际采样间隔超过当前获准范围。', SAMPLING_GAP_PRECISION_INSUFFICIENT: '时间精度不足以确认间隔符合要求。',
  SAMPLING_ORDER_UNCONFIRMED: '各次采样的准确顺序尚未明确。', INDEPENDENT_SAMPLING_REQUIRED: '两次检查尚未确认独立采样。',
  SAMPLING_RELATION_VERIFICATION_REQUIRED: '采样关系需要有效专业核对。', SAME_SAMPLING_DUPLICATE: '同次采样的其他来源分别保留。',
  FIELD_VERIFICATION_REQUIRED: '该项需要有效专业核对。', EXACT_NUMERIC_VALUES_REQUIRED: '比较需要完整的准确原值。',
  FIELD_RAW_UNIT_NOT_COMPARABLE: '两次原始单位尚不能比较。', FIELD_METHOD_NOT_COMPARABLE: '两次检验方法尚不能比较。',
  FIELD_CONCEPT_CODE_NOT_COMPARABLE: '两次项目含义尚不能比较。', FIELD_FIELD_CODE_NOT_COMPARABLE: '两次项目尚不能比较。',
  CURRENT_AFFIRMED_CONTEXT_REQUIRED: '实际发生与当前语境尚需明确。', MEDICAL_TIME_ORDER_NOT_CONFIRMED: '医学时间的前后顺序尚需明确。',
  SOURCE_RECORD_NOT_CURRENT: '来源记录已有更正或撤回。', NO_SAMPLING_DATA_IN_PERIOD: '该范围尚无能够采用的采样资料。',
  OBSERVATION_PERIOD_SUPERSEDED: '此观察范围已有新版本。', NO_NEW_SAMPLING_DATA: '没有新增采样资料，当前资料不能证明没有风险。',
  FACT_AVAILABILITY_UNKNOWN: '该项资料的可得时间尚未明确。', FACT_NOT_YET_AVAILABLE: '该项资料在当前时点尚不可采用。',
  OBSERVATION_PERIOD_EPISODE_UNAVAILABLE: '关联就诊资料的当前权限需要核验。',
  OBSERVATION_PERIOD_RETENTION_UNAVAILABLE: '观察范围的保存条件尚未准备好，当前资料可按权限读取。',
};
function reasonText(code: string) { return reasons[code] ?? `资料限制：${code}`; }
function dateInput(value: string, zone: string): MedicalTimeInput {
  return { precision: 'date', raw_text: value, local_date: value, local_datetime: null, timezone_name: zone,
    utc_offset_minutes: null, instant: null, resolution_status: 'local_only', local_year: null, local_month: null,
    end_year: null, end_month: null, relative_text: null };
}

export function UI2ObservationPeriodPage() {
  const access = useAccess();
  return <PeriodContent key={`${access.principalId}:${access.patientId}:${access.scopeVersion}`} />;
}

function PeriodContent() {
  const access = useAccess(), navigate = useNavigate(), [params, setParams] = useSearchParams();
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error
    && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [value, setValue] = useState<ObservationPeriodView | null>(null), [error, setError] = useState<Error | null>(null);
  const [request, setRequest] = useState<PeriodRequest | null>(null), [identity, setIdentity] = useState<PeriodIdentity | null>(null);
  const [intent, setIntent] = useState<IntentView | null>(null), [busy, setBusy] = useState(false), [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0), [start, setStart] = useState(''), [end, setEnd] = useState(''), [label, setLabel] = useState('');
  const [zone, setZone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone), [episode, setEpisode] = useState('');
  const [reason, setReason] = useState('本人设置观察范围');
  const pending = useRef<AbortController | null>(null), mounted = useRef(true), composing = useRef(false), settled = useRef('');
  const revalidating = useRef(false);
  const permissionError = error instanceof ApiError && [401, 403].includes(error.status) ? error : null;
  function current(signal?: AbortSignal) {
    return mounted.current && !signal?.aborted && document.visibilityState === 'visible'
      && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  }
  const requestId = params.get('request'), clientId = params.get('client_request'), versionId = params.get('period_version');
  const marker = requestId ?? clientId ?? versionId ?? 'current';

  function adopt(saved: ObservationPeriodView) {
    setValue(saved); setStart(saved.start?.local_date ?? ''); setEnd(saved.end?.local_date ?? '');
    setLabel(saved.label ?? ''); setEpisode(saved.episode_id ?? '');
    setZone(saved.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone); setReason('本人设置观察范围');
  }
  function clearRead() {
    setValue(null); setStart(''); setEnd(''); setLabel(''); setEpisode(''); setZone(''); setReason('');
  }
  function clearPrivateState() { clearRead(); setIdentity(null); setRequest(null); setIntent(null); }
  useEffect(() => {
    mounted.current = true;
    const change = () => { const shown = document.visibilityState === 'visible'; setVisible(shown);
      if (!shown) { pending.current?.abort(); clearPrivateState(); settled.current = ''; } };
    const freeze = () => { pending.current?.abort(); clearPrivateState(); setVisible(false); settled.current = ''; };
    const resume = () => { setVisible(document.visibilityState === 'visible'); settled.current = ''; setReload(current => current + 1); void access.refresh(); };
    document.addEventListener('visibilitychange', change);
    document.addEventListener('freeze', freeze); document.addEventListener('resume', resume);
    return () => { mounted.current = false; pending.current?.abort(); document.removeEventListener('visibilitychange', change);
      document.removeEventListener('freeze', freeze); document.removeEventListener('resume', resume); };
  }, []);
  useEffect(() => {
    if (!canRead || !visible) { pending.current?.abort(); clearPrivateState(); setLoading(false); settled.current = ''; return; }
    // A backend denial remains closed until the user explicitly revalidates.
    // Refreshing access here would toggle canRead and recursively repeat this read.
    if (permissionError || revalidating.current || pending.current || settled.current === `${marker}:${reload}`) return;
    const controller = new AbortController(); let active = true;
    queueMicrotask(() => {
      if (!active || !current(controller.signal) || !access.patientId) return;
      setLoading(true); setError(null); settled.current = `${marker}:${reload}`;
      void (async () => {
        if (requestId || clientId) {
          const original = await readPeriodRequest(requestId ?? clientId!, controller.signal, requestId ? undefined : {
            request_id: clientId!, target_key: `/patients/${access.patientId}/observation-period/versions`,
          });
          if (!current(controller.signal)) return;
          if (original.target_key !== `/patients/${access.patientId}/observation-period/versions`)
            throw new Error('观察时段原请求不属于当前患儿。');
          setRequest(original); setIdentity({ operation_id: original.operation_id, target_key: original.target_key,
            request_id: original.request_id, schema_version: '2.0.0', payload: original.period_input });
          setIntent({ intent_id: original.intent_id, request_id: original.request_id, generation: original.generation,
            expires_at: original.expires_at, payload_sha256: original.payload_sha256,
            status: original.state === 'committed' ? 'committed' : original.state === 'failed' ? 'failed' : 'prepared' });
          const saved = await readObservationPeriod(access.patientId!, controller.signal, original.receipt?.resource_id);
          if (original.receipt && saved.version !== original.receipt.version) throw new Error('观察时段版本与原请求回执不一致。');
          if (current(controller.signal)) {
            adopt(saved);
            if (!original.receipt) {
              setStart(original.period_input.start.local_date ?? ''); setEnd(original.period_input.end.local_date ?? '');
              setLabel(original.period_input.label ?? ''); setEpisode(original.period_input.episode_id ?? '');
              setZone(original.period_input.start.timezone_name ?? ''); setReason(original.period_input.reason);
            }
          }
        } else {
          const saved = await readObservationPeriod(access.patientId!, controller.signal, versionId ?? undefined);
          if (current(controller.signal)) adopt(saved);
        }
      })().then(undefined, failure => {
        if (!(failure instanceof Error)) throw failure;
        if (current(controller.signal)) { setError(failure); clearRead();
          if (failure instanceof ApiError && [401, 403].includes(failure.status)) clearPrivateState();
        }
      }).finally(() => { if (current(controller.signal)) setLoading(false); });
    });
    return () => { active = false; controller.abort(); };
  }, [canRead, visible, access.principalId, access.patientId, access.scopeVersion, marker, reload, permissionError]);

  function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (!canRead || !visible || permissionError || revalidating.current || pending.current || !current()) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setError(null);
    void Promise.resolve().then(() => current(controller.signal) ? operation(controller.signal) : undefined).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (current(controller.signal)) {
        setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) clearPrivateState();
      }
    }).finally(() => { if (pending.current === controller) { pending.current = null;
      if (mounted.current) { setBusy(false); if (controller.signal.aborted) { settled.current = ''; setReload(current => current + 1); } }
    } });
  }
  function submit() {
    if (!allowed) return;
    run(async signal => {
      if (!access.patientId || !value || !start || !end || !zone || !reason.trim()) throw new Error('观察范围、时区及说明需要完整填写。');
      const original: PeriodIdentity = identity ?? { operation_id: 'observation_period_change', request_id: crypto.randomUUID(),
        target_key: `/patients/${access.patientId}/observation-period/versions`, schema_version: '2.0.0',
        payload: { expected_version: value.version, start: dateInput(start, zone), end: dateInput(end, zone), label: label.trim() || null,
          episode_id: episode || null, reason } };
      setIdentity(original); settled.current = `${original.request_id}:${reload}`;
      setParams({ client_request: original.request_id }, { replace: true });
      const prepared = intent ?? await preparePeriod(original, signal);
      if (!current(signal)) return;
      setIntent(prepared); settled.current = `${prepared.intent_id}:${reload}`;
      setParams({ client_request: original.request_id, request: prepared.intent_id }, { replace: true });
      const receipt = await submitPeriod(original, prepared, signal);
      if (!current(signal)) return;
      const saved = await readObservationPeriod(access.patientId, signal, receipt.resource_id);
      if (saved.version !== receipt.version || saved.owner_id !== access.principalId || saved.label !== original.payload.label)
        throw new Error('观察时段回读与保存正文或版本不一致。');
      if (current(signal)) { adopt(saved); setRequest({ intent_id: prepared.intent_id, request_id: original.request_id,
        operation_id: original.operation_id, target_key: original.target_key, state: 'committed', may_retry: false,
        generation: prepared.generation, expires_at: prepared.expires_at, payload_sha256: prepared.payload_sha256,
        receipt, error_code: null, period_input: original.payload }); }
    });
  }
  function inspect() { if (!current() || permissionError || pending.current) return; settled.current = ''; setReload(current => current + 1); }
  async function revalidate() {
    if (revalidating.current || pending.current || !mounted.current) return;
    revalidating.current = true; clearPrivateState();
    try {
      await access.refresh();
      // A changed account, patient or grant mounts its own clean page.
      if (!current()) return;
      setError(null); settled.current = ''; setReload(value => value + 1);
    } finally { revalidating.current = false; }
  }
  function retry() { run(async signal => {
    if (!request) throw new Error('尚未取得原请求。');
    const prepared = await retryPeriod(request, signal);
    if (current(signal)) { setIntent(prepared); setRequest({ ...request, state: 'pending', may_retry: false,
      generation: prepared.generation, expires_at: prepared.expires_at }); }
  }); }
  function editCurrent() {
    if (busy || identity && request?.state !== 'committed' && request?.state !== 'failed') throw new Error('原请求状态尚需核验。');
    setIdentity(null); setIntent(null); setRequest(null); setError(null); settled.current = `${marker}:${reload + 1}`;
    setParams({}, { replace: true }); setReload(current => current + 1);
  }
  const data = canRead && visible && !permissionError ? value : null;
  const editable = !!data && data.is_current && !loading && !busy && !identity && data.allowed_actions.includes('observation_period_change');
  const inScope = data?.samplings.filter(item => ['inside', 'overlap', 'unknown', 'not_selected'].includes(item.range_state)) ?? [];
  const known = inScope.filter(item => item.fact.medical_time !== null && item.range_state !== 'unknown');
  const latest = known.at(-1)?.fact.medical_time;
  const lastInterval = data?.intervals.at(-1);
  const allowed = !!data && !permissionError && !revalidating.current
    && (editable || !!identity && request?.state === 'pending' && !request.may_retry && !busy && !loading);

  return <UI2FamilyFrame title="观察时段" screenId="UI2-31" note="观察范围和原始采样时间分别保存，时段标签属于本人自报。" backTo="/records">
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span>
      <span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    {permissionError ? <section className="status-panel permission rich-read-state" role="alert">
      <div className="status-top"><LockKeyhole size={22} /><span>页面状态</span></div>
      <h2>{permissionError.status === 401 ? '登录状态需要重新核验' : '当前权限不能读取此观察时段'}</h2>
      <p>本页资料和未保存输入已清除。核验成功后会重新读取资料或原请求结果。</p>
      <p>{permissionError.message}</p><details><summary>查看错误详情</summary><p>HTTP {permissionError.status} · {permissionError.code}</p></details>
      <button className="text-action" disabled={access.loading || busy} onClick={() => void revalidate()}>重新核验并读取<ChevronRight size={16} /></button>
      <button className="text-action" onClick={() => navigate(permissionError.status === 401 ? '/login' : '/associations')}>{permissionError.status === 401 ? '重新登录' : '查看患儿关联'}<ChevronRight size={16} /></button>
    </section> : <UI2ReadState loading={loading || busy || access.loading} error={error} onRetry={inspect} />}
    <form style={{ overflowWrap: 'anywhere' }} onSubmit={event => { event.preventDefault(); if (!composing.current) submit(); }}
      onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}>
      <section className="card form-card"><h2 className="section-title">选择观察范围</h2><div className="field-grid">
        <label className="field"><span>开始日期</span><input type="date" required disabled={!editable} value={start} onChange={event => setStart(event.target.value)} /></label>
        <label className="field"><span>结束日期</span><input type="date" required disabled={!editable} value={end} min={start || undefined} onChange={event => setEnd(event.target.value)} /></label></div>
        <label className="field"><span>本人时段标签</span><input placeholder="可填写本次关注的时段" autoComplete="off" disabled={!editable} value={label} onChange={event => setLabel(event.target.value)} />
          <small>标签仅代表本人记录，不判定病情变化。</small></label>
        <details><summary>时区、关联就诊与保存说明</summary>
          <label className="field"><span>观察范围时区</span><input required disabled={!editable} value={zone} onChange={event => setZone(event.target.value)} /></label>
          <label className="field"><span>关联就诊</span><select disabled={!editable} value={episode} onChange={event => setEpisode(event.target.value)}>
            <option value="">尚未关联就诊</option>{data?.episode_options.map(item => <option key={item.episode_id} value={item.episode_id}>{item.label} · {item.organization ?? '医院未知'}</option>)}</select></label>
          <label className="field"><span>保存说明</span><input required disabled={!editable} value={reason} onChange={event => setReason(event.target.value)} /></label>
        </details></section>
      <section className="card">{!inScope.length ? <div className="empty"><span className="empty-icon"><CalendarDays size={54} /></span>
        <h3>当前可访问范围暂无采样资料</h3><p>仅显示当前权限允许读取的资料。没有显示内容不能说明没有风险。</p></div>
        : <><h2 className="section-title">同日采样与资料间隔</h2>{inScope.map(item => <details key={item.fact.fact_version_id}>
          <summary>{item.fact.original_name ?? item.fact.field_code} · {documentTimeLabel(item.fact.medical_time)}</summary>
          <p>{comparisonSymbol(item.fact.qualifier)} {item.fact.raw_numeric ?? item.fact.raw_text ?? '原值未知'} {item.fact.raw_unit ?? '单位未知'}；{item.fact.method ?? '检验方法未知'}</p>
          <p>语境：{factContextLabel(item.fact.context_type)}；发生状态：{assertionStateLabel(item.fact.assertion_state)}；核对：{verificationStatusLabel(item.fact.verification_status)}</p>
          <p>资料可得时间：{item.available_at ? <time dateTime={item.available_at}>{new Date(item.available_at).toLocaleString('zh-CN', { timeZoneName: 'short' })}</time> : '未知'}。平台取得资料的时间不能代替检查时间。</p>
          {item.reason_codes.map(code => <p key={code}>{reasonText(code)}</p>)}
          <ContextLink className="text-action" to={`/records/versions/${item.fact.record_version_id}`}>查看来源版本<ChevronRight size={17} /></ContextLink>
          {item.document_version_id && item.document_id && <ContextLink className="text-action" to={`/archives/source?document_version=${item.document_version_id}&document=${item.document_id}`}>查看原文依据<ChevronRight size={17} /></ContextLink>}
        </details>)}</>}
        {data?.intervals.map(item => <div className="form-card" key={`${item.before_fact_version_id}:${item.after_fact_version_id}`}>
          <p>实际采样间隔：{item.actual_hours !== null ? `${item.actual_hours} 小时` : item.minimum_hours !== null && item.maximum_hours !== null ? `${item.minimum_hours} 至 ${item.maximum_hours} 小时，精度尚不能确定准确间隔` : '尚不能确定'}</p>
          <p>比较状态：{{ comparable: '符合当前比较条件', incomparable: '当前不可比较', unknown: '比较条件尚未明确' }[item.comparability]}</p>
          {item.reason_codes.map(code => <p key={code}>{reasonText(code)}</p>)}
        </div>)}</section>
      <section className="card form-card"><div className="pair"><span>最近医学时间</span><strong>{latest ? documentTimeLabel(latest) : '未知'}</strong></div>
        <div className="pair"><span>实际观察间隔</span><strong>{lastInterval?.actual_hours !== null && lastInterval?.actual_hours !== undefined ? `${lastInterval.actual_hours} 小时` : '待资料确认'}</strong></div>
        <div className="pair"><span>是否可以比较</span><strong>{data?.intervals.some(item => item.comparability === 'comparable') ? '符合当前比较条件' : '资料不足'}</strong></div>
        {data?.reason_codes.map(code => <p key={code}>{reasonText(code)}</p>)}
        {data?.version && <details><summary>已保存观察范围与依据</summary><p>版本 {data.version} · {data.is_current ? '当前版本' : '历史版本'} · 本人自报标签</p>
          <p>{documentTimeLabel(data.start)} 至 {documentTimeLabel(data.end)}</p><p>采样间隔依据：{data.interval_basis_ref ?? '尚未取得批准依据'}</p>
          <p>当前资料不能证明没有风险。</p></details>}
      </section>
      <button className="button primary" disabled={!allowed || !start || !end || !zone || !reason.trim()} type="submit">使用此观察范围</button>
    </form>
    {identity && <section className="card form-card"><h2>{request?.state === 'committed' ? '观察范围已保存' : '原请求保存结果需要核验'}</h2>
      <button className="button secondary" disabled={busy || loading} onClick={inspect}>查询原请求结果</button>
      {request?.may_retry && <button className="button secondary" disabled={busy || loading} onClick={retry}>重新授权原请求</button>}
      {(request?.state === 'committed' || request?.state === 'failed') && <button className="button secondary" disabled={busy || loading} onClick={editCurrent}>读取当前范围并继续设置</button>}
    </section>}
  </UI2FamilyFrame>;
}
