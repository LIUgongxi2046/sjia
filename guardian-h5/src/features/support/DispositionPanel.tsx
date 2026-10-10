import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '../../api/client';
import { preparePurposeRequest, readPurposeRequest, readPurposeTask, readSupportDisposition, submitPurposeRequest } from '../../api/purpose';
import type { PurposeRequestIdentity } from '../../api/purpose';
import { validateApiValue } from '../../api/schema';
import type { DispositionItemView, DispositionView } from '../../../../frontend-design/v2-types';
import { PurposeTaskProgress } from './PurposeTaskProgress';

export function DispositionPanel({ active, onPermission }: { active: boolean; onPermission: () => void }) {
  const [params, setParams] = useSearchParams(), id = params.get('disposition_id'), requestId = params.get('disposition_request');
  const [value, setValue] = useState<DispositionView | null>(null), [error, setError] = useState<Error | null>(null);
  const [proof, setProof] = useState(''), [retainProof, setRetainProof] = useState(''), [recheck, setRecheck] = useState('');
  const [reload, setReload] = useState(0), [busy, setBusy] = useState(false), [unconfirmed, setUnconfirmed] = useState(false);
  const [taskId, setTaskId] = useState<string | null>(params.get('disposition_task'));
  const pending = useRef<AbortController | null>(null), running = useRef(false);
  const reloadResult = useCallback((resultId: string) => {
    if (resultId !== id) throw new Error('处置结果与当前申请不一致。');
    setTaskId(null); setReload(current => current + 1);
  }, [id]);
  useEffect(() => {
    const controller = new AbortController(); setValue(null); setError(null);
    if (active && id) void readSupportDisposition(id, controller.signal).then(result => {
      if (!controller.signal.aborted) setValue(result);
    }, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setError(failure); if (failure instanceof ApiError && [401, 403].includes(failure.status)) onPermission(); }
    });
    return () => { controller.abort(); pending.current?.abort(); setProof(''); setRetainProof(''); setRecheck(''); };
  }, [active, id, reload, onPermission]);
  async function query() {
    if (!active || !id || !requestId || running.current) return;
    running.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); pending.current = controller;
    await readPurposeRequest(requestId, controller.signal).then(async result => {
      if (!result.target_key.startsWith(`/support/dispositions/${id}/`) && result.operation_id !== 'disposition_retry') throw new Error('请求范围与当前处置申请不一致。');
      if (result.state === 'committed') {
        if (!result.receipt) throw new Error('处置保存结果缺少准确回执。');
        if (result.receipt.task_id) { await readPurposeTask(result.receipt.task_id, controller.signal); setTaskId(result.receipt.task_id); }
        else if (result.receipt.resource_id !== id) throw new Error('保存结果与当前申请不一致。');
        setUnconfirmed(false); setReload(current => current + 1);
      } else { setUnconfirmed(result.state !== 'failed'); if (result.state === 'failed') throw new Error('处置提交未完成，请重新核查当前条件。'); }
    }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setError(failure); })
      .finally(() => { running.current = false; setBusy(false); });
  }
  useEffect(() => { if (active && requestId) { setUnconfirmed(true); void query(); } }, [active, requestId]);
  async function submit(identity: PurposeRequestIdentity, schema: string) {
    if (!active || running.current || unconfirmed) return;
    running.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); pending.current = controller;
    validateApiValue(`v2-protocol.schema.json#/$defs/${schema}`, identity.payload);
    await preparePurposeRequest(identity, controller.signal).then(async intent => {
      setParams(current => { const next = new URLSearchParams(current); next.set('disposition_request', intent.intent_id); return next; }, { replace: true });
      setUnconfirmed(true);
      const receipt = await submitPurposeRequest(identity, intent, controller.signal);
      if (controller.signal.aborted) return;
      if (receipt.task_id) { setTaskId(receipt.task_id); setParams(current => { const next = new URLSearchParams(current); next.set('disposition_task', receipt.task_id!); return next; }, { replace: true }); }
      else if (receipt.resource_id !== id) throw new Error('保存结果与当前处置申请不一致。');
      setUnconfirmed(false); setReload(current => current + 1);
    }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) { setError(failure); if (failure instanceof ApiError && [401, 403].includes(failure.status)) onPermission(); } })
      .finally(() => { running.current = false; setBusy(false); });
  }
  function action(item: DispositionItemView, retain: boolean) {
    if (!value) return;
    const payload = { policy_version: value.policy_version, expected_scope_sha256: value.scope_sha256, expected_target_version: item.target_version };
    void submit({ requestId: crypto.randomUUID(), operation: retain ? 'disposition_retain' : 'disposition_execute',
      target: `/support/dispositions/${value.disposition_id}/items/${item.item_id}/${retain ? 'retain' : 'execute'}`,
      payload: retain ? { ...payload, retention_evidence_ref: retainProof, recheck_at: new Date(recheck).toISOString() } : payload }, retain ? 'DispositionRetainInput' : 'DispositionExecuteInput');
  }
  async function retryTask() {
    if (!taskId || busy || unconfirmed) return;
    const task = await readPurposeTask(taskId);
    if (task.task_type !== 'disposition') throw new Error('当前任务类型与处置范围不一致。');
    if (!task.allowed_actions.includes('retry')) { setReload(current => current + 1); return; }
    await submit({ requestId: crypto.randomUUID(), operation: 'disposition_retry', target: `/support/disposition-tasks/${taskId}/retry`,
      payload: { expected_status_version: task.status_version } }, 'DispositionRetryInput');
  }
  if (!active || !id) return null;
  return <section className="card form-card" style={{ overflowWrap: 'anywhere' }}><h2>资料处置申请</h2>
    {error && <p role="alert">{error.message}</p>}
    {unconfirmed && <><p role="status">处置保存结果待确认。</p><button className="text-action" disabled={busy} onClick={() => void query()}>查询原处置请求</button></>}
    {!value && !error && <p role="status">正在读取当前申请</p>}
    {value && <><p>申请状态 {value.status}；保存策略版本 {value.policy_version}。</p>{value.targets.map(target => <p key={`${target.target_type}:${target.target_id}`}>{target.target_type} · {target.target_id} · {target.target_version}</p>)}
      {value.status === 'pending' && <><label className="field"><span>处置批准证明</span><textarea value={proof} onChange={event => setProof(event.target.value)} /></label>
        <button className="button" disabled={busy || unconfirmed || !proof.trim()} onClick={() => void submit({ requestId: crypto.randomUUID(), operation: 'disposition_approve',
          target: `/support/dispositions/${value.disposition_id}/approve`, payload: { policy_version: value.policy_version, expected_scope_sha256: value.scope_sha256, evidence_ref: proof } }, 'DispositionApprovalInput')}>批准准确处置范围</button></>}
      {value.status !== 'pending' && value.status !== 'completed' && <><label className="field"><span>继续保存证明</span><textarea value={retainProof} onChange={event => setRetainProof(event.target.value)} /></label>
        <label className="field"><span>重新核查时间</span><input type="datetime-local" value={recheck} onChange={event => setRecheck(event.target.value)} /></label></>}
      {value.items.map(item => <article key={item.item_id}><h3>{item.target_type} · {item.target_id}</h3><p>处置状态 {item.status}；版本 {item.target_version}。</p>
        {item.reason && <p role="status">{item.reason}</p>}{item.receipt_sha256 && <p>实际回执 SHA256 {item.receipt_sha256}</p>}{item.recheck_at && <p>重新核查 {item.recheck_at}</p>}
        {['pending', 'failed', 'blocked'].includes(item.status) && value.status !== 'completed' && <><button className="button" disabled={busy || unconfirmed || !!taskId} onClick={() => action(item, false)}>执行此项已批准处置</button>
          <button className="button secondary" disabled={busy || unconfirmed || !!taskId || !retainProof.trim() || !recheck} onClick={() => action(item, true)}>记录此项继续保存</button></>}</article>)}
    </>}
    {taskId && <PurposeTaskProgress taskId={taskId} taskType="disposition" active={active} onCompleted={reloadResult} onPermission={onPermission} />}
    {taskId && <button className="text-action" disabled={busy || unconfirmed} onClick={() => void retryTask().then(undefined, failure => { if (!(failure instanceof Error)) throw failure; setError(failure); })}>查询处置任务状态</button>}
    <button className="text-action" disabled={busy} onClick={() => setReload(current => current + 1)}>重新读取处置证明</button>
  </section>;
}
