import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import type { SupportSessionView } from '../../../../frontend-design/v2-types';
import { UI2Frame } from '../../components/UI2Frame';
import { CorrectionRecovery } from '../../components/CorrectionRecovery';
import { ApiError } from '../../api/client';
import { readCorrectionQueue, readCorrectionReview, type CorrectionDetail, type CorrectionReview } from '../../api/corrections';
import { useCorrectionSubmission } from '../../state/useCorrectionSubmission';
import { SupportReadStatus, type SupportReadState } from './SupportApp';

export function CorrectionReviewPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate(); const [params, setParams] = useSearchParams();
  const selected = params.get('correction_id');
  const available = accessState === 'default' && !!session?.capabilities.some(item => item.capability_id === 'verify_field' && item.state === 'available');
  const [items, setItems] = useState<CorrectionReview[]>([]); const [detail, setDetail] = useState<CorrectionDetail | null>(null);
  const [cursor, setCursor] = useState<string | null>(null); const [state, setState] = useState<SupportReadState>('loading');
  const [error, setError] = useState<string | null>(null); const [refresh, setRefresh] = useState(0);
  const [reason, setReason] = useState(''); const [decision, setDecision] = useState<'resolved' | 'rejected' | ''>(''); const [resolution, setResolution] = useState('');
  const reader = useRef<AbortController | null>(null); const composing = useRef(false);
  const submission = useCorrectionSubmission(available, `/api/support/correction-requests/${selected}/resolve`, async (receipt, signal) => {
    if (!selected || receipt.resource_id !== selected) throw new Error('处理回执与当前复核申请不一致。');
    const saved = await readCorrectionReview(selected, signal);
    if (saved.correction.version !== receipt.version || !['resolved', 'rejected'].includes(saved.correction.status)) throw new Error('保存后的复核状态与回执不一致。');
    setDetail(saved); setState('default');
  });
  useEffect(() => {
    reader.current?.abort(); setDetail(null); setItems([]); setCursor(null); setReason(''); setDecision(''); setResolution('');
    if (!available) return;
    const controller = new AbortController(); reader.current = controller; setState('loading'); setError(null);
    void (selected ? readCorrectionReview(selected, controller.signal).then(value => { if (!controller.signal.aborted) setDetail(value); })
      : readCorrectionQueue(null, controller.signal).then(value => { if (!controller.signal.aborted) { setItems(value.items); setCursor(value.next_cursor); } }))
      .then(() => { if (!controller.signal.aborted) setState('default'); })
      .catch(failure => { if (!controller.signal.aborted) { setError(failure instanceof Error ? failure.message : '复核资料读取失败。');
        setState(failure instanceof ApiError && [401, 403].includes(failure.status) ? 'permission' : navigator.onLine ? 'error' : 'offline'); } });
    return () => controller.abort();
  }, [available, selected, refresh]);
  async function more() {
    if (!available || !cursor || state !== 'default') return;
    const controller = new AbortController(); reader.current = controller; setState('loading');
    try { const value = await readCorrectionQueue(cursor, controller.signal); if (!controller.signal.aborted) { setItems(previous => [...previous, ...value.items.filter(row => !previous.some(old => old.correction.id === row.correction.id))]); setCursor(value.next_cursor); setState('default'); } }
    catch (failure) { if (!controller.signal.aborted) { setError(failure instanceof Error ? failure.message : '队列读取失败。'); setState('error'); } }
  }
  const shown = available && state === 'default' ? detail : null;
  const canResolve = !!shown && ['pending', 'in_review'].includes(shown.correction.status) && !submission.locked;
  return <UI2Frame title="更正申请复核" screenId="UI2-34-C" note="只处理获准病例、指定字段与准确版本。" onBack={() => navigate('/support/verification')}>
    <SupportReadStatus state={accessState !== 'default' ? accessState : !available ? 'permission' : state} error={accessError ?? error} onRefresh={!available ? onRefresh : () => setRefresh(value => value + 1)} />
    {available && !selected && <section className="card form-card"><h2>待处理申请</h2>{state === 'default' && !items.length && <p>本页没有当前获准处理的申请。</p>}
      {state === 'default' && items.map(row => <button className="list-row" key={row.correction.id} onClick={() => setParams({ correction_id: row.correction.id })}>
        <span>{row.display_id} · 记录版本 {row.record_version}<small>{row.correction.request.reason}</small></span></button>)}
      {cursor && <button className="text-action" disabled={state !== 'default'} onClick={() => void more()}>继续读取队列</button>}</section>}
    {shown && <section className="card form-card"><h2>{shown.display_id} · 更正申请</h2>
      <p>原记录版本 {shown.record_version}；当前记录版本 {shown.current_record_version}；申请版本 {shown.correction.version}</p>
      <p>申请原因：{shown.correction.request.reason}</p><p>申请人建议：{shown.correction.request.proposed_text ?? '未提供'}</p>
      {shown.fields.map(field => <div className="pair" key={field.field_id}><span>{field.field_code}</span><strong>{field.raw_text ?? field.raw_numeric ?? field.categorical_value ?? '未知'} {field.raw_unit}</strong></div>)}
      <p>处理状态：{shown.correction.status}</p>{shown.resolution_reason && <p>处理原因：{shown.resolution_reason}</p>}
      <fieldset disabled={!canResolve} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
        <label className="field"><span>处理结论</span><select value={decision} onChange={event => setDecision(event.target.value as typeof decision)}><option value="">请选择结论</option><option value="resolved">关联已保存更正</option><option value="rejected">不采纳申请</option></select></label>
        {decision === 'resolved' && <label className="field"><span>实际保存的直接替代版本</span><select value={resolution} onChange={event => setResolution(event.target.value)}><option value="">请选择准确版本</option>{shown.resolution_options.map(row => <option key={row.record_version_id} value={row.record_version_id}>版本 {row.version} · {row.reason}</option>)}</select></label>}
        {decision === 'resolved' && !shown.resolution_options.length && <p>尚无符合此申请的实际保存更正版本。请先由获准人员完成专业记录更正，再重新读取。</p>}
        <label className="field"><span>处理原因</span><textarea value={reason} onChange={event => setReason(event.target.value)} /></label>
        <button className="button" disabled={!canResolve || !decision || !reason.trim() || decision === 'resolved' && !resolution} onClick={() => {
          if (!canResolve || composing.current || !decision || !reason.trim()) return;
          void submission.start({ operation_id: 'correction_request_resolve', target_key: `/api/support/correction-requests/${shown.correction.id}/resolve`, request_id: crypto.randomUUID(), schema_version: '1.0.0',
            payload: { expected_version: shown.correction.version, decision, reason, resolution_record_version_id: decision === 'resolved' ? resolution : null } });
        }}>提交处理结论</button>
      </fieldset></section>}
    {available && <CorrectionRecovery submission={submission} />}
  </UI2Frame>;
}
