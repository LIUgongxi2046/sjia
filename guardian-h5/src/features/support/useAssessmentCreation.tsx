import { useEffect, useRef, useState } from 'react';
import { useBlocker, useSearchParams } from 'react-router';
import type { IntentView } from '../../../../frontend-design/types';
import type { AssessmentClinicalSelection, AssessmentCreationOptions, AssessmentIntentInput, ClinicalDirectoryView, RequestView, SupportSessionView,
  DynamicClinicalSelection, DynamicCreationOptions, DynamicCreationPlan, AssessmentCreationPlan, DynamicIntentInput } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { assessmentPlanKey, assessmentTarget, findAssessmentRequest, prepareAssessment, readAssessmentCreationOptions,
  readAssessmentRequest, readClinicalDirectory, retryAssessmentRequest, submitAssessment } from '../../api/assessments';
import { validateApiValue } from '../../api/schema';
import { timeLabel } from '../../api/records';
import { UI2Sheet } from '../../components/UI2Frame';
import { AssessmentSelectionFields } from './AssessmentSelectionFields';
import { DynamicSelectionFields } from './DynamicSelectionFields';
import { DynamicTriggerRecords } from './DynamicTriggerRecords';
import { dynamicPlanKey, dynamicTarget, findDynamicRequest, prepareDynamic, readDynamicOptions, readDynamicRequest,
  retryDynamicRequest, submitDynamic } from '../../api/dynamic';

type CreationIdentity = AssessmentIntentInput | DynamicIntentInput;
const targetPatient = (identity: CreationIdentity) => identity.operation_id === 'assessment_v2_create' ? assessmentTarget(identity) : dynamicTarget(identity);
const planKey = (plan: AssessmentCreationPlan | DynamicCreationPlan) => plan.kind === 'dynamic' ? dynamicPlanKey(plan) : assessmentPlanKey(plan);

