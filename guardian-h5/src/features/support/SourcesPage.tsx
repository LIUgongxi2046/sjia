import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import type { IntentView, RequestView, WriteReceipt } from '../../../../frontend-design/types';
import type { SupportSessionView } from '../../../../frontend-design/v2-types';
import { ApiError, fetchJson } from '../../api/client';
import { preparePublication, publicationOperations, publicationRequestIdentity, publicationTarget,
  readPublicationRequest, retryPublication, submitPublication } from '../../api/publication';
import type { PublicationIdentity, PublicationOperation } from '../../api/publication';
import { UI2Frame } from '../../components/UI2Frame';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';

const operations = ['source_register', 'source_revision_register', 'source_revision_approve', 'source_revision_revoke',
  'retention_policy_create', 'retention_policy_activate'] as const;
interface Revision { id: string; version: string; status: string; checksum: string; rights_evidence_ref: string | null; allowed_use: string[] }
interface Source { id: string; source_key: string; status: string; revisions: Revision[] }
interface Retention { id: string; policy_key: string; version: string; status: string }

export function SourcesPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate(); const [parameters, setParameters] = useSearchParams();
  const [operation, setOperation] = useState<PublicationOperation>('source_register');
  const [targetId, setTargetId] = useState(''); const [payload, setPayload] = useState('');
  const [sources, setSources] = useState<Source[]>([]); const [retentions, setRetentions] = useState<Retention[]>([]);
  const [request, setRequest] = useState<RequestView | null>(null); const [receipt, setReceipt] = useState<WriteReceipt | null>(null);
  const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const locked = useRef(false); const controller = useRef<AbortController>();
  const available = accessState === 'default' && session?.capabilities.some(item => item.capability_id === 'manage_config' && item.state === 'available') === true;
  useEffect(() => {
    const hidden = () => { if (document.visibilityState !== 'visible') {
      controller.current?.abort(); setSources([]); setRetentions([]); setRequest(null); setReceipt(null); setPayload('');
    } else onRefresh(); };
    document.addEventListener('visibilitychange', hidden);
    return () => document.removeEventListener('visibilitychange', hidden);
  }, [onRefresh]);
  const blocked = busy || parameters.has('intent_id') && (!request || request.state === 'pending' || request.state === 'unknown');
  async function directories(signal: AbortSignal) {
    const source = await fetchJson<{ items: Source[] }>('/api/support/sources', { signal });
    const policies = await fetchJson<{ items: Retention[] }>('/api/support/retention-policies', { signal });
    if (!signal.aborted) { setSources(source.items); setRetentions(policies.items); }
  }
  async function run(action: (signal: AbortSignal) => Promise<void>) {
    if (!available || locked.current) return;
    locked.current = true; setBusy(true); setError(null); controller.current?.abort(); controller.current = new AbortController();
    const current = controller.current; const signal = current.signal;
    await action(signal).then(() => { if (!signal.aborted) setBusy(false); }, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!signal.aborted) {
        setError(failure.message); setBusy(false);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setSources([]); setRetentions([]); setRequest(null); setReceipt(null); onRefresh(); }
      }
    });
    if (controller.current === current) locked.current = false;
  }
  async function restore(id: string, signal: AbortSignal) {
    const value = await readPublicationRequest(id, undefined, signal);
    if (!operations.includes(value.operation_id as typeof operations[number])) throw new Error('原请求不属于来源维护');
    if (!signal.aborted) { setRequest(value); setReceipt(value.receipt); setOperation(value.operation_id as PublicationOperation);
      setPayload(JSON.stringify(value.controlled_input, null, 2)); setTargetId(value.target_key.split('/')[4] ?? ''); }
    await directories(signal);
  }
  useEffect(() => {
    if (!available) { controller.current?.abort(); setSources([]); setRetentions([]); setRequest(null); setReceipt(null); setPayload(''); return; }
    if (locked.current) return;
    void run(signal => parameters.has('intent_id') ? restore(parameters.get('intent_id')!, signal) : directories(signal));
    return () => { controller.current?.abort(); locked.current = false; setBusy(false); };
  }, [available, session?.principal_id]);
  async function send(identity: PublicationIdentity, intent: IntentView | null, signal: AbortSignal) {
    const prepared = intent ?? await preparePublication(identity, signal);
    if (signal.aborted) return;
    setParameters({ intent_id: prepared.intent_id }, { replace: true });
    const pending = await readPublicationRequest(prepared.intent_id, undefined, signal);
    if (signal.aborted) return; setRequest(pending);
    const saved = await submitPublication(identity, prepared, signal);
    if (signal.aborted) return; setReceipt(saved);
    await restore(prepared.intent_id, signal);
  }
  function submit() {
    if (blocked) return;
    void run(async signal => {
      const body = JSON.parse(payload) as Record<string, unknown>;
      const identity: PublicationIdentity = { operation_id: operation, target_key: publicationTarget(operation, targetId),
        schema_version: '1.0.0', request_id: crypto.randomUUID(), payload: body };
      await send(identity, null, signal);
    });
  }
  function continueRequest() {
    if (!request || request.receipt) return;
    void run(async signal => {
      const identity = publicationRequestIdentity(request);
      const intent = request.may_retry ? await retryPublication(request, signal) : {
        intent_id: request.intent_id, request_id: request.request_id, generation: request.generation!, status: 'prepared',
        expires_at: request.expires_at!, payload_sha256: request.payload_sha256! } as IntentView;
      await send(identity, intent, signal);
    });
  }
  return <UI2Frame title="来源与保留政策" screenId="SOURCE-MAINTENANCE" note="来源、许可、用途和准确版本分别保存。" onBack={() => navigate('/support/publication')}>
    <SupportReadStatus state={accessState} error={accessError} onRefresh={onRefresh} />
    {!available && accessState === 'default' && <section className="status-panel permission"><h2>当前没有来源维护权限</h2></section>}
    {available && <><section className="card form-card"><h2>维护准确资料</h2>
      <label className="field"><span>来源维护操作</span><select aria-label="来源维护操作" value={operation} disabled={blocked} onChange={event => { setOperation(event.target.value as PublicationOperation); setTargetId(''); setPayload(''); }}>{operations.map(value => <option key={value} value={value}>{publicationOperations[value][3]}</option>)}</select></label>
      {!['source_register', 'retention_policy_create'].includes(operation) && <label className="field"><span>准确目标标识</span><input value={targetId} disabled={blocked} onChange={event => setTargetId(event.target.value)} /></label>}
      <label className="field"><span>完整来源操作正文</span><textarea value={payload} disabled={blocked} onChange={event => setPayload(event.target.value)} placeholder="填写所选操作要求的完整 JSON 正文" /></label>
      <p>正文格式：{publicationOperations[operation][1]}。保存来源版本与批准分别进行；保留政策保存为草稿。</p>
      <button className="button" disabled={blocked || !payload.trim()} onClick={submit}>{busy ? '正在核验保存结果' : publicationOperations[operation][3]}</button>
    </section>
    {error && <p role="alert">{error}</p>}
    {parameters.has('intent_id') && <section className="card"><h2>原提交结果</h2><p>意图：{parameters.get('intent_id')}</p>
      <button className="text-action" disabled={busy} onClick={() => void run(signal => restore(parameters.get('intent_id')!, signal))}>重新读取原请求</button>
      {request && <p>状态：{request.state} · 版本 {request.generation}</p>}
      {receipt && <p role="status">已经保存：{receipt.resource_type} · {receipt.resource_id} · {receipt.saved_at}</p>}
      {request && !request.receipt && (request.may_retry || request.state === 'pending' && Date.parse(request.expires_at!) > Date.now()) && <button className="text-action" disabled={busy} onClick={continueRequest}>继续原请求</button>}
      {request && ['committed', 'failed'].includes(request.state) && <button className="text-action" disabled={busy} onClick={() => { setParameters({}, { replace: true }); setRequest(null); setReceipt(null); setPayload(''); }}>建立新的维护请求</button>}
    </section>}
    <section className="card"><h2>保存的来源版本</h2>{sources.map(source => <div key={source.id}><h3>{source.source_key}</h3><p>{source.id} · {source.status}</p>{source.revisions.map(value => <p key={value.id}>版本 {value.version} · {value.status} · {value.id} · 用途 {value.allowed_use.join('、')}</p>)}</div>)}</section>
    <section className="card"><h2>保存的保留政策</h2>{retentions.map(value => <p key={value.id}>{value.policy_key} · 版本 {value.version} · {value.status} · {value.id}</p>)}</section></>}
  </UI2Frame>;
}
