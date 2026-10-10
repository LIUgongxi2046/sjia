import { fetchJson } from './client';
import { validateApiValue } from './schema';
import type { IntentView } from '../../../frontend-design/types';
import type { DispositionInput, DispositionOptions, DispositionView, WriteReceipt } from '../../../frontend-design/v2-types';

export interface DispositionIdentity { requestId: string; target: string; payload: DispositionInput }
export interface DispositionRequestView { intent_id: string; request_id: string; operation_id: 'disposition_create'; target_key: string;
  state: 'pending' | 'processing' | 'committed' | 'failed' | 'unknown'; may_retry: boolean; receipt: WriteReceipt | null; disposition_input: DispositionInput }
async function read<T>(path: string, schema: string, options: Parameters<typeof fetchJson>[1]): Promise<T> {
  return validateApiValue(`v2-protocol.schema.json#/$defs/${schema}`, await fetchJson<unknown>(path, options));
}
export function readDispositionOptions(patientId: string, signal?: AbortSignal): Promise<DispositionOptions> {
  return read(`/api/v2/patients/${encodeURIComponent(patientId)}/disposition-options`, 'DispositionOptions', { signal });
}
export function readDisposition(id: string, signal?: AbortSignal): Promise<DispositionView> {
  return read(`/api/v2/dispositions/${encodeURIComponent(id)}`, 'DispositionView', { signal });
}
export function readDispositionRequest(id: string, signal?: AbortSignal): Promise<DispositionRequestView> {
  return read(`/api/v2/requests/${encodeURIComponent(id)}`, 'RequestView', { signal });
}
export async function prepareDisposition(identity: DispositionIdentity, signal?: AbortSignal): Promise<IntentView> {
  validateApiValue('v2-protocol.schema.json#/$defs/DispositionInput', identity.payload);
  return validateApiValue('protocol.schema.json#/$defs/IntentView', await fetchJson('/api/v2/write-intents', { method: 'POST', signal,
    body: { request_id: identity.requestId, operation_id: 'disposition_create', target_key: identity.target, schema_version: '2.0.0', payload: identity.payload } }));
}
export function submitDisposition(identity: DispositionIdentity, intent: IntentView, signal?: AbortSignal): Promise<WriteReceipt> {
  if (intent.request_id !== identity.requestId) throw new Error('处置提交身份与原请求不一致。');
  return read('/api/v2' + identity.target, 'WriteReceipt', { method: 'POST', signal, body: identity.payload,
    idempotencyKey: identity.requestId, writeIntent: intent.intent_id, intentGeneration: intent.generation });
}
