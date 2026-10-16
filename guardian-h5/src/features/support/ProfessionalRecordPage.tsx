import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import type { SupportSessionView } from '../../../../frontend-design/v2-types';
import type { IntentView, WriteReceipt } from '../../../../frontend-design/types';
import { UI2Frame } from '../../components/UI2Frame';
import { fetchJson } from '../../api/client';
import { validateApiValue } from '../../api/schema';
import { SupportReadStatus, type SupportReadState } from './SupportApp';

type Dataset = { schema_version: string; records: Array<{ patient_id: string; record_id: string;
  record_version_id: string; version: number; kind: string; fields: Array<{ field_code: string; raw_text: string | null;
    raw_numeric: string | null; categorical_value: string | null; raw_unit: string | null }> }> };
type Operation = 'professional_record_create' | 'professional_record_correct';
type Identity = { operation_id: Operation; target_key: string; request_id: string; schema_version: '1.0.0'; payload: Record<string, unknown> };
type Recovery = { intent_id: string; request_id: string; operation_id: Operation; target_key: string; generation: string;
  status: string; may_retry: boolean; receipt: WriteReceipt | null; professional_input: Record<string, unknown> };
const checkedDataset = (value: unknown) => validateApiValue<Dataset>('dataset.schema.json#/$defs/clinicalDataset', value);
const receiptValue = (value: unknown) => validateApiValue<WriteReceipt>('protocol.schema.json#/$defs/WriteReceipt', value);

