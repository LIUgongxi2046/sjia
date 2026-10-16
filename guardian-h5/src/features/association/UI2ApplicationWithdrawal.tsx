import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useEffect, useRef, useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { useAccess } from '../../state/access';
import { useRelationResource } from '../../api/useRelationResource';
import { UI2ReadState } from '../../components/UI2ReadState';
import { applicationWithdrawalTarget, prepareApplicationWithdrawal, submitApplicationWithdrawal,
  readApplication, readApplicationRequest, retryApplication } from '../../api/application';
import type { ApplicationAction, ApplicationIntent, ApplicationRequest, ApplicationReceipt } from '../../api/application';
import type { ApplicationView } from '../../api/endpoints';

interface WithdrawalAttempt { requestId: string; payload: ApplicationAction; intent: ApplicationIntent | null }

export function UI2ApplicationWithdrawal({ application, onCurrent }: {
  application: ApplicationView; onCurrent: (value: ApplicationView) => void;
}) {
  const access = useAccess();
  const canWrite = !!access.principalId && !!access.csrfToken && !access.loading && !access.error;
  const target = applicationWithdrawalTarget(application.application_id);
  const recent = useRelationResource<{ requests: ApplicationRequest[] }>(canWrite ? '/api/requests' : null,
    access.principalId, 'ApplicationRequestList');
  const [opened, setOpened] = useState(false);
  const [reason, setReason] = useState('');
  const [attempt, setAttempt] = useState<WithdrawalAttempt | null>(null);
  const [request, setRequest] = useState<ApplicationRequest | null>(null);
  const [selectedIntentId, setSelectedIntentId] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<ApplicationReceipt | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const composing = useRef(false);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (!canWrite) { pending.current?.abort(); setReceipt(null); setRequest(null); }
  }, [canWrite]);
  useEffect(() => {
    function pause() {
      if (document.hidden && busyRef.current) {
        pending.current?.abort(); setError(new Error('页面已经暂停，请核验原撤回请求的保存结果。'));
      }
    }
    document.addEventListener('visibilitychange', pause);
    return () => document.removeEventListener('visibilitychange', pause);
  }, []);

  function run(operation: (controller: AbortController) => Promise<void>) {
    if (!canWrite || busyRef.current) return;
    const controller = new AbortController(); pending.current = controller;
    busyRef.current = true; setBusy(true); setError(null);
    void operation(controller).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setError(failure); recent.reload(); }
    }).finally(() => {
      if (pending.current === controller) { pending.current = null; busyRef.current = false; setBusy(false); }
    });
  }

  async function adopt(saved: ApplicationReceipt, payload: ApplicationAction | null, controller: AbortController) {
    if (saved.resource_id !== application.application_id) throw new Error('撤回回执对应其他申请。');
    const current = await readApplication(application.application_id, controller.signal);
    if (current.status !== 'withdrawn' || BigInt(current.version) < BigInt(saved.version)
      || (payload !== null && current.reason !== payload.reason)) throw new Error('重新读取的申请状态、版本或撤回说明与回执不一致。');
    if (!controller.signal.aborted) { setReceipt(saved); onCurrent(current); recent.reload(); }
  }

  function submit() {
    run(async controller => {
      if (!access.csrfToken || application.status !== 'pending') throw new Error('当前身份或申请状态不允许撤回。');
      if (attempt === null && (!canStart || selectedIntentId !== null)) throw new Error('本人原请求尚未核验，当前不能建立新的撤回请求。');
      const current = attempt ?? { requestId: crypto.randomUUID(), payload: {
        expected_state: 'pending' as const, expected_version: application.version, reason: reason.trim(),
      }, intent: null };
      const { validateRelationRead } = await import('../../api/relationReadSchema');
      validateRelationRead('ApplicationAction', current.payload);
      setAttempt(current);
      const prepared = current.intent ?? await prepareApplicationWithdrawal(current.requestId, application.application_id,
        current.payload, access.csrfToken, controller.signal);
      if (controller.signal.aborted) return;
      setAttempt({ ...current, intent: prepared });
      if (prepared.status === 'failed' || (prepared.status === 'prepared' && Date.parse(prepared.expires_at) <= Date.now()))
        throw new Error('原撤回意图需要重新核验，当前没有重新提交。');
      await adopt(await submitApplicationWithdrawal(prepared, application.application_id, current.payload,
        access.csrfToken, controller.signal), current.payload, controller);
    });
  }

  function inspect(intentId: string) {
    run(async controller => {
      setOpened(true); setSelectedIntentId(intentId); setAttempt(null); setRequest(null); setReceipt(null); setReason('');
      const current = await readApplicationRequest(intentId, controller.signal);
      if (current.operation_id !== 'application_withdraw' || current.target_key !== target)
        throw new Error('保存请求与当前申请撤回入口不一致。');
      if (controller.signal.aborted) return;
      setRequest(current);
      let payload: ApplicationAction | null = null;
      if (current.application_input !== null) {
        const { validateRelationRead } = await import('../../api/relationReadSchema');
        payload = validateRelationRead<ApplicationAction>('ApplicationAction', current.application_input);
        setReason(payload.reason);
        setAttempt({ requestId: current.request_id, payload, intent: {
          intent_id: current.intent_id, request_id: current.request_id, generation: current.generation,
          status: current.state === 'committed' ? 'committed' : current.state === 'failed' ? 'failed' : 'prepared',
          expires_at: current.expires_at, payload_sha256: current.payload_sha256,
        } });
      }
      if (current.receipt) await adopt(current.receipt, payload, controller);
      else {
        const value = await readApplication(application.application_id, controller.signal);
        if (!controller.signal.aborted) onCurrent(value);
      }
    });
  }

  function verify() {
    const intentId = attempt?.intent?.intent_id ?? selectedIntentId
      ?? recent.data?.requests.find(value => value.request_id === attempt?.requestId && value.target_key === target)?.intent_id;
    if (intentId) inspect(intentId);
    else { recent.reload(); setError(new Error('尚未取得原撤回请求标识，请重新读取本人保存请求。')); }
  }

  function retry() {
    run(async controller => {
      if (!access.csrfToken || application.status !== 'pending' || !request || !request.may_retry
        || request.operation_id !== 'application_withdraw' || request.target_key !== target || request.application_input === null)
        throw new Error('当前申请或保存请求不具备重新提交条件。');
      const { validateRelationRead } = await import('../../api/relationReadSchema');
      const payload = validateRelationRead<ApplicationAction>('ApplicationAction', request.application_input);
      if (payload.expected_version !== application.version) throw new Error('申请版本已经改变，请核对当前申请；原请求不能采用其他版本。');
      const prepared = await retryApplication(request, access.csrfToken, controller.signal);
      if (controller.signal.aborted) return;
      setAttempt({ requestId: request.request_id, payload, intent: prepared }); setRequest(null);
      await adopt(await submitApplicationWithdrawal(prepared, application.application_id, payload,
        access.csrfToken, controller.signal), payload, controller);
    });
  }

  const records = recent.data?.requests.filter(value => value.operation_id === 'application_withdraw' && value.target_key === target);
  const hasUnconfirmed = records?.some(value => value.state !== 'committed');
  const frozen = busy || attempt !== null || selectedIntentId !== null;
  const canStart = canWrite && application.status === 'pending' && records !== undefined && !recent.loading && !recent.error && !hasUnconfirmed;
  return <>
    {application.status === 'pending' && !opened && <button className="text-action" disabled={!canStart || busy} onClick={() => setOpened(true)}>撤回申请</button>}
    {application.status === 'pending' && <UI2ReadState requirePatient={false} loading={recent.loading} error={recent.error} onRetry={recent.reload} />}
    {opened && <form className="card form-card" onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}
      onSubmit={event => { event.preventDefault(); if (!composing.current) submit(); }}>
      <h3>撤回本人申请</h3><p>撤回后，这份申请不再进入关联核验。已经保存的申请依据和撤回结果继续保留。</p>
      <div className="pair"><span>本次申请版本</span><strong>{attempt?.payload.expected_version ?? application.version}</strong></div>
      <label className="field" htmlFor="application-withdraw-reason"><span>撤回说明</span><textarea id="application-withdraw-reason" required maxLength={10000}
        disabled={!canWrite || frozen || application.status !== 'pending'} value={reason} onChange={event => setReason(event.target.value)} /></label>
      <button className="button" type="submit" disabled={!canWrite || busy || application.status !== 'pending' || !reason.trim() || receipt !== null
        || (attempt === null && (!canStart || selectedIntentId !== null))
        || (request !== null && (request.state !== 'pending' || request.may_retry || request.application_input === null))}>
        {busy ? '正在核验撤回保存结果' : attempt ? '继续提交原撤回请求' : '确认撤回申请'}</button>
      {!frozen && <button className="button secondary" type="button" onClick={() => { setOpened(false); setReason(''); }}>保留申请</button>}
    </form>}
    {error && <section className="status-panel error" role="alert"><h3>撤回结果需要核验</h3><p>{error.message}</p><p>撤回状态尚未确认，请先查询处理结果。</p></section>}
    {canWrite && (attempt || selectedIntentId) && !receipt && <button className="text-action" disabled={busy} onClick={verify}>核验原撤回结果</button>}
    {request && !receipt && <section className="card form-card"><h3>本人撤回请求</h3>
      <div className="pair"><span>当前结果</span><strong>{({ pending: '保存待核验', committed: '已保存', failed: '未完成' } as const)[request.state]}</strong></div>
      <div className="pair"><span>请求版本</span><strong>{request.generation}</strong></div>
      {request.application_input === null && <p>该请求没有保留可恢复正文，不能据此重新提交。</p>}
      {request.may_retry && request.application_input !== null && <button className="button" disabled={busy || application.status !== 'pending'
        || attempt?.payload.expected_version !== application.version} onClick={retry}>重新提交原撤回请求</button>}
      {request.error_code && <p role="status">{request.error_code}</p>}
    </section>}
    {receipt && <section className="status-panel success" role="status"><h3>申请撤回已经保存</h3>
      <div className="pair"><span>本次保存版本</span><strong>{receipt.version}</strong></div>
      <div className="pair"><span>保存时间</span><strong>{eventTimeLabel(receipt.saved_at)}</strong></div>
      <p>已核对撤回状态、保存版本与撤回说明。</p>
    </section>}
    {canWrite && records && records.length > 0 && <details className="card form-card"><summary>本人撤回保存请求</summary>
      {records.map(value => <button className="list-row" disabled={busy} key={value.intent_id} onClick={() => inspect(value.intent_id)}>
        <span className="row-text"><strong>{({ pending: '保存待核验', committed: '已保存', failed: '未完成' } as const)[value.state]}</strong>
          <small>{eventTimeLabel(value.prepared_at)}</small></span><ChevronRight size={17} />
      </button>)}
    </details>}
    {hasUnconfirmed && !opened && <p role="status">已有尚未确认的撤回请求，请查询原请求后继续处理。</p>}
  </>;
}
