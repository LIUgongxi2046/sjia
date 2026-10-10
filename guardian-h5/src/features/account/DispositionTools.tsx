import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '../../api/client';
import { prepareDisposition, readDisposition, readDispositionOptions, readDispositionRequest, submitDisposition } from '../../api/dispositions';
import { useAccess } from '../../state/access';
import type { DispositionOptions, DispositionView } from '../../../../frontend-design/v2-types';

export function DispositionTools() {
  const access = useAccess(), [params, setParams] = useSearchParams();
  const id = params.get('disposition_id'), intentId = params.get('disposition_request');
  const [options, setOptions] = useState<DispositionOptions | null>(null), [value, setValue] = useState<DispositionView | null>(null);
  const [selected, setSelected] = useState<string[]>([]), [proof, setProof] = useState(''), [policyId, setPolicyId] = useState('');
  const [opened, setOpened] = useState(!!id || !!intentId), [busy, setBusy] = useState(false), [unknown, setUnknown] = useState(false);
  const [error, setError] = useState<Error | null>(null), [reload, setReload] = useState(0), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const pending = useRef<AbortController | null>(null), running = useRef(false);
  const ready = visible && !access.loading && !access.error && !!access.patientId && !!access.principalId;
  useEffect(() => {
    const change = () => { setVisible(document.visibilityState === 'visible'); if (document.visibilityState !== 'visible') {
      pending.current?.abort(); setValue(null); setOptions(null); setProof(''); setSelected([]); setPolicyId('');
    } else { void access.refresh(); setReload(current => current + 1); } };
    document.addEventListener('visibilitychange', change);
    return () => { document.removeEventListener('visibilitychange', change); pending.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setOptions(null); setValue(null);
    if (ready && opened && !running.current) void (async () => {
      const choices = await readDispositionOptions(access.patientId!, controller.signal);
      if (choices.patient_id !== access.patientId) throw new Error('资料处置范围与当前患儿不一致。');
      let selectedId = id;
      if (intentId) {
        const original = await readDispositionRequest(intentId, controller.signal);
        if (original.target_key !== `/patients/${access.patientId}/dispositions`) throw new Error('原申请与当前患儿不一致。');
        setUnknown(original.state !== 'committed' && original.state !== 'failed');
        if (original.receipt) selectedId = original.receipt.resource_id;
      }
      const current = selectedId ? await readDisposition(selectedId, controller.signal) : null;
      if (controller.signal.aborted) return;
      setOptions(choices); setValue(current);
      if (current && current.patient_id !== access.patientId) throw new Error('保存结果与当前患儿不一致。');
      if (current && current.disposition_id !== id) setParams(before => { const next = new URLSearchParams(before); next.set('disposition_id', current.disposition_id); return next; }, { replace: true });
    })().then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setError(failure); if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setOptions(null); setValue(null); void access.refresh(); } }
    });
    return () => controller.abort();
  }, [ready, access.patientId, opened, id, intentId, reload, busy]);
  async function submit() {
    if (!ready || !options || !policyId || !selected.length || running.current || unknown || value) return;
    running.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); pending.current = controller;
    const targets = options.targets.filter(target => selected.includes(target.target_id));
    if (targets.some(target => target.policy_id !== policyId) || targets.length !== selected.length) throw new Error('所选资料与当前批准策略不一致。');
    const identity = { requestId: crypto.randomUUID(), target: `/patients/${access.patientId}/dispositions`,
      payload: { policy_id: policyId, evidence_ref: proof, targets: targets.map(({ target_type, target_id, target_version }) => ({ target_type, target_id, target_version })) } };
    await prepareDisposition(identity, controller.signal).then(async intent => {
      setUnknown(true); setParams(before => { const next = new URLSearchParams(before); next.set('disposition_request', intent.intent_id); return next; }, { replace: true });
      const receipt = await submitDisposition(identity, intent, controller.signal);
      const current = await readDisposition(receipt.resource_id, controller.signal);
      if (current.policy_id !== policyId || current.scope_sha256.length !== 64) throw new Error('处置保存结果与准确范围不一致。');
      if (!controller.signal.aborted) { setValue(current); setProof(''); setSelected([]); setUnknown(false);
        setParams(before => { const next = new URLSearchParams(before); next.set('disposition_id', current.disposition_id); return next; }, { replace: true }); }
    }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setError(failure); })
      .finally(() => { running.current = false; setBusy(false); });
  }
  return <><button className="button secondary" disabled={!ready || busy} onClick={() => setOpened(true)}>申请处理我的资料</button>
    {opened && <section className="card form-card" style={{ overflowWrap: 'anywhere' }}><h2>申请处理我的资料</h2>
      {error && <p role="alert">{error.message}</p>}{unknown && <p role="status">申请保存结果待确认，请查询原请求。</p>}
      {!options && !error && <p role="status">正在读取准确资料与批准策略</p>}
      {options && !value && !unknown && <><label className="field"><span>资料保存策略</span><select value={policyId} disabled={busy} onChange={event => { setPolicyId(event.target.value); setSelected([]); }}>
        <option value="">请选择已批准策略</option>{options.policies.map(policy => <option key={policy.policy_id} value={policy.policy_id}>{policy.policy_key} · {policy.version}</option>)}</select></label>
        {options.targets.filter(target => target.policy_id === policyId).map(target => <label className="check-line" key={target.target_id}><input type="checkbox" disabled={busy} checked={selected.includes(target.target_id)}
          onChange={event => setSelected(before => event.target.checked ? [...before, target.target_id] : before.filter(key => key !== target.target_id))} />{target.label} · 版本 {target.target_version}</label>)}
        {options.targets.length === 0 && <p>当前没有可申请处置的获准资料。</p>}
        <label className="field"><span>处置申请说明与依据</span><textarea value={proof} onChange={event => setProof(event.target.value)} disabled={busy} /></label>
        <button className="button" disabled={busy || !policyId || !selected.length || !proof.trim()} onClick={() => void submit()}>提交准确资料范围</button></>}
      {value && <><p role="status">申请已经保存，当前状态 {value.status}。</p><p>申请 {value.disposition_id}；批准策略版本 {value.policy_version}。</p>
        {value.targets.map(target => <p key={target.target_id}>{target.target_type} · {target.target_id} · {target.target_version}</p>)}
        {value.items.map(item => <article key={item.item_id}><p>资料 {item.target_id}；处置状态 {item.status}。</p>{item.receipt_sha256 && <p>实际处置回执 SHA256 {item.receipt_sha256}</p>}{item.recheck_at && <p>继续保存至重新核查 {item.recheck_at}</p>}</article>)}
        <p>只有具备实际处置回执的项目才登记删除完成；继续保存的项目保留证明和重新核查时间。</p></>}
      <button className="text-action" disabled={busy || !ready} onClick={() => { setError(null); setReload(current => current + 1); }}>{unknown ? '查询原申请请求' : '重新读取申请结果'}</button>
    </section>}</>;
}
