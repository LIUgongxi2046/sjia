import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { IntentView } from '../../../../frontend-design/types';
import type { RelationView, VerificationResourceView } from '../../../../frontend-design/v2-types';
import type { RelationDirectory, RelationIdentity, RelationRequestView } from '../../../../frontend-design/archive-relations-types';
import { ApiError } from '../../api/client';
import { prepareRelationRequest, readArchiveRelation, readRelationDirectory, readRelationRequest, relationPayloadMatches,
  retryRelationRequest, submitRelationRequest } from '../../api/archiveRelations';

export function RelationVerificationTools({ proof, onRefresh }: { proof: VerificationResourceView; onRefresh: () => void }) {
  const [params, setParams] = useSearchParams();
  const [directory, setDirectory] = useState<RelationDirectory | null>(null);
  const [relation, setRelation] = useState<RelationView | null>(null);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<RelationView | null>(null);
  const [original, setOriginal] = useState<RelationRequestView | null>(null);
  const current = useRef<{ identity: RelationIdentity; intent: IntentView | null } | null>(null);
  const controller = useRef<AbortController | null>(null);
  const active = useRef(false);
  const mounted = useRef(true);
  const recovered = useRef<string | null>(null);
  const pending = params.get('relation_intent') ?? params.get('relation_request');
  function run(operation: (signal: AbortSignal) => Promise<void>) {
    if (active.current || document.visibilityState !== 'visible') return;
    const operationController = new AbortController(); controller.current = operationController; active.current = true; setBusy(true); setError(null);
    void operation(operationController.signal).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!operationController.signal.aborted && mounted.current) { setError(failure); if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setDirectory(null); setSaved(null); onRefresh(); } }
    }).finally(() => { if (controller.current === operationController) { active.current = false; if (mounted.current) setBusy(false); } });
  }
  useEffect(() => {
    mounted.current = true;
    const change = () => { if (document.visibilityState !== 'visible') { controller.current?.abort(); setDirectory(null); setRelation(null); setSaved(null); setOriginal(null); current.current = null; setReason(''); recovered.current = null; } };
    document.addEventListener('visibilitychange', change);
    return () => { mounted.current = false; controller.current?.abort(); controller.current = null; active.current = false;
      recovered.current = null; current.current = null; document.removeEventListener('visibilitychange', change); };
  }, []);
  async function adopt(value: NonNullable<RelationRequestView['receipt']>, identity: RelationIdentity, signal: AbortSignal) {
    const relation = await readArchiveRelation(value.resource_id, proof.patient_id, signal, value.version);
    if (!relationPayloadMatches(relation, identity.payload) || relation.verification_id !== proof.resource_id) throw new Error('关系版本与指定核对证明不一致。');
    if (!signal.aborted && mounted.current) setSaved(relation);
  }
  async function recover(signal: AbortSignal, retry = false) {
    const id = params.get('relation_intent');
    const relationId = params.get('relation');
    const requestId = params.get('relation_request');
    if (!id && (!relationId || !requestId)) throw new Error('原关系核对请求缺少准确身份。');
    const value = await readRelationRequest(id ?? requestId!, signal, id ? undefined : { operation_id: 'archive_relation_verify', target_key: `/support/archive-relations/${relationId}/verification` });
    if (value.operation_id !== 'archive_relation_verify' || !('verification_id' in value.relation_input) || value.relation_input.verification_id !== proof.resource_id) throw new Error('原关系请求不属于当前核对证明。');
    const identity: RelationIdentity = { operation_id: value.operation_id, target_key: value.target_key, request_id: value.request_id, schema_version: '2.0.0', payload: value.relation_input };
    const intent: IntentView = { intent_id: value.intent_id, request_id: value.request_id, generation: value.generation,
      expires_at: value.expires_at, payload_sha256: value.payload_sha256, status: value.state === 'committed' ? 'committed' : value.state === 'failed' ? 'failed' : 'prepared' };
    if (signal.aborted) return;
    current.current = { identity, intent }; setOriginal(value); setReason(value.relation_input.reason);
    if (value.receipt) await adopt(value.receipt, identity, signal);
    else if (retry) { const renewed = await retryRelationRequest(value, signal); current.current.intent = renewed;
      await adopt(await submitRelationRequest(identity, renewed, signal), identity, signal); }
  }
  useEffect(() => { if (pending && recovered.current !== pending && !active.current) { recovered.current = pending; run(signal => recover(signal)); } }, [pending, busy]);
  function load() {
    run(async signal => {
      const value = await readRelationDirectory(proof.patient_id, directory?.next_cursor ?? null, signal);
      if (directory?.next_cursor && value.snapshot_version !== directory.snapshot_version) throw new Error('采样关系目录已有更新，请重新读取。');
      if (!signal.aborted) setDirectory(directory?.next_cursor ? { ...value,
        sources: [...new Map([...directory.sources, ...value.sources].map(item => [item.id, item])).values()],
        candidates: [...directory.candidates, ...value.candidates],
        relations: [...new Map([...directory.relations, ...value.relations].map(item => [item.relation_id, item])).values()] } : value);
    });
  }
  function submit() {
    run(async signal => {
      if (!proof.evidence_ref || proof.revoked_at || !relation || !reason.trim()) throw new Error('指定核对证明或关系原因不具备提交条件。');
      const identity: RelationIdentity = current.current?.identity ?? { operation_id: 'archive_relation_verify',
        target_key: `/support/archive-relations/${relation.relation_id}/verification`, request_id: crypto.randomUUID(), schema_version: '2.0.0',
        payload: { expected_version: relation.version, verification_id: proof.resource_id, evidence_ref: proof.evidence_ref, reason } };
      current.current = { identity, intent: current.current?.intent ?? null };
      setParams(previous => { const copy = new URLSearchParams(previous); copy.set('relation', relation.relation_id); copy.set('relation_request', identity.request_id); return copy; }, { replace: true });
      const intent = current.current.intent ?? await prepareRelationRequest(identity, signal);
      current.current.intent = intent; recovered.current = intent.intent_id;
      setParams(previous => { const copy = new URLSearchParams(previous); copy.set('relation_intent', intent.intent_id); return copy; }, { replace: true });
      await adopt(await submitRelationRequest(identity, intent, signal), identity, signal);
    });
  }
  const candidates = directory?.relations.filter(value => value.status === 'confirmed' && value.allowed_actions.includes('archive_relation_verify')
    && [value.left_fact_version_id, value.right_fact_version_id].some(id => directory.sources.some(source => source.id === id && source.record_version_id === proof.record_version_id))) ?? [];
  return <details><summary>同次采样、独立检查与更正的专业核对</summary>
    <p>指定资料关系、准确字段版本及当前核对依据共同用于采样资格判断。</p>
    <button type="button" className="text-action" disabled={busy || !!current.current} onClick={load}>{directory ? directory.next_cursor ? '继续读取采样关系' : '重新读取采样关系' : '读取当前获准采样关系'}</button>
    <label className="field"><span>指定采样关系</span><select aria-label="指定采样关系" value={relation?.relation_id ?? ''} disabled={busy || !!current.current} onChange={event => setRelation(candidates.find(value => value.relation_id === event.target.value) ?? null)}>
      <option value="">请选择准确资料关系</option>{candidates.map(value => <option key={value.relation_id} value={value.relation_id}>{value.relation_type} · 版本 {value.version} · {value.relation_id}</option>)}</select></label>
    <label className="field"><span>采样关系核对原因</span><textarea aria-label="采样关系核对原因" disabled={busy || !!current.current} value={reason} onChange={event => setReason(event.target.value)} /></label>
    <button type="button" className="button secondary" disabled={busy || !!saved || !!pending || !relation || !reason.trim()} onClick={submit}>保存指定采样关系核对</button>
    {pending && !saved && <button type="button" className="text-action" disabled={busy} onClick={() => run(signal => recover(signal))}>查询原采样核对请求</button>}
    {original?.may_retry && !saved && <button type="button" className="text-action" disabled={busy} onClick={() => run(signal => recover(signal, true))}>重新提交原采样核对请求</button>}
    {original?.state === 'pending' && !original.may_retry && !saved && <button type="button" className="text-action" disabled={busy} onClick={() => run(async signal => {
      if (!current.current?.intent) throw new Error('原采样请求缺少准确意图。');
      await adopt(await submitRelationRequest(current.current.identity, current.current.intent, signal), current.current.identity, signal);
    })}>继续原采样核对请求</button>}
    {error && <p role="alert">{error.message}</p>}{saved && <p role="status">专业采样关系已经保存，版本 {saved.version}，证明 {saved.verification_id}。</p>}
  </details>;
}
