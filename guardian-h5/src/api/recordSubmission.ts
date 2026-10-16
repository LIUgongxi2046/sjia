import { ApiError, fetchJson } from './client';
import equal from 'fast-deep-equal';
import { validateRecordCreationReceipt, validateRecordIntent, validateRecordRequest } from './legacyResponseSchema';
export { validateRecordCreationReceipt } from './legacyResponseSchema';

let sessionGeneration = 0;

/** One explicit save action. Keep this object until the outcome is known. */
export function createRecordSubmission(patientId: string, payload: Record<string, unknown>, isCurrent = () => true, originalRequestId?: string, onIntentPrepared?: (intentId: string) => void) {
  return createBoundSubmission(patientId, 'record_create', `/api/patients/${patientId}/records`, payload, isCurrent, originalRequestId, onIntentPrepared,
    async value => ({ ...validateRecordCreationReceipt(value, patientId) }));
}

export async function validateCorrectionReceipt(value: unknown, patientId: string, recordId: string, payload: Record<string, unknown>) {
  const { validateCorrectionValue } = await import('./recordSearchSchema');
  const result = validateCorrectionValue<{patient_id: string; record_id: string; record_version_id: string; version: number}>('RecordCorrectionReceipt', value);
  if (result.patient_id !== patientId || result.record_id !== recordId || result.version !== Number(payload.expected_version) + 1) throw new Error('更正回执与本次患儿、记录或原版本不一致。');
  return result;
}

export function createCorrectionSubmission(patientId: string, recordId: string, payload: Record<string, unknown>, isCurrent = () => true, originalRequestId?: string, onIntentPrepared?: (intentId: string) => void) {
  const frozen = JSON.parse(JSON.stringify(payload)) as Record<string, unknown>;
  return createBoundSubmission(patientId, 'record_correct', `/api/records/${recordId}/corrections`, frozen, isCurrent, originalRequestId, onIntentPrepared,
    value => validateCorrectionReceipt(value, patientId, recordId, frozen));
}

function createBoundSubmission(patientId: string, operation: 'record_create' | 'record_correct', target: string, payload: Record<string, unknown>, isCurrent: () => boolean, originalRequestId?: string, onIntentPrepared?: (intentId: string) => void, validateReceipt?: (value: unknown) => Promise<Record<string, unknown>>) {
  const requestId = originalRequestId ?? crypto.randomUUID();
  // Capture the wire representation, including draft metadata, before any await.
  // Neither later form edits nor nested object mutation may change a retry.
  const payloadJson = JSON.stringify(payload);
  const generation = sessionGeneration;
  const controller = new AbortController();
  let pending: Promise<Record<string, unknown>> | null = null;
  let receipt: Record<string, unknown> | null = null;
  let uncertain = Boolean(originalRequestId);
  let preparedIdentity: { id: string; digest: string } | null = null;
  const identityIsCurrent = () => generation === sessionGeneration && isCurrent() && !controller.signal.aborted;
  function checkIdentity() {
    if (!identityIsCurrent()) throw new Error('登录身份或当前患儿已改变，请重新核对记录。');
  }

  return {
    get uncertain() { return uncertain; },
    get requestId() { return requestId; },
    cancel() { controller.abort(); },
    submit(csrfToken?: string): Promise<Record<string, unknown>> {
      try { checkIdentity(); } catch (error) { return Promise.reject(error); }
      if (receipt) return Promise.resolve(receipt);
      if (pending) return pending;
      const body = JSON.parse(payloadJson) as Record<string, unknown>;
      pending = (async () => {
        try {
          // One bounded recovery: an expired, proven-uncommitted intent may be
          // renewed; an unknown/committed result must never get a fresh key.
          for (let recovery = 0; ; recovery += 1) {
            checkIdentity();
            const intent = validateRecordIntent(await fetchJson<unknown>('/api/write-intents', {
              method: 'POST', body: { operation_id: operation, target_key: target, request_id: requestId, payload: body, schema_version: 'back-json-1' }, csrfToken, signal: controller.signal, isCurrent: identityIsCurrent,
            }), requestId, preparedIdentity?.id);
            if (preparedIdentity && preparedIdentity.digest !== intent.payload_sha256) throw new Error('原提交摘要发生变化，请保留本次请求。');
            preparedIdentity = { id: intent.intent_id, digest: intent.payload_sha256 };
            checkIdentity();
            onIntentPrepared?.(intent.intent_id);
            try {
              const value = await fetchJson<Record<string, unknown>>(target, {
                method: 'POST', body, csrfToken, writeIntent: intent.intent_id, intentGeneration: intent.generation, idempotencyKey: requestId, signal: controller.signal, isCurrent: identityIsCurrent,
              });
              checkIdentity();
              const checked = validateReceipt ? await validateReceipt(value) : value;
              checkIdentity();
              receipt = checked;
              return receipt;
            } catch (error) {
              if (!(error instanceof ApiError) || error.code !== 'WRITE_INTENT_EXPIRED' || recovery > 0) throw error;
              uncertain = true;
              const original = validateRecordRequest(await fetchJson<unknown>(`/api/requests/${intent.intent_id}`, { signal: controller.signal, isCurrent: identityIsCurrent }),
                { patientId, operation, target, requestId, intentId: intent.intent_id });
              checkIdentity();
              if (!equal(original.record_input, body) || original.generation !== intent.generation) throw new Error('原提交内容或版本核对失败，请保留本次请求。');
              if (original.status === 'committed' && original.resource_id) continue; // Replay the same committed key.
              if (!original.may_retry || original.resource_id !== null) throw new Error('原提交结果仍未明确，请保留本次请求。');
              validateRecordIntent(await fetchJson<unknown>(`/api/write-intents/${intent.intent_id}/retry`, {
                method: 'POST', body: { expected_generation: original.generation }, csrfToken, signal: controller.signal, isCurrent: identityIsCurrent,
              }), requestId, intent.intent_id, String(BigInt(original.generation) + 1n));
              checkIdentity();
            }
          }
        } catch (error) {
          // A later rejection cannot prove that an earlier lost response did not
          // commit. Only the first definite client rejection permits editing.
          if (!(error instanceof ApiError) || error.status >= 500 || error.status === 408) uncertain = true;
          throw error;
        } finally {
          pending = null;
        }
      })();
      return pending;
    },
  };
}

// Invalidate in-memory submissions without keeping any clinical payload globally.
export function clearPendingRecordRequests() { sessionGeneration += 1; }
