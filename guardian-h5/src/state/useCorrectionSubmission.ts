import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { IntentView, WriteReceipt } from '../../../frontend-design/types';
import { ApiError } from '../api/client';
import { prepareCorrection, queryCorrection, retryCorrection, submitCorrection, type CorrectionIdentity, type CorrectionRequest } from '../api/corrections';

export function useCorrectionSubmission(available: boolean, expectedTarget: string, onSaved: (receipt: WriteReceipt, signal: AbortSignal) => Promise<void>) {
  const [params, setParams] = useSearchParams();
  const [phase, setPhase] = useState<'idle' | 'pending' | 'unknown' | 'saved'>(params.has('correction_request_idempotency') ? 'unknown' : 'idle');
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [request, setRequest] = useState<CorrectionRequest | null>(null);
  const busy = useRef(false); const controller = useRef<AbortController | null>(null);
  const current = useRef<{ identity: CorrectionIdentity; intent: IntentView | null } | null>(null);
  const save = useRef(onSaved); save.current = onSaved;
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => { if (!available) controller.current?.abort(); }, [available]);
  const remember = (identity: CorrectionIdentity, intent: IntentView | null) => {
    setParams(old => { const next = new URLSearchParams(old); next.set('correction_request_idempotency', identity.request_id);
      next.set('correction_operation', identity.operation_id); next.set('correction_target', identity.target_key);
      if (intent) next.set('correction_intent', intent.intent_id); return next; }, { replace: true });
  };
  async function run(identity: CorrectionIdentity, intent: IntentView | null, signal: AbortSignal) {
    if (identity.target_key !== expectedTarget) throw new Error('原请求不属于当前更正目标。');
    current.current = { identity, intent }; remember(identity, intent);
    const prepared = intent ?? await prepareCorrection(identity, signal);
    current.current = { identity, intent: prepared }; remember(identity, prepared);
    const receipt = await submitCorrection(identity, prepared, signal);
    await save.current(receipt, signal);
    if (!signal.aborted) setPhase('saved');
  }
  async function start(identity: CorrectionIdentity) {
    if (!available || busy.current || phase === 'unknown' || phase === 'pending' || phase === 'saved') return;
    busy.current = true; controller.current = new AbortController(); setPhase('pending'); setError(null);
    try { await run(identity, null, controller.current.signal); }
    catch (failure) { setPhase('unknown'); setError(failure instanceof Error ? failure.message : '提交结果需要查询。'); }
    finally { busy.current = false; }
  }
  async function recover(action: 'query' | 'continue' | 'retry' = 'query') {
    if (!available || busy.current) return;
    busy.current = true; controller.current = new AbortController(); const signal = controller.current.signal;
    setPhase('pending'); setError(null);
    const key = current.current?.identity ?? { request_id: params.get('correction_request_idempotency') ?? '',
      operation_id: params.get('correction_operation') as CorrectionIdentity['operation_id'], target_key: params.get('correction_target') ?? '' };
    try {
      if (key.target_key !== expectedTarget) throw new Error('原请求不属于当前更正目标。');
      const original = await queryCorrection(key, current.current?.intent?.intent_id ?? params.get('correction_intent'), signal);
      setNotFound(false);
      if (signal.aborted) return;
      setRequest(original);
      if (original.receipt) { await save.current(original.receipt, signal); if (!signal.aborted) setPhase('saved'); return; }
      const identity: CorrectionIdentity = { ...key, schema_version: '1.0.0', payload: original.correction_input };
      if (action === 'retry') { await run(identity, await retryCorrection(original, signal), signal); return; }
      if (action === 'continue' && original.state === 'pending' && !original.may_retry && original.generation && original.expires_at && original.payload_sha256) {
        await run(identity, { intent_id: original.intent_id, request_id: original.request_id, generation: original.generation,
          status: 'prepared', expires_at: original.expires_at, payload_sha256: original.payload_sha256 }, signal); return;
      }
      setPhase('unknown');
    } catch (failure) {
      setPhase('unknown'); setError(failure instanceof Error ? failure.message : '原请求尚未核验。');
      if (failure instanceof ApiError && failure.status === 404 && !current.current?.intent && !params.has('correction_intent')) setNotFound(true);
      // A missing prepare response may be retried with exactly the same identity and body.
      if (failure instanceof ApiError && failure.status === 404 && current.current && !current.current.intent && action === 'continue') {
        try { await run(current.current.identity, null, signal); } catch (retryFailure) { setError(retryFailure instanceof Error ? retryFailure.message : '原请求仍需核验。'); }
      }
    } finally { busy.current = false; }
  }
  function resetUnregistered() {
    if (!notFound || busy.current || current.current?.intent || params.has('correction_intent')) return;
    current.current = null; setNotFound(false); setRequest(null); setPhase('idle'); setError(null);
    setParams(old => { const next = new URLSearchParams(old); for (const key of ['correction_request_idempotency', 'correction_operation', 'correction_target', 'correction_intent']) next.delete(key); return next; }, { replace: true });
  }
  return { phase, error, request, start, recover, notFound, resetUnregistered, locked: phase !== 'idle', hasIdentity: params.has('correction_request_idempotency') || current.current !== null };
}
