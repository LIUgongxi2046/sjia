import { useEffect, useRef, useState } from 'react';
import { useBlocker, useNavigate, useSearchParams } from 'react-router';
import { ArrowRight, LockKeyhole } from 'lucide-react';
import type { IntentView } from '../../../../frontend-design/types';
import type { ClinicalDirectoryView, RequestView, SupportSessionView, VerificationCreationScopeView, VerificationInput, VerificationIntentInput,
  VerificationOptionsView, VerificationResourceView } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { readClinicalDirectory } from '../../api/assessments';
import { findVerificationRequest, prepareVerification, readVerificationCreationScope, readVerificationOptions, readVerificationRequest,
  readVerificationResource, retryVerificationRequest, submitVerification } from '../../api/verifications';
import { UI2Frame, UI2Sheet } from '../../components/UI2Frame';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';
import { VerificationOriginals } from './VerificationOriginals';
import { RelationVerificationTools } from './RelationVerificationTools';

const results = { consistent: '一致', needs_correction: '需补正', uncertain: '无法确定' };
const recordNames = { observation: '体温与症状', report: '检验报告', therapy: '治疗记录', professional: '专业记录' };
type Result = keyof typeof results;

export function VerificationPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate();
  const [parameters, setParameters] = useSearchParams();
  const requestId = parameters.get('verification_request_id'), proofId = parameters.get('verification_id');
  const available = accessState === 'default' && session !== null
    && session.capabilities.some(item => item.capability_id === 'verify_field' && item.state === 'available');
  const canRevoke = available && session!.capabilities.some(item => item.capability_id === 'revoke_verification' && item.state === 'available');
  const [resource, setResource] = useState<VerificationResourceView | null>(null);
  const [options, setOptions] = useState<VerificationOptionsView | null>(null);
  const [state, setState] = useState<SupportReadState>('default');
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [conclusion, setConclusion] = useState<Result | ''>('');
  const [evidence, setEvidence] = useState(''), [policy, setPolicy] = useState('');
  const [reason, setReason] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [sheet, setSheet] = useState<'policy' | 'revoke' | 'create' | 'originals' | null>(null);
  const [creation, setCreation] = useState<VerificationCreationScopeView | null>(null);
  const [directory, setDirectory] = useState<ClinicalDirectoryView | null>(null), [directoryLoading, setDirectoryLoading] = useState(false);
  const [creationPatient, setCreationPatient] = useState('');
  const [versionInput, setVersionInput] = useState(parameters.get('record_version_id') ?? '');
  const [purposeInput, setPurposeInput] = useState(parameters.get('purpose') ?? '');
  const [assigned, setAssigned] = useState(''), [creationFields, setCreationFields] = useState<string[]>([]);
  const [phase, setPhase] = useState<'ready' | 'pending' | 'unknown' | 'saved'>('ready');
  const [request, setRequest] = useState<RequestView | null>(null);
  const current = useRef<{ identity: VerificationIntentInput; intent: IntentView | null } | null>(null);
  const writer = useRef<AbortController>(), busy = useRef(false), composing = useRef(false);
  const needsConfirmation = available && (phase === 'pending' || phase === 'unknown' || !!conclusion || !!evidence || !!reason || creationFields.length > 0);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => needsConfirmation && currentLocation.pathname !== nextLocation.pathname);

  function failed(failure: unknown) {
    setResource(null); setOptions(null); setCreation(null); setCreationFields([]); setDirectory(null); setCreationPatient(''); setSheet(null);
    const denied = failure instanceof ApiError && [401, 403].includes(failure.status);
    setState(denied ? 'permission' : navigator.onLine ? 'error' : 'offline');
    setError(failure instanceof Error ? failure.message : '专业核对读取失败。');
    if (denied) { current.current = null; setEvidence(''); setPolicy(''); setSelected([]); setConclusion(''); onRefresh(); }
  }
  useEffect(() => {
    setResource(null); setOptions(null); setSelected([]); setSheet(null); setError(null);
    setCreation(null); setCreationFields([]); setAssigned(''); setDirectory(null); setCreationPatient('');
    setPolicy(''); setEvidence(''); setConclusion(''); setState('default');
    if (!available || (!requestId && !proofId) || document.visibilityState !== 'visible') return;
    const controller = new AbortController();
    async function query() {
      setState('loading');
      try {
        if (requestId && proofId) throw new Error('页面需要选择一个准确核对请求或证明。');
        const value = await readVerificationResource(proofId ? 'verification' : 'verification_request', proofId ?? requestId!, controller.signal);
        const choices = value.resource_type === 'verification_request' && value.assigned_to === session!.principal_id
          && ['pending', 'in_review'].includes(value.status!) ? await readVerificationOptions(value, controller.signal) : null;
        if (controller.signal.aborted || document.visibilityState !== 'visible') return;
        setResource(value); setOptions(choices); setState('default');
      } catch (failure) { if (!controller.signal.aborted) failed(failure); }
    }
    void query();
    return () => controller.abort();
  }, [available, requestId, proofId, session?.principal_id, refresh, onRefresh]);
  useEffect(() => {
    function suspend() {
      if (document.visibilityState === 'visible') { onRefresh(); setRefresh(value => value + 1); return; }
      writer.current?.abort(); setResource(null); setOptions(null); setCreation(null); setCreationFields([]); setDirectory(null); setCreationPatient(''); setSheet(null); setState('loading');
      setConclusion(''); setEvidence(''); setPolicy(''); setSelected([]); setReason('');
    }
    const restore = () => { onRefresh(); setRefresh(value => value + 1); };
    document.addEventListener('visibilitychange', suspend); window.addEventListener('pageshow', restore);
    return () => { writer.current?.abort(); document.removeEventListener('visibilitychange', suspend); window.removeEventListener('pageshow', restore); };
  }, [onRefresh]);
  useEffect(() => {
    function leaving(event: BeforeUnloadEvent) {
      if (phase !== 'pending' && phase !== 'unknown' && !conclusion && !evidence && !reason && creationFields.length === 0) return;
      event.preventDefault(); event.returnValue = '';
    }
    window.addEventListener('beforeunload', leaving);
    return () => window.removeEventListener('beforeunload', leaving);
  }, [phase, conclusion, evidence, reason, creationFields]);

  async function saved(receipt: NonNullable<RequestView['receipt']>, signal: AbortSignal) {
    const kind = receipt.resource_type === 'verification_request' ? 'verification_request' : 'verification';
    const value = await readVerificationResource(kind, receipt.resource_id, signal);
    if (signal.aborted || document.visibilityState !== 'visible') return;
    setResource(value); setOptions(null); setSheet(null); setState('default'); setPhase('saved');
    setConclusion(''); setEvidence(''); setPolicy(''); setReason(''); setSelected([]); setRequest(null);
    setCreation(null); setCreationFields([]); setAssigned('');
    const query = new URLSearchParams(parameters);
    query.delete('verification_request_id'); query.delete('verification_id');
    query.set(kind === 'verification_request' ? 'verification_request_id' : 'verification_id', receipt.resource_id);
    setParameters(query, { replace: true });
  }
  async function send(identity: VerificationIntentInput, intent: IntentView | null) {
    if (busy.current || !available) return;
    busy.current = true; writer.current?.abort(); writer.current = new AbortController();
    setPhase('pending'); setError(null);
    const signal = writer.current.signal;
    try {
      const prepared = intent ?? await prepareVerification(identity, signal);
      current.current = { identity, intent: prepared };
      const query = new URLSearchParams(parameters);
      query.set('intent_id', prepared.intent_id); setParameters(query, { replace: true });
      const receipt = await submitVerification(identity, prepared, signal);
      await saved(receipt, signal);
    } catch (failure) {
      setPhase('unknown');
      if (!signal.aborted) {
        setError(failure instanceof Error ? failure.message : '提交结果尚未确认，请查询原请求。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) failed(failure);
      }
    } finally { busy.current = false; }
  }
  function begin(identity: VerificationIntentInput) {
    current.current = { identity, intent: null };
    const query = new URLSearchParams(parameters);
    query.delete('intent_id'); query.set('request_id', identity.request_id); query.set('operation_id', identity.operation_id);
    query.set('target_key', identity.target_key); setParameters(query, { replace: true });
    void send(identity, null);
  }
  async function loadDirectory() {
    if (!available || busy.current) return;
    busy.current = true; writer.current?.abort(); writer.current = new AbortController();
    const signal = writer.current.signal;
    setSheet('create'); setDirectoryLoading(true); setDirectory(null); setCreation(null); setCreationPatient('');
    setCreationFields([]); setVersionInput(''); setPurposeInput(''); setAssigned(''); setError(null);
    try {
      const value = await readClinicalDirectory('verify_field', signal);
      if (!signal.aborted && document.visibilityState === 'visible') {
        setDirectory(value);
        const requested = parameters.get('record_version_id');
        const owner = value.patients.find(item => item.records.some(record => record.record_version_id === requested));
        const record = owner?.records.find(item => item.record_version_id === requested);
        if (owner && record) { setCreationPatient(owner.patient_id); setVersionInput(record.record_version_id); setPurposeInput(record.purpose); }
      }
    } catch (failure) { if (!signal.aborted) failed(failure); }
    finally { busy.current = false; setDirectoryLoading(false); }
  }
  async function loadCreationScope() {
    if (!available || busy.current || composing.current) return;
    busy.current = true; writer.current?.abort(); writer.current = new AbortController();
    const signal = writer.current.signal;
    setCreation(null); setCreationFields([]); setAssigned(''); setError(null);
    try {
      const value = await readVerificationCreationScope(versionInput.trim(), purposeInput.trim(), signal);
      if (!signal.aborted && document.visibilityState === 'visible') setCreation(value);
    } catch (failure) {
      if (!signal.aborted) {
        setError(failure instanceof Error ? failure.message : '核对创建范围读取失败。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) failed(failure);
      }
    } finally { busy.current = false; }
  }
  function createRequest() {
    if (!creation || !creationFields.length || busy.current || composing.current) return;
    const recipient = creation.assignees.find(item => item.principal_id === assigned);
    if (!recipient || creationFields.some(id => !recipient.field_ids.includes(id))) {
      setError('需要选择覆盖全部指定字段的获准核对人员。'); return;
    }
    begin({ operation_id: 'verification_v2_request_create', target_key: '/support/verification-requests',
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: {
        record_version_id: creation.record_version_id, purpose: creation.purpose,
        field_ids: creationFields, assigned_to: assigned } });
  }
  function submit(revoking = false) {
    if (busy.current || composing.current || !resource) return;
    let payload: VerificationIntentInput['payload'];
    if (revoking) {
      if (!canRevoke || !reason.trim() || resource.resource_type !== 'verification') return;
      payload = { expected_verification_sha256: resource.reference_sha256, reason };
    } else {
      if (!conclusion || selected.length === 0 || !options) return;
      const source = options.evidence.find(item => item.evidence_ref === evidence && item.allowed_policy_ids.includes(policy));
      if (!source || !options.policies.some(item => item.policy_id === policy)) { setError('需要选择当前获准政策与准确原始依据。'); return; }
      payload = { record_version_id: resource.record_version_id, purpose: resource.purpose, policy_id: policy,
        evidence_source_id: source.source_revision_id, evidence_ref: source.evidence_ref,
        fields: selected.map(id => {
          const field = resource.fields.find(item => item.field_id === id);
          if (!field || field.state_version === null || field.status !== 'pending') throw new Error('待核对字段状态已经变化。');
          return { field_id: id, expected_state_version: field.state_version, result: conclusion, reason: conclusion === 'consistent' ? null : reason };
        }) } satisfies VerificationInput;
      if (conclusion !== 'consistent' && !reason.trim()) { setError('需要填写补正或无法确定的原因。'); return; }
    }
    const identity: VerificationIntentInput = { operation_id: revoking ? 'verification_v2_revoke' : 'verification_v2_submit',
      target_key: revoking ? `/support/verifications/${resource.resource_id}/revoke` : '/support/verifications',
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload };
    begin(identity);
  }
  async function recover(retry = false) {
    if (busy.current || !available) return;
    busy.current = true; writer.current?.abort(); writer.current = new AbortController();
    setPhase('pending'); setError(null);
    const signal = writer.current.signal;
    try {
      const intentId = current.current?.intent?.intent_id ?? parameters.get('intent_id');
      const key = current.current?.identity ?? { request_id: parameters.get('request_id')!,
        operation_id: parameters.get('operation_id') as VerificationIntentInput['operation_id'], target_key: parameters.get('target_key')! };
      const original = intentId ? await readVerificationRequest(intentId, signal) : await findVerificationRequest(key, signal);
      if (signal.aborted) return;
      setRequest(original);
      if (original.receipt) { await saved(original.receipt, signal); return; }
      const identity: VerificationIntentInput = { operation_id: original.operation_id as VerificationIntentInput['operation_id'],
        target_key: original.target_key, request_id: original.request_id, schema_version: '2.0.0', payload: original.verification_input! };
      current.current = { identity, intent: null };
      if (original.state === 'pending' && !original.may_retry) current.current.intent = {
        intent_id: original.intent_id, request_id: original.request_id, generation: original.generation!, status: 'prepared',
        expires_at: original.expires_at!, payload_sha256: original.payload_sha256! };
      if (retry) {
        const intent = await retryVerificationRequest(original, signal);
        current.current.intent = intent;
        busy.current = false; await send(identity, intent); return;
      }
      setPhase('unknown');
    } catch (failure) {
      setPhase('unknown');
      if (!signal.aborted) failed(failure);
    } finally { busy.current = false; }
  }
  const visible = available && state === 'default';
  const displayed = visible ? resource : null;
  const canSubmit = displayed?.resource_type === 'verification_request' && displayed.assigned_to === session?.principal_id
    && options !== null && ['pending', 'in_review'].includes(displayed.status!) && phase !== 'pending' && phase !== 'unknown';
  const canCreate = visible && !displayed && phase !== 'pending' && phase !== 'unknown';
  const readState = accessState !== 'default' ? accessState : !available ? 'permission' : state;
  return <UI2Frame title="专业资料核对" screenId="UI2-34" note="只对获准资料的指定字段与版本执行。" onBack={() => navigate('/support/data-use')}
    overlay={blocker.state === 'blocked' ? <UI2Sheet title="离开当前核对" onClose={() => blocker.reset()}>
      <p>未提交内容仅保留在当前页面，已发出请求需要按原身份查询结果。</p>
      <button className="button secondary" onClick={() => blocker.reset()}>继续核对</button>
      <button className="button" onClick={() => { writer.current?.abort(); blocker.proceed(); }}>确认离开</button>
    </UI2Sheet> : visible && sheet && <UI2Sheet title={sheet === 'policy' ? '核对政策与来源' : sheet === 'create' ? '创建专业核对请求' : sheet === 'originals' ? '准确版本原始资料' : '撤销核对证明'} onClose={() => setSheet(null)}>
      {sheet === 'originals' && displayed ? <VerificationOriginals resource={displayed} onRefresh={onRefresh} /> : sheet === 'create' ? <div onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
        <label className="field"><span>当前获准病例</span><select value={creationPatient} disabled={directoryLoading || phase === 'pending' || phase === 'unknown'} onChange={event => {
          setCreationPatient(event.target.value); setVersionInput(''); setPurposeInput(''); setCreation(null); setCreationFields([]); setAssigned(''); }}>
          <option value="">请选择病例</option>{directory?.patients.map(item => <option key={item.patient_id} value={item.patient_id}>{item.display_id}</option>)}</select></label>
        <label className="field"><span>准确记录版本</span><select value={versionInput} disabled={directoryLoading || phase === 'pending' || phase === 'unknown'} onChange={event => {
          const record = directory?.patients.find(item => item.patient_id === creationPatient)?.records.find(item => item.record_version_id === event.target.value);
          if (!record && event.target.value) throw new Error('所选记录已不属于当前获准目录。');
          setVersionInput(record?.record_version_id ?? ''); setPurposeInput(record?.purpose ?? ''); setCreation(null); setCreationFields([]); setAssigned(''); }}>
          <option value="">请选择记录</option>{directory?.patients.find(item => item.patient_id === creationPatient)?.records.map(item =>
            <option key={item.record_version_id} value={item.record_version_id}>{recordNames[item.kind]} · {item.source_organization ?? '来源医院未知'} · 版本 {item.version} · {new Date(item.recorded_at).toLocaleString('zh-CN')}</option>)}</select></label>
        <div className="pair"><span>准确资料用途</span><strong>{purposeInput || '尚未选择'}</strong></div>
        {directoryLoading && <p role="status">正在读取当前获准目录。</p>}
        {directory && !directory.patients.length && <p role="status">当前没有获准核对的资料。</p>}
        <button className="button secondary" disabled={directoryLoading || phase === 'pending' || phase === 'unknown' || !versionInput.trim() || !purposeInput.trim()}
          onClick={() => void loadCreationScope()}>读取获准字段与人员</button>
        {creation?.fields.map(field => <label className="switch-row" key={field.field_id}>
          <span>{field.original_name ?? field.field_code}<small>{field.raw_text ?? field.raw_numeric ?? field.categorical_value ?? '未知'}</small></span>
          <input type="checkbox" disabled={phase === 'pending'} checked={creationFields.includes(field.field_id)} onChange={event => {
            setCreationFields(value => event.target.checked ? [...value, field.field_id] : value.filter(id => id !== field.field_id)); setAssigned(''); }} />
        </label>)}
        {creation && <label className="field"><span>获准核对人员</span><select value={assigned} disabled={phase === 'pending' || !creationFields.length}
          onChange={event => setAssigned(event.target.value)}><option value="">请选择准确人员</option>
          {creation.assignees.filter(item => creationFields.every(id => item.field_ids.includes(id))).map(item =>
            <option key={item.principal_id} value={item.principal_id}>{item.principal_id}</option>)}</select></label>}
        {error && <p role="alert">{error}</p>}
        <button className="button" disabled={!creation || !assigned || !creationFields.length || phase === 'pending' || phase === 'unknown'} onClick={createRequest}>创建核对请求</button>
      </div> : sheet === 'policy' && options ? <>
        <label className="field"><span>准确政策版本</span><select value={policy} onChange={event => { setPolicy(event.target.value); setEvidence(''); }}>
          <option value="">请选择获准政策</option>{options.policies.map(item => <option key={item.policy_id} value={item.policy_id}>{item.config_key} · 版本 {item.version}</option>)}</select></label>
        <label className="field"><span>准确原始依据</span><select value={evidence} onChange={event => setEvidence(event.target.value)}>
          <option value="">请选择获准依据</option>{options.evidence.filter(item => item.allowed_policy_ids.includes(policy))
            .map(item => <option key={item.source_revision_id} value={item.evidence_ref}>{item.evidence_ref}</option>)}</select></label>
        <button className="button secondary" onClick={() => setSheet(null)}>继续核对</button>
      </> : <><label className="field"><span>撤销原因</span><textarea required value={reason} onChange={event => setReason(event.target.value)} /></label>
        <button className="button" disabled={!reason.trim() || phase === 'pending'} onClick={() => submit(true)}>确认撤销</button></>}
    </UI2Sheet>}>
    <div className="support-banner"><LockKeyhole size={16} />受控支持 · 需独立岗位授权</div>
    <section className="card form-card"><div className="eyebrow">指定范围内操作</div><h2>专业核对</h2>
      <p className="muted">只对获准资料的指定字段与版本执行。</p><div className="pair"><span>当前资料范围</span><strong>{displayed?.patient_id ?? '尚未授权选择'}</strong></div></section>
    <section className="card form-card" onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
      <label className="field"><span>指定核对结论</span><select disabled={!canSubmit} value={conclusion} onChange={event => setConclusion(event.target.value as Result | '')}>
        <option value="">请选择核对结论</option>{Object.entries(results).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span>原始依据</span><textarea disabled={!canSubmit} placeholder="关联指定文书、页码与字段版本" value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
      <div className="pair"><span>记录版本</span><strong>{displayed?.record_version_id ?? '尚未选择'}</strong></div>
      <div className="pair"><span>核对资格</span><strong>{policy ? '已选择准确政策版本' : '待确认'}</strong></div>
      {canSubmit && <button className="text-action" onClick={() => setSheet('policy')}>选择获准政策与依据</button>}
      {displayed?.fields.map(field => <label className="switch-row" key={field.field_id}>
        <span>{field.original_name ?? field.field_code}<small>{field.qualifier && field.qualifier !== 'eq' ? `${field.qualifier} ` : ''}
          {field.raw_text ?? field.raw_numeric ?? field.categorical_value ?? '未知'}{field.raw_unit ? ` ${field.raw_unit}` : ''}</small>
          {field.method && <small>检测方法：{field.method}</small>}{field.reference_interval && <small>参考区间：{field.reference_interval}</small>}
          {field.report_flag && <small>报告标记：{field.report_flag}</small>}</span>
        <input type="checkbox" disabled={!canSubmit || field.status !== 'pending'} checked={selected.includes(field.field_id)}
          onChange={event => setSelected(value => event.target.checked ? [...value, field.field_id] : value.filter(id => id !== field.field_id))} />
      </label>)}
      {canSubmit && conclusion && conclusion !== 'consistent' && <label className="field"><span>核对原因</span><textarea required value={reason} onChange={event => setReason(event.target.value)} /></label>}
    </section>
    <div className="two-buttons"><button className="button secondary" disabled={!displayed}
      onClick={() => setSheet('originals')}>查看原件</button>
      <button className="button" disabled={!canCreate && (!canSubmit || !conclusion || !selected.length || !policy || !evidence)}
        onClick={() => { if (canCreate) void loadDirectory(); else submit(); }}>提交核对</button></div>
    <button className="text-action center" onClick={() => navigate('/support/assessment')}>查看评估依据<ArrowRight size={15} /></button>
    <SupportReadStatus state={readState} error={accessState !== 'default' ? accessError : error}
      onRefresh={accessState !== 'default' || !available || readState === 'permission' ? onRefresh : () => setRefresh(value => value + 1)} />
    {visible && displayed?.resource_type === 'verification' && <section className="card form-card">
      <h3>{displayed.revoked_at ? '核对证明已撤销' : '核对结果已保存'}</h3>
      {displayed.results!.map(item => <div className="pair" key={item.field_id}><span>{item.field_id}</span><strong>{results[item.result]}</strong></div>)}
      {canRevoke && displayed.revoked_at === null && <button className="text-action" onClick={() => setSheet('revoke')}>撤销当前核对证明</button>}
      {available && displayed.revoked_at === null && displayed.verifier_id === session?.principal_id
        && phase !== 'pending' && phase !== 'unknown' && <RelationVerificationTools key={displayed.resource_id} proof={displayed} onRefresh={onRefresh} />}
      {phase !== 'pending' && phase !== 'unknown' && <button className="text-action" onClick={() => void loadDirectory()}>指定字段与核对人员</button>}
    </section>}
    {available && (phase === 'unknown' || phase === 'pending' || parameters.get('intent_id') || parameters.get('request_id')) && <section className="status-panel" role="status">
      <h2>{phase === 'pending' ? '正在核验提交' : phase === 'saved' ? '保存已经确认' : '查询原提交结果'}</h2><p>{error}</p>
      <button className="text-action" disabled={phase === 'pending'} onClick={() => void recover()}>查询原请求</button>
      {request?.may_retry && <button className="text-action" disabled={phase === 'pending'} onClick={() => void recover(true)}>重试原请求</button>}
      {request?.state === 'pending' && !request.may_retry && current.current?.intent
        && <button className="text-action" disabled={phase === 'pending'} onClick={() => void send(current.current!.identity, current.current!.intent)}>继续原提交</button>}
    </section>}
    {visible && displayed?.status === 'superseded' && <section className="status-panel stale"><h2>核对请求依据已变化</h2><p>需要使用当前准确请求与字段版本。</p></section>}
    {visible && options && !options.policies.length && <section className="status-panel partial"><h2>核对政策需要确认</h2><p>当前没有可采用的准确批准政策。</p></section>}
  </UI2Frame>;
}