export function useAssessmentCreation({ session, available, kind, onRefresh }: {
  session: SupportSessionView | null; available: boolean; kind: 'classification' | 'ms' | 'dynamic' | null; onRefresh: () => void;
}) {
  const [parameters, setParameters] = useSearchParams();
  const canCreate = available && session?.capabilities.some(item => item.capability_id === 'execute_assessment' && item.state === 'available');
  const [open, setOpen] = useState(false), [loading, setLoading] = useState(false);
  const [directory, setDirectory] = useState<ClinicalDirectoryView | null>(null);
  const [options, setOptions] = useState<AssessmentCreationOptions | DynamicCreationOptions | null>(null);
  const [authorization, setAuthorization] = useState('');
  const [patient, setPatient] = useState(''), [plan, setPlan] = useState(''), [period, setPeriod] = useState('');
  const [assessmentAt, setAssessmentAt] = useState(''), [knownAt, setKnownAt] = useState('');
  const [mode, setMode] = useState<'current' | 'replay'>('current');
  const [choices, setChoices] = useState<(AssessmentClinicalSelection | DynamicClinicalSelection)[]>([]);
  const selectedPlan = options?.plans.find(item => planKey(item) === plan && item.kind === kind);
  const [phase, setPhase] = useState<'ready' | 'pending' | 'unknown' | 'saved'>('ready');
  const [error, setError] = useState<string | null>(null), [request, setRequest] = useState<RequestView | null>(null);
  const current = useRef<{ identity: CreationIdentity; intent: IntentView | null } | null>(null);
  const controller = useRef<AbortController>(), busy = useRef(false), composing = useRef(false);
  const dirty = !!assessmentAt || !!knownAt || !!plan || phase === 'pending' || phase === 'unknown';
  const blocker = useBlocker(({ currentLocation, nextLocation }) => !!canCreate && dirty && currentLocation.pathname !== nextLocation.pathname);

  function failed(failure: unknown) {
    setError(failure instanceof Error ? failure.message : '评估创建操作失败。');
    if (failure instanceof ApiError && [401, 403].includes(failure.status)) {
      current.current = null; setRequest(null); setDirectory(null); setOptions(null); setChoices([]); setOpen(false);
      setPatient(''); setPlan(''); setPeriod(''); setAuthorization(''); setAssessmentAt(''); setKnownAt(''); onRefresh();
    }
  }
  useEffect(() => {
    controller.current?.abort(); setOptions(null); setChoices([]); setPlan(''); setPeriod(''); setAuthorization(''); setOpen(false);
    setPhase(value => value === 'pending' ? 'unknown' : value);
  }, [kind]);
  useEffect(() => {
    if (canCreate) return;
    controller.current?.abort(); current.current = null; setRequest(null); setDirectory(null); setOptions(null); setChoices([]); setOpen(false);
    setPatient(''); setPlan(''); setPeriod(''); setAuthorization(''); setAssessmentAt(''); setKnownAt('');
    setPhase(value => value === 'pending' ? 'unknown' : value);
  }, [canCreate, session?.principal_id]);
  useEffect(() => {
    const hidden = () => {
      if (document.visibilityState === 'visible') return;
      controller.current?.abort(); current.current = null; setRequest(null); setDirectory(null); setOptions(null); setChoices([]); setOpen(false);
      setPatient(''); setPlan(''); setPeriod(''); setAuthorization(''); setAssessmentAt(''); setKnownAt('');
      setPhase(value => value === 'pending' ? 'unknown' : value);
    };
    document.addEventListener('visibilitychange', hidden);
    return () => { controller.current?.abort(); document.removeEventListener('visibilitychange', hidden); };
  }, []);
  useEffect(() => {
    const leaving = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', leaving);
    return () => window.removeEventListener('beforeunload', leaving);
  }, [dirty]);
  async function directoryRead() {
    if (!canCreate || busy.current) return;
    busy.current = true; controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal;
    setOpen(true); setLoading(true); setDirectory(null); setOptions(null); setChoices([]); setPatient(''); setPlan(''); setPeriod(''); setError(null);
    try {
      const value = await readClinicalDirectory('execute_assessment', signal);
      if (!signal.aborted && document.visibilityState === 'visible') {
        setDirectory(value);
        const selected = parameters.get('patient_id');
        if (selected && value.patients.some(item => item.patient_id === selected)) setPatient(selected);
      }
    } catch (failure) { if (!signal.aborted) failed(failure); }
    finally { busy.current = false; setLoading(false); }
  }
  async function optionsRead() {
    if (!canCreate || busy.current || composing.current || !patient) return;
    busy.current = true; controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal;
    setLoading(true); setOptions(null); setChoices([]); setPlan(''); setPeriod(''); setError(null);
    try {
      const value = kind === 'dynamic' ? await readDynamicOptions(patient, knownAt, mode, signal)
        : await readAssessmentCreationOptions(patient, knownAt, mode, signal);
      if (!signal.aborted && document.visibilityState === 'visible') setOptions(value);
    } catch (failure) { if (!signal.aborted) failed(failure); }
    finally { busy.current = false; setLoading(false); }
  }
  function adopted(taskId: string, patientId: string, requestId: string, intentId: string) {
    if (document.visibilityState !== 'visible') return;
    const query = new URLSearchParams(parameters);
    query.delete('assessment_id'); query.delete('evaluation_id'); query.set('task_id', taskId); query.set('patient_id', patientId);
    if (!current.current) throw new Error('当前保存缺少原提交身份。');
    query.set('evaluation_kind', current.current.identity.operation_id === 'assessment_v2_create' ? current.current.identity.payload.kind : 'dynamic');
    query.set('request_id', requestId); query.set('intent_id', intentId);
    setParameters(query, { replace: true }); setPhase('saved'); setOpen(false); setRequest(null);
    setOptions(null); setDirectory(null); setChoices([]); setPatient(''); setPlan(''); setPeriod(''); setAssessmentAt(''); setKnownAt('');
  }
  async function send(identity: CreationIdentity, intent: IntentView | null) {
    if (!canCreate || busy.current) return;
    busy.current = true; controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal; setPhase('pending'); setError(null);
    try {
      const prepared = intent ?? (identity.operation_id === 'assessment_v2_create' ? await prepareAssessment(identity, signal) : await prepareDynamic(identity, signal));
      current.current = { identity, intent: prepared };
      const query = new URLSearchParams(parameters); query.set('intent_id', prepared.intent_id);
      query.set('request_id', identity.request_id); query.set('patient_id', targetPatient(identity));
      query.set('evaluation_kind', identity.operation_id === 'assessment_v2_create' ? identity.payload.kind : 'dynamic');
      setParameters(query, { replace: true });
      const task = identity.operation_id === 'assessment_v2_create' ? await submitAssessment(identity, prepared, signal) : await submitDynamic(identity, prepared, signal);
      if (!signal.aborted) adopted(task.task_id, targetPatient(identity), identity.request_id, prepared.intent_id);
    } catch (failure) {
      setPhase('unknown'); if (!signal.aborted) failed(failure);
    } finally { busy.current = false; }
  }
  function submit() {
    if (!canCreate || busy.current || composing.current || !kind || !options) return;
    const selected = options.plans.find(item => planKey(item) === plan && item.kind === kind);
    if (!selected || options.patient_id !== patient || options.mode !== mode || Date.parse(options.known_at) !== Date.parse(knownAt)
        || period && !options.observation_periods.some(item => item.observation_period_id === period)) {
      setError('需要重新读取当前病例、配置与观察区间。'); return;
    }
    if (selected.kind === 'dynamic' && !selected.use_authorization_ids.includes(authorization)) {
      setError('需要选择当前准确用途授权。'); return;
    }
    const dynamicChoices = choices.filter((item): item is DynamicClinicalSelection => 'input_role' in item);
    const identity: CreationIdentity = selected.kind === 'dynamic'
      ? { operation_id: 'alert_evaluation_v2_create', target_key: `/support/patients/${patient}/alert-evaluations`,
        request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { mode, assessment_at: assessmentAt, known_at: knownAt,
          policy_id: selected.rule.config_id, input_rule_id: selected.input_rule.config_id, window_policy_id: selected.window.config_id,
          verification_policy_id: selected.verification.config_id, use_authorization_id: authorization, observation_period_id: period || null,
          clinical_selections: dynamicChoices } }
      : { operation_id: 'assessment_v2_create', target_key: `/support/patients/${patient}/assessments`,
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { kind: selected.kind, assessment_at: assessmentAt, known_at: knownAt,
        mode, rule_id: selected.rule.config_id, window_policy_id: selected.window.config_id,
        verification_policy_id: selected.verification.config_id, observation_period_id: period || null,
        clinical_selections: choices.filter(item => !('input_role' in item)) } };
    try {
      validateApiValue('v2-protocol.schema.json#/$defs/Timestamp', assessmentAt); targetPatient(identity);
    } catch (failure) { failed(failure); return; }
    current.current = { identity, intent: null };
    const query = new URLSearchParams(parameters);
    query.delete('intent_id'); query.delete('assessment_id'); query.delete('evaluation_id'); query.delete('task_id'); query.set('evaluation_kind', selected.kind);
    query.set('patient_id', patient); query.set('request_id', identity.request_id); setParameters(query, { replace: true });
    void send(identity, null);
  }
  async function recover(retry = false) {
    if (!canCreate || busy.current) return;
    busy.current = true; controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal; setPhase('pending'); setError(null);
    try {
      const intentId = current.current?.intent?.intent_id ?? parameters.get('intent_id');
      const dynamic = current.current ? current.current.identity.operation_id === 'alert_evaluation_v2_create' : parameters.get('evaluation_kind') === 'dynamic';
      const readOriginal = dynamic ? readDynamicRequest : readAssessmentRequest;
      const findOriginal = dynamic ? findDynamicRequest : findAssessmentRequest;
      const original = intentId ? await readOriginal(intentId, signal) : await findOriginal(
        current.current?.identity.request_id ?? parameters.get('request_id')!,
        current.current ? targetPatient(current.current.identity) : parameters.get('patient_id')!, signal);
      if (signal.aborted) return;
      setRequest(original);
      const identity: CreationIdentity = dynamic
        ? { operation_id: 'alert_evaluation_v2_create', target_key: original.target_key, request_id: original.request_id, schema_version: '2.0.0', payload: original.dynamic_input! }
        : { operation_id: 'assessment_v2_create', target_key: original.target_key, request_id: original.request_id, schema_version: '2.0.0', payload: original.assessment_input! };
      current.current = { identity, intent: null };
      if (original.receipt) { adopted(original.receipt.task_id!, targetPatient(identity), original.request_id, original.intent_id); return; }
      if (original.state === 'pending' && !original.may_retry) current.current.intent = {
        intent_id: original.intent_id, request_id: original.request_id, generation: original.generation!, status: 'prepared',
        expires_at: original.expires_at!, payload_sha256: original.payload_sha256! };
      if (retry) {
        const intent = dynamic ? await retryDynamicRequest(original, signal) : await retryAssessmentRequest(original, signal); current.current.intent = intent;
        busy.current = false; await send(identity, intent); return;
      }
      setPhase('unknown');
    } catch (failure) { setPhase('unknown'); if (!signal.aborted) failed(failure); }
    finally { busy.current = false; }
  }
  const content = canCreate && <>
    {(phase === 'pending' || phase === 'unknown' || parameters.get('intent_id') || parameters.get('request_id')) && <section className="status-panel" role="status">
      <h2>{phase === 'pending' ? '正在确认原提交' : phase === 'saved' ? '评估任务受理已经保存' : '查询原评估提交'}</h2><p>{error}</p>
      <button className="text-action" disabled={phase === 'pending'} onClick={() => void recover()}>查询原请求</button>
      {request?.may_retry && <button className="text-action" disabled={phase === 'pending'} onClick={() => void recover(true)}>重试原请求</button>}
      {request?.state === 'pending' && !request.may_retry && current.current?.intent
        && <button className="text-action" disabled={phase === 'pending'} onClick={() => void send(current.current!.identity, current.current!.intent)}>继续原提交</button>}
    </section>}
  </>;
  const overlay = blocker.state === 'blocked' ? <UI2Sheet title="离开当前评估" onClose={() => blocker.reset()}>
    <p>未提交输入保留在当前页面，已发出请求需要按原身份查询结果。</p>
    <button className="button secondary" onClick={() => blocker.reset()}>继续评估</button>
    <button className="button" onClick={() => { controller.current?.abort(); blocker.proceed(); }}>确认离开</button>
  </UI2Sheet> : canCreate && open && <UI2Sheet title={kind === 'dynamic' ? '创建动态评估' : kind === 'ms' ? '创建 MS 评分' : '创建静态分类'} onClose={() => setOpen(false)}>
    <div onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
      <label className="field"><span>当前获准病例</span><select value={patient} disabled={loading || phase === 'pending' || phase === 'unknown'} onChange={event => {
        setPatient(event.target.value); setOptions(null); setChoices([]); setPlan(''); setPeriod(''); }}><option value="">请选择病例</option>
        {directory?.patients.map(item => <option key={item.patient_id} value={item.patient_id}>{item.display_id}</option>)}</select></label>
      <label className="field"><span>评估时点（含时区）</span><input value={assessmentAt} disabled={phase === 'pending' || phase === 'unknown'} placeholder="包含时区的日期时间" onChange={event => setAssessmentAt(event.target.value)} /></label>
      {kind === 'dynamic' && patient && session && phase !== 'pending' && phase !== 'unknown'
        && session.capabilities.some(item => item.capability_id === 'read_assessment' && ['available', 'read_only'].includes(item.state))
        && <DynamicTriggerRecords patientId={patient} principalId={session.principal_id} onPermissionLost={onRefresh} />}
      <label className="field"><span>资料截止时点（含时区）</span><input value={knownAt} disabled={phase === 'pending' || phase === 'unknown'} placeholder="包含时区的日期时间" onChange={event => {
        setKnownAt(event.target.value); setOptions(null); setChoices([]); setPlan(''); setPeriod(''); }} /></label>
      <label className="field"><span>计算用途</span><select value={mode} disabled={phase === 'pending' || phase === 'unknown'} onChange={event => {
        setMode(event.target.value as typeof mode); setOptions(null); setChoices([]); setPlan(''); setPeriod(''); }}>
        <option value="current">当前受控评估</option><option value="replay">历史固定版本回放</option></select></label>
      <button className="button secondary" disabled={!patient || !knownAt || loading || phase === 'pending' || phase === 'unknown'} onClick={() => void optionsRead()}>读取准确批准版本</button>
      {options && <>
        <label className="field"><span>获准规则与窗口</span><select value={plan} disabled={phase === 'pending' || phase === 'unknown'} onChange={event => { setPlan(event.target.value); setChoices([]); }}>
          <option value="">请选择准确批准组合</option>{options.plans.filter(item => item.kind === kind).map(item => <option key={planKey(item)} value={planKey(item)}>
            {item.rule.config_key} 版本 {item.rule.version}；{item.window.config_key} 版本 {item.window.version}；{item.verification.config_key} 版本 {item.verification.version}</option>)}</select></label>
        <label className="field"><span>保存的观察区间</span><select value={period} disabled={phase === 'pending' || phase === 'unknown'} onChange={event => setPeriod(event.target.value)}>
          <option value="">按批准窗口检查完整候选</option>{options.observation_periods.map(item => <option key={item.observation_period_id} value={item.observation_period_id}>
            {item.label ?? '观察区间'}：{timeLabel(item.start_time)} 至 {timeLabel(item.end_time)}（版本 {item.version}）</option>)}</select></label>
        {!options.plans.some(item => item.kind === kind) && <p role="status">当前没有符合资料截止时点的完整批准配置。</p>}
      </>}
      {selectedPlan?.kind === 'dynamic' && <label className="field"><span>准确资料用途授权</span><select value={authorization} disabled={loading || phase === 'pending' || phase === 'unknown'}
        onChange={event => setAuthorization(event.target.value)}><option value="">请选择当前获准授权</option>
        {selectedPlan.use_authorization_ids.map(id => <option key={id} value={id}>{id}</option>)}</select></label>}
      {selectedPlan?.kind === 'dynamic' && <DynamicSelectionFields plan={selectedPlan} mode={mode} disabled={loading || phase === 'pending' || phase === 'unknown'}
        canSelect={!!session?.capabilities.some(item => item.capability_id === 'verify_field' && item.state === 'available')}
        choices={choices} onChange={setChoices} />}
      {selectedPlan && selectedPlan.kind !== 'dynamic' && <AssessmentSelectionFields plan={selectedPlan} mode={mode} disabled={loading || phase === 'pending' || phase === 'unknown'}
        canSelect={!!session?.capabilities.some(item => item.capability_id === 'verify_field' && item.state === 'available')}
        choices={choices.filter(item => !('input_role' in item))} onChange={setChoices} />}
      {loading && <p role="status">正在读取当前获准资料。</p>}{error && <p role="alert">{error}</p>}
      <button className="button" disabled={!selectedPlan || !assessmentAt || loading || phase === 'pending' || phase === 'unknown'
        || choices.some(item => !item.reason.trim() || !item.evidence_ref)} onClick={submit}>创建评估任务</button>
    </div>
  </UI2Sheet>;
  return { content, overlay, canCreate: !!canCreate && kind !== null && phase !== 'pending' && phase !== 'unknown',
    openCreation: directoryRead, patientId: patient || null };
}