export function ProfessionalRecordPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate(); const [params, setParams] = useSearchParams();
  const available = accessState === 'default' && !!session?.capabilities.some(item => item.capability_id === 'write_professional' && item.state === 'available');
  const [text, setText] = useState(''); const [mode, setMode] = useState<Operation>('professional_record_create');
  const [expectedVersion, setExpectedVersion] = useState(''); const [reason, setReason] = useState('');
  const [review, setReview] = useState<Dataset | null>(null); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [saved, setSaved] = useState<WriteReceipt | null>(null);
  const [locked, setLocked] = useState(Boolean(params.get('professional_intent') && params.get('professional_actor') === session?.principal_id));
  const controller = useRef<AbortController | null>(null); const inFlight = useRef(false);
  const current = useRef<{ identity: Identity; intent: IntentView | null } | null>(null);
  const marker = params.get('professional_actor') === session?.principal_id ? params.get('professional_intent') : null; const composing = useRef(false);
  function datasetFor(identity: Identity) {
    return checkedDataset(identity.operation_id === 'professional_record_correct' ? identity.payload.dataset : identity.payload);
  }
  function targetFor(operation: Operation, dataset: Dataset) {
    if (dataset.records.length !== 1) throw new Error('每次只提交一条准确记录及其领域资料。');
    const record = dataset.records[0];
    return operation === 'professional_record_create' ? `/api/support/patients/${record.patient_id}/records`
      : `/api/support/records/${record.record_id}/corrections`;
  }
  async function query(intentId: string, signal: AbortSignal) {
    const value = await fetchJson<Recovery>(`/api/requests/${intentId}`, { signal });
    if (value.intent_id !== intentId || !['professional_record_create', 'professional_record_correct'].includes(value.operation_id)
      || typeof value.professional_input !== 'object' || !value.professional_input) throw new Error('专业请求恢复资料无效。');
    const identity: Identity = { operation_id: value.operation_id, target_key: value.target_key,
      request_id: value.request_id, schema_version: '1.0.0', payload: value.professional_input };
    const dataset = datasetFor(identity);
    if (targetFor(identity.operation_id, dataset) !== value.target_key) throw new Error('专业请求的目标与原始资料不一致。');
    current.current = { identity, intent: { intent_id: value.intent_id, request_id: value.request_id,
      generation: value.generation, status: value.status as IntentView['status'], expires_at: '', payload_sha256: '' } };
    setReview(dataset); setMode(identity.operation_id);
    if (value.receipt) {
      const receipt = receiptValue(value.receipt);
      if (receipt.request_id !== value.request_id || receipt.resource_type !== 'record_version') throw new Error('专业保存回执不一致。');
      setSaved(receipt);
    }
    return value;
  }
  useEffect(() => {
    if (!available || !marker || inFlight.current) return;
    const request = new AbortController(); controller.current = request; setBusy(true); setError(null);
    void query(marker, request.signal).catch(failure => { if (!request.signal.aborted) setError(failure instanceof Error ? failure.message : '原提交尚未核对。'); })
      .finally(() => { if (!request.signal.aborted) setBusy(false); });
    return () => request.abort();
  }, [available, marker]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { if (!available) controller.current?.abort(); }, [available]);
  async function submit() {
    if (!available || inFlight.current || composing.current || saved || !review) return;
    inFlight.current = true; setBusy(true); setError(null); setLocked(true);
    const request = new AbortController(); controller.current = request;
    try {
      if (!current.current) {
        const dataset = JSON.parse(JSON.stringify(review)) as Dataset;
        const payload = mode === 'professional_record_correct' ? { expected_version: expectedVersion, reason, dataset } : dataset;
        current.current = { identity: { operation_id: mode, target_key: targetFor(mode, dataset), request_id: crypto.randomUUID(),
          schema_version: '1.0.0', payload: payload as unknown as Record<string, unknown> }, intent: null };
      }
      let active = current.current;
      if (active.intent) {
        const original = await query(active.intent.intent_id, request.signal);
        if (original.receipt) return;
        active = current.current!;
        if (original.may_retry) active.intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
          await fetchJson(`/api/write-intents/${original.intent_id}/retry`, { method: 'POST', body: { expected_generation: original.generation }, signal: request.signal }));
      }
      if (!active.intent) {
        active.intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson('/api/write-intents',
          { method: 'POST', body: active.identity, signal: request.signal }));
        setParams(old => { const next = new URLSearchParams(old); next.set('professional_intent', active.intent!.intent_id); next.set('professional_actor', session!.principal_id); return next; }, { replace: true });
      }
      if (request.signal.aborted) return;
      const receipt = receiptValue(await fetchJson(active.identity.target_key, { method: 'POST', body: active.identity.payload,
        idempotencyKey: active.identity.request_id, writeIntent: active.intent.intent_id,
        intentGeneration: active.intent.generation, signal: request.signal }));
      if (receipt.request_id !== active.identity.request_id || receipt.resource_type !== 'record_version') throw new Error('专业保存回执不一致。');
      if (!request.signal.aborted) setSaved(receipt);
    } catch (failure) { if (!request.signal.aborted) setError(failure instanceof Error ? failure.message : '提交结果尚未确认，请查询并重试原提交。'); }
    finally { inFlight.current = false; if (!request.signal.aborted) setBusy(false); }
  }
  return <UI2Frame title="专业记录登记" screenId="PROFESSIONAL-RECORD" note="指定病例、准确来源与逐字段权限均在保存时重新核验。" onBack={() => navigate('/support/verification')}>
    <SupportReadStatus state={accessState !== 'default' ? accessState : !available ? 'permission' : 'default'} error={accessError} onRefresh={onRefresh} />
    {available && <section className="card form-card">
      <fieldset disabled={locked || busy} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
        <label className="field"><span>操作</span><select value={mode} onChange={event => { setMode(event.target.value as Operation); setReview(null); }}>
          <option value="professional_record_create">登记专业来源记录</option><option value="professional_record_correct">更正准确记录版本</option></select></label>
        <label className="field"><span>ClinicalDataset JSON 资料</span><textarea value={text} onChange={event => { setText(event.target.value); setReview(null); }} rows={12} /></label>
        <p>使用已有准确来源、用途授权和保留政策标识。交换资料中的核对证明、映射与人员声明不会授予资格。</p>
        {mode === 'professional_record_correct' && <><label className="field"><span>预期原记录版本</span><input value={expectedVersion} onChange={event => setExpectedVersion(event.target.value)} /></label>
          <label className="field"><span>更正原因</span><textarea value={reason} onChange={event => setReason(event.target.value)} /></label></>}
        <button type="button" className="button secondary" onClick={() => { try { setReview(checkedDataset(JSON.parse(text))); setError(null); }
          catch (failure) { setReview(null); setError(failure instanceof Error ? failure.message : '资料格式无效。'); } }}>核对导入内容</button>
      </fieldset>
      {review && <section aria-label="专业记录核对"><h2>本次记录</h2>{review.records.map(record => <div key={record.record_version_id}>
        <p>患儿 {record.patient_id} · {record.kind} · 新版本 {record.version}</p>
        {record.fields.map((field, index) => <p key={index}>{field.field_code}：{field.raw_text ?? field.raw_numeric ?? field.categorical_value ?? '未知'} {field.raw_unit}</p>)}</div>)}</section>}
      {error && <p role="alert">{error}</p>}
      {!saved && <button type="button" className="button" disabled={busy || !review || mode === 'professional_record_correct' && !locked && (!/^[1-9][0-9]*$/.test(expectedVersion) || !reason.trim())} onClick={() => void submit()}>
        {busy ? '正在核对保存结果' : locked ? '查询并重试原提交' : '确认并保存专业记录'}</button>}
      {saved && <section role="status"><p>记录版本 {saved.version} 已保存。专业核对资格仍需单独取得。</p>
        <button type="button" className="button secondary" onClick={() => navigate(`/support/verification?record_version_id=${saved.resource_id}`)}>查看新版本与核对入口</button>
        <button type="button" className="text-action" onClick={() => { current.current = null; setSaved(null); setReview(null); setText(''); setLocked(false); setReason(''); setExpectedVersion('');
          setParams(old => { const next = new URLSearchParams(old); next.delete('professional_intent'); next.delete('professional_actor'); return next; }, { replace: true }); }}>登记另一条记录</button></section>}
    </section>}
  </UI2Frame>;
}
