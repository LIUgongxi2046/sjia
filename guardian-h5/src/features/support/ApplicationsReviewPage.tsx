import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import type { SupportSessionView } from '../../../../frontend-design/v2-types';
import { UI2Frame } from '../../components/UI2Frame';
import { ApiError } from '../../api/client';
import { applicationDecisionMatches, checkApplicationDecision, checkApplicationId, decideApplicationReview, readApplicationReview,
  type SupportApplicationDecisionInput, type SupportApplicationReview } from '../../api/applicationReview';
import { SupportReadStatus, type SupportReadState } from './SupportApp';
import { ApplicationDeclarationDetails } from '../association/ApplicationDeclarationDetails';

const states = { pending: '待核验', approved: '已批准关联', rejected: '未批准', withdrawn: '申请人已撤回' };
export function ApplicationsReviewPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate(); const [params, setParams] = useSearchParams(); const id = params.get('application_id');
  const available = accessState === 'default' && !!session?.capabilities.some(row => row.capability_id === 'decide_relation' && row.state === 'available');
  const [lookup, setLookup] = useState(id ?? ''); const [application, setApplication] = useState<SupportApplicationReview | null>(null);
  const [readState, setReadState] = useState<SupportReadState>('default'); const [error, setError] = useState<string | null>(null);
  const [decision, setDecision] = useState<'approved' | 'rejected' | ''>(''); const [patient, setPatient] = useState('');
  const [reason, setReason] = useState(''); const [confirmed, setConfirmed] = useState(false); const [refresh, setRefresh] = useState(0);
  const [phase, setPhase] = useState<'editing' | 'pending' | 'unknown' | 'saved'>('editing'); const [mayRetry, setMayRetry] = useState(false);
  const controller = useRef<AbortController | null>(null); const busy = useRef(false); const composing = useRef(false);
  const original = useRef<{ application: SupportApplicationReview; payload: SupportApplicationDecisionInput } | null>(null);
  useEffect(() => {
    controller.current?.abort(); setApplication(null); setMayRetry(false);
    if (!available || !id) { setReadState('default'); return; }
    const reader = new AbortController(); controller.current = reader; setReadState('loading'); setError(null);
    void readApplicationReview(id, reader.signal).then(value => { if (!reader.signal.aborted) { setApplication(value); setPatient(value.patient_id ?? ''); setReadState('default'); } })
      .catch(failure => { if (!reader.signal.aborted) { setError(failure instanceof Error ? failure.message : '申请读取失败。');
        setReadState(failure instanceof ApiError && [401, 403].includes(failure.status) ? 'permission' : navigator.onLine ? 'error' : 'offline'); } });
    return () => reader.abort();
  }, [available, id, refresh]);
  useEffect(() => () => controller.current?.abort(), []);
  async function send(retry = false) {
    if (!available || busy.current || composing.current || !application || application.status !== 'pending') return;
    if (!retry && (phase !== 'editing' || !decision || !reason.trim() || decision === 'approved' && (!patient || !confirmed))) return;
    if (retry && (!mayRetry || !original.current)) return;
    const selected = retry ? original.current! : { application, payload: { expected_version: application.version,
      decision: decision as 'approved' | 'rejected', reason, ...(decision === 'approved' ? { patient_id: patient } : {}) } };
    try { checkApplicationDecision(selected.payload); } catch (failure) { setError(failure instanceof Error ? failure.message : '请核对准确目标和结论。'); return; }
    original.current = selected; busy.current = true; setPhase('pending'); setError(null); setMayRetry(false);
    const writer = new AbortController(); controller.current = writer;
    try {
      await decideApplicationReview(selected.application.application_id, selected.payload, writer.signal);
      const saved = await readApplicationReview(selected.application.application_id, writer.signal);
      if (!applicationDecisionMatches(selected.application, selected.payload, saved)) throw new Error('重新读取的申请与提交结论不一致，需要核验原申请。');
      if (!writer.signal.aborted) { setApplication(saved); setPhase('saved'); }
    } catch (failure) { setPhase('unknown'); if (!writer.signal.aborted) setError(failure instanceof Error ? failure.message : '提交结果需要查询。'); }
    finally { busy.current = false; }
  }
  async function recover() {
    if (!available || busy.current || !id) return;
    busy.current = true; const reader = new AbortController(); controller.current = reader; setError(null); setMayRetry(false);
    try {
      const current = await readApplicationReview(id, reader.signal);
      if (reader.signal.aborted) return;
      setApplication(current); setReadState('default');
      if (original.current && applicationDecisionMatches(original.current.application, original.current.payload, current)) setPhase('saved');
      else if (original.current && current.status === 'pending' && current.version === original.current.payload.expected_version
        && current.patient_id === original.current.application.patient_id) { setPhase('unknown'); setMayRetry(true); }
      else { setPhase('unknown'); setError('申请已变化，请依据当前处理状态核验；原结论不能重复提交。'); }
    } catch (failure) { if (!reader.signal.aborted) { setError(failure instanceof Error ? failure.message : '原申请暂不可读。');
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setApplication(null); setReadState('permission'); } } }
    finally { busy.current = false; }
  }
  const shown = available && readState === 'default' ? application : null;
  const editable = !!shown && shown.status === 'pending' && phase === 'editing';
  return <UI2Frame title="患儿关联申请核验" screenId="UI2-04-S" note="只按准确申请标识读取和处理，不提供患儿目录。" onBack={() => navigate('/support/data-use')}>
    <SupportReadStatus state={accessState !== 'default' ? accessState : !available ? 'permission' : readState}
      error={accessError ?? error} onRefresh={!available || readState === 'permission' ? onRefresh : () => setRefresh(value => value + 1)} />
    {available && !id && <form className="card form-card" onSubmit={event => { event.preventDefault(); try { checkApplicationId(lookup); setParams({ application_id: lookup }); } catch (failure) { setError(failure instanceof Error ? failure.message : '申请标识无效。'); } }}>
      <label className="field"><span>准确申请标识</span><input value={lookup} onChange={event => setLookup(event.target.value)} required /></label><button className="button" type="submit">读取指定申请</button></form>}
    {shown && <section className="card form-card"><h2>申请状态：{states[shown.status]}</h2><p>申请 {shown.application_id} · 版本 {shown.version}</p>
      <p>申请人标识：{shown.applicant_id}</p><p>申请说明与证据：{shown.evidence_ref}</p><p>补充说明：{shown.note ?? '未提供'}</p>
      <ApplicationDeclarationDetails declaration={shown.applicant_declaration} />
      <p>申请的访问范围：{shown.requested_access ?? '未填写'}</p><p>当前绑定患儿：{shown.patient_id ?? '尚未绑定'}</p>
      {shown.checked_at && <p>核验时间：{eventTimeLabel(shown.checked_at)}</p>}{shown.reason && <p>处理原因：{shown.reason}</p>}
      <fieldset disabled={!editable} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
        <label className="field"><span>核验结论</span><select value={decision} onChange={event => { setDecision(event.target.value as typeof decision); setConfirmed(false); }}>
          <option value="">请选择明确结论</option><option value="approved">批准患儿关联</option><option value="rejected">不批准申请</option></select></label>
        {decision === 'approved' && <><label className="field"><span>核验后的准确患儿标识</span><input value={patient} readOnly={shown.patient_id !== null} onChange={event => { setPatient(event.target.value); setConfirmed(false); }} /></label>
          <label className="switch-row"><span>已核对该申请与以上患儿标识；已有绑定不能更换</span><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} /></label>
          <p>此操作仅建立关联，不自动授予字段读取、登记或通知权限。</p></>}
        <label className="field"><span>核验处理原因</span><textarea required value={reason} onChange={event => setReason(event.target.value)} /></label>
        <button className="button" disabled={!editable || !decision || !reason.trim() || decision === 'approved' && (!patient || !confirmed)} onClick={() => void send()}>提交核验结论</button>
      </fieldset></section>}
    {available && error && readState === 'default' && <p role="alert">{error}</p>}
    {available && phase !== 'editing' && <section className="status-panel" role="status"><h3>{phase === 'saved' ? '处理结果已核验' : phase === 'pending' ? '正在提交原申请结论' : '原申请结果需要核验'}</h3>
      <button className="text-action" disabled={phase === 'pending'} onClick={() => void recover()}>查询同一申请</button>
      {mayRetry && <button className="text-action" onClick={() => void send(true)}>按相同版本和原结论重试</button>}</section>}
  </UI2Frame>;
}
