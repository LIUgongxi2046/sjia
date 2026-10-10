import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { Clock3, LockKeyhole } from 'lucide-react';
import type { SupportSessionView } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { cancelNotificationRequest, findNotificationRequest, notificationIdentity, prepareNotification, readNotificationCases, readNotificationHistory, readNotificationRecord, retryNotification, submitNotification } from '../../api/notifications';
import type { NotificationCases, NotificationHistory, NotificationRecord, NotificationRequest, NotificationIdentity } from '../../api/notifications';
import { UI2Frame, UI2Sheet } from '../../components/UI2Frame';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';

const states = { not_sent: '尚未发送', pending: '待确认发送', accepted: '已受理', delivered: '已送达', failed: '发送失败', uncertain: '受理状态未知', cancelled: '已取消发送', unknown: '尚无准确发送证明' };

export function NotificationPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate(); const [parameters, setParameters] = useSearchParams();
  const [cases, setCases] = useState<NotificationCases | null>(null);
  const [patient, setPatient] = useState<{ patient_id: string; display_id: string } | null>(null);
  const [history, setHistory] = useState<NotificationHistory | null>(null);
  const [record, setRecord] = useState<NotificationRecord | null>(null);
  const [sheet, setSheet] = useState<'cases' | 'record' | null>(null);
  const [pending, setPending] = useState(false); const [error, setError] = useState<string | null>(null);
  const [savedRequest, setSavedRequest] = useState<NotificationRequest | null>(null);
  const [absentRequest, setAbsentRequest] = useState(false);
  const [resume, setResume] = useState(0);
  const [cancelReason, setCancelReason] = useState('');
  const controller = useRef<AbortController>(); const busy = useRef(false);
  const available = session !== null && accessState === 'default' && session.capabilities.some(item => item.capability_id === 'manage_notifications' && item.state === 'available');
  const clear = useCallback(() => { controller.current?.abort(); setCases(null); setPatient(null); setHistory(null); setRecord(null); setSavedRequest(null); setAbsentRequest(false); setCancelReason(''); setSheet(null); setError(null); }, []);
  useEffect(() => {
    if (!available) clear();
    const hidden = () => { if (document.visibilityState !== 'visible') clear(); else { onRefresh(); setResume(value => value + 1); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { controller.current?.abort(); document.removeEventListener('visibilitychange', hidden); };
  }, [available, session, clear]);
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (!available || busy.current) return;
    busy.current = true; controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal; setPending(true); setError(null);
    try { await action(signal); }
    catch (failure) {
      if (!signal.aborted) {
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) { clear(); onRefresh(); }
        setError(failure instanceof Error ? failure.message : '通知记录读取失败。');
      }
    } finally { busy.current = false; setPending(false); }
  }
  function loadCases(cursor?: string) {
    void run(async signal => {
      const value = await readNotificationCases(cursor, signal);
      if (cursor && value.snapshot_version !== cases?.snapshot_version) throw new Error('通知资料范围已经变化，请重新读取。');
      if (!signal.aborted) { setCases(cursor ? { ...value, items: [...cases!.items, ...value.items] } : value); setSheet('cases'); }
    });
  }
  function choose(value: { patient_id: string; display_id: string }) {
    void run(async signal => {
      const result = await readNotificationHistory(value.patient_id, true, undefined, signal);
      if (!signal.aborted) { setPatient(value); setHistory(result); setRecord(null); setSheet(null);
        setSavedRequest(null); setAbsentRequest(false); setCancelReason('');
        const query = new URLSearchParams(parameters); query.set('patient_id', value.patient_id); query.delete('recipient_id'); query.delete('request_id'); setParameters(query, { replace: true }); }
    });
  }
  function more() {
    if (!patient || !history?.next_cursor) return;
    void run(async signal => {
      const value = await readNotificationHistory(patient.patient_id, true, history.next_cursor!, signal);
      if (value.snapshot_version !== history.snapshot_version) throw new Error('通知历史已经变化，请重新读取。');
      if (!signal.aborted) setHistory({ ...value, items: [...history.items, ...value.items] });
    });
  }
  function open(item: NotificationRecord) {
    void run(async signal => {
      const value = await readNotificationRecord(item.recipient_id, item.patient_id, true, signal);
      if (!signal.aborted) { setRecord(value); setSheet('record');
        const query = new URLSearchParams(parameters); query.set('patient_id', item.patient_id); query.set('recipient_id', item.recipient_id);
        if (query.get('recipient_id') !== parameters.get('recipient_id')) { query.delete('request_id'); setSavedRequest(null); setAbsentRequest(false); }
        setParameters(query, { replace: true }); }
    });
  }
  async function original(requestId: string, item: NotificationRecord, signal: AbortSignal) {
    try {
      const value = await findNotificationRequest(requestId, item.recipient_id, signal);
      if (!signal.aborted) { setSavedRequest(value); setAbsentRequest(false); }
      return value;
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 404 && failure.code === 'NOTIFICATION_REQUEST_NOT_FOUND') {
        if (!signal.aborted) { setSavedRequest(null); setAbsentRequest(true); }
        return null;
      }
      throw failure;
    }
  }
  function send() {
    if (!record || parameters.get('request_id') && !absentRequest) return;
    const identity = notificationIdentity(record);
    const query = new URLSearchParams(parameters); query.set('request_id', identity.request_id); setParameters(query, { replace: true });
    setAbsentRequest(false);
    void run(async signal => {
      const intent = await prepareNotification(identity);
      await submitNotification(identity, intent);
      const value = await readNotificationRecord(record.recipient_id, record.patient_id, true, signal);
      await original(identity.request_id, value, signal);
      if (!signal.aborted) { setRecord(value); setResume(value => value + 1); }
    });
  }
  function continueOriginal() {
    if (!savedRequest || !record || savedRequest.state === 'committed' || savedRequest.state === 'unknown'
        || savedRequest.error_code === 'NOTIFICATION_INTENT_CANCELLED') return;
    const current = savedRequest;
    const identity: NotificationIdentity = { operation_id: current.operation_id, schema_version: '2.0.0',
      target_key: current.target_key, request_id: current.request_id, payload: current.notification_input };
    void run(async signal => {
      const intent = current.may_retry ? await retryNotification(current) : await prepareNotification(identity);
      await submitNotification(identity, intent);
      const value = await readNotificationRecord(record.recipient_id, record.patient_id, true, signal);
      await original(current.request_id, value, signal);
      if (!signal.aborted) { setRecord(value); setResume(value => value + 1); }
    });
  }
  function cancelOriginal() {
    if (!savedRequest || savedRequest.state !== 'pending') return;
    void run(async signal => {
      const value = await cancelNotificationRequest(savedRequest, cancelReason);
      if (!signal.aborted) { setSavedRequest(value); setCancelReason(''); }
    });
  }
  function correct() {
    if (savedRequest?.error_code !== 'NOTIFICATION_INTENT_CANCELLED' && !absentRequest) return;
    const query = new URLSearchParams(parameters); query.delete('request_id'); setParameters(query, { replace: true });
    setSavedRequest(null); setAbsentRequest(false);
    setResume(value => value + 1); if (record) setSheet('record');
  }
  useEffect(() => {
    const id = parameters.get('patient_id'), recipientId = parameters.get('recipient_id');
    if (!available || !id) return;
    const lifecycle = new AbortController();
    void Promise.resolve().then(() => {
      if (lifecycle.signal.aborted) return;
      return run(async signal => {
        const value = await readNotificationHistory(id, true, undefined, signal);
        const detail = recipientId ? await readNotificationRecord(recipientId, id, true, signal) : null;
        if (detail && parameters.get('request_id')) await original(parameters.get('request_id')!, detail, signal);
        if (!signal.aborted) { setPatient({ patient_id: id, display_id: id }); setHistory(value); setRecord(detail); if (detail) setSheet('record'); }
      });
    });
    return () => lifecycle.abort();
  }, [available, session?.principal_id, resume]);
  const close = useCallback(() => setSheet(null), []);
  const current = record ?? history?.items[0] ?? null;
  return <UI2Frame title="通知记录" screenId="UI2-39" note="受理、送达、阅读分别记录。" onBack={() => navigate('/alerts')}
    overlay={available && sheet && <UI2Sheet title={sheet === 'cases' ? '选择获准资料范围' : '准确通知记录'} onClose={close}>
      {error && <p role="alert">{error}</p>}
      {sheet === 'cases' && cases && <>{cases.capability_state === 'unavailable' && <p>{cases.capability_reason}</p>}
        {cases.items.map(item => <button className="row" key={item.patient_id} disabled={pending} onClick={() => choose(item)}><span><strong>{item.display_id}</strong><small>{item.patient_id}</small></span></button>)}
        {cases.capability_state === 'available' && !cases.items.length && <p>当前岗位尚无获准患儿范围。</p>}
        {cases.next_cursor && <button className="text-action" disabled={pending} onClick={() => loadCases(cases.next_cursor!)}>读取更多范围</button>}</>}
      {sheet === 'record' && record && <><div className="pair"><span>准确通知身份</span><strong>{record.recipient_id}</strong></div>
        <div className="pair"><span>当前内容资格</span><strong>{record.current_validity === 'valid' && record.content_state === 'available' ? '当前有效' : '当前不可采用'}</strong></div>
        <div className="pair"><span>本人阅读时间</span><strong>{record.read_at ?? '尚未阅读'}</strong></div>
        {!record.attempts.length && <p>尚无实际发送尝试及准确回执。</p>}
        {record.current_validity === 'valid' && record.content_state === 'available' && ['not_sent', 'failed', 'cancelled'].includes(record.delivery_status)
          && (!parameters.get('request_id') || absentRequest) && <button className="button primary" disabled={pending} onClick={send}>取得许可并发送当前通知</button>}
        {record.attempts.map(attempt => <section className="card form-card" key={attempt.attempt_id}><h3>发送尝试 {attempt.attempt_number}</h3>
          <div className="pair"><span>许可有效期</span><strong>{attempt.expires_at}</strong></div><div className="pair"><span>Provider 请求</span><strong>{attempt.provider_request_id ?? '尚未开始请求'}</strong></div>
          <div className="pair"><span>开始时间</span><strong>{attempt.started_at ?? '尚未开始'}</strong></div>
          {attempt.receipts.map(receipt => <div className="pair" key={receipt.receipt_id}><span>{receipt.status === 'accepted' ? '受理回执' : receipt.status === 'delivered' ? '送达回执' : '失败回执'}</span><strong>{receipt.occurred_at} · {receipt.provider_event_id}</strong></div>)}
        </section>)}</>}
    </UI2Sheet>}>
    <div className="support-banner"><LockKeyhole size={16} />受控支持 · 需独立岗位授权</div>
    <section className="card form-card"><div className="eyebrow">指定范围内操作</div><h2>通知记录</h2><p className="muted">受理、送达、阅读分别记录。</p>
      <div className="pair"><span>当前资料范围</span><strong>{available && patient ? patient.display_id : '尚未授权选择'}</strong></div>
      {available && <button className="text-action" disabled={pending} onClick={() => loadCases()}>选择获准资料范围</button>}
    </section><SupportReadStatus state={accessState} error={accessError} onRefresh={onRefresh} />
    <section className="card form-card"><ol className="process-list">{[['待发送', '检查当前接收关系与许可'], ['已受理', '需要 Provider 受理凭证'], ['已送达', '需要有效送达回执'], ['已查看', '仅记录本人实际阅读']].map(([label, description]) =>
      <li key={label}><Clock3 size={20} /><div><strong>{label}</strong><small>{description}</small></div></li>)}</ol></section>
    <section className="card form-card"><div className="pair"><span>当前实际发送</span><strong>{available && current ? states[current.delivery_status] : '尚无记录'}</strong></div>
      <div className="pair"><span>未知受理状态</span><strong>查询原请求后处理</strong></div><div className="pair"><span>撤权后行为</span><strong>停止新的发送</strong></div></section>
    {available && history && <section className="card form-card"><h3>实际发送与回执</h3>
      {history.capability_state === 'unavailable' && <p>{history.capability_reason}</p>}
      {history.items.map(item => <button className="row" key={item.recipient_id} disabled={pending} onClick={() => open(item)}><span><strong>{states[item.delivery_status]}</strong><small>{item.channel} · {item.created_at}</small></span></button>)}
      {history.capability_state === 'available' && !history.items.length && <p>当前获准范围尚无通知记录。</p>}
      {patient && <button className="text-action" disabled={pending} onClick={() => choose(patient)}>重新读取当前通知</button>}
      {history.next_cursor && <button className="text-action" disabled={pending} onClick={more}>读取更多通知</button>}
    </section>}{pending && <p role="status">正在读取当前通知资料。</p>}{error && !sheet && <p role="alert">{error}</p>}
    {available && parameters.get('request_id') && <section className="card form-card"><h3>原提交结果</h3>
      <div className="pair"><span>原请求身份</span><strong>{parameters.get('request_id')}</strong></div>
      <p>{savedRequest?.state === 'committed' ? '发送许可已保存，当前受理与送达状态见准确通知记录。'
        : savedRequest?.state === 'unknown' ? '当前提交结果未知，请查询同一请求。'
        : absentRequest ? '当前查询确认原意图尚未保存，可以更正当前操作。'
        : savedRequest?.error_code === 'NOTIFICATION_INTENT_CANCELLED' ? '原意图已经取消，可以更正当前操作。'
        : savedRequest?.state === 'failed' ? '原意图未提交，重试需要重新检查当前许可。'
        : savedRequest?.state === 'pending' ? '原意图尚未提交，可以继续同一请求。' : '需要查询原请求结果。'}</p>
      <button className="text-action" disabled={pending} onClick={() => setResume(value => value + 1)}>查询原请求与当前通知</button>
      {savedRequest && ['pending', 'failed'].includes(savedRequest.state) && savedRequest.error_code !== 'NOTIFICATION_INTENT_CANCELLED'
        && <button className="button primary" disabled={pending} onClick={continueOriginal}>继续原请求</button>}
      {savedRequest?.state === 'pending' && <><label className="field">取消未提交意图的理由<textarea value={cancelReason} disabled={pending} onChange={event => setCancelReason(event.target.value)} /></label>
        <button className="button secondary" disabled={pending || !cancelReason.trim()} onClick={cancelOriginal}>取消当前未提交意图</button></>}
      {(savedRequest?.error_code === 'NOTIFICATION_INTENT_CANCELLED' || absentRequest) && <button className="button secondary" disabled={pending} onClick={correct}>更正当前操作</button>}
      {savedRequest?.receipt && <p>保存时间：{savedRequest.receipt.saved_at} · 发送尝试：{savedRequest.receipt.resource_id}</p>}
    </section>}
  </UI2Frame>;
}
