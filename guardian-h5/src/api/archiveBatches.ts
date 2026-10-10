import type { IntentView } from '../../../frontend-design/types';
import type { BatchActionIntentInput, BatchIntentInput, BatchInputsView, BatchView, RequestView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

export type BatchIdentity = BatchIntentInput | BatchActionIntentInput;
const schema = 'v2-protocol.schema.json#/$defs/';
export function batchTarget(identity: BatchIdentity) {
  validateApiValue(schema + (identity.operation_id === 'archive_batch_create' ? 'BatchIntentInput' : 'BatchActionIntentInput'), identity);
  const patterns = { archive_batch_create: /^\/patients\/([0-9a-f-]{36})\/archive-batches$/,
    archive_batch_process: /^\/archive-batches\/([0-9a-f-]{36})\/process$/,
    archive_page_retry: /^\/archive-pages\/([0-9a-f-]{36})\/retry$/,
    archive_page_cancel: /^\/archive-pages\/([0-9a-f-]{36})\/cancel$/ };
  const matched = patterns[identity.operation_id].exec(identity.target_key);
  if (!matched) throw new Error('批次操作目标与当前动作不一致。');
  validateApiValue(schema + 'Uuid', matched[1]);
  return identity.target_key;
}
export async function batchInputs(patientId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', patientId);
  const value = validateApiValue<BatchInputsView>(schema + 'BatchInputsView', await fetchJson<unknown>(
    `/api/v2/patients/${patientId}/archive-batch-inputs`, { signal }));
  if (value.patient_id !== patientId) throw new Error('可接收原件与当前患儿不一致。');
  return value;
}
export async function readBatch(batchId: string, patientId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', batchId);
  const value = validateApiValue<BatchView>(schema + 'BatchView', await fetchJson<unknown>(`/api/v2/archive-batches/${batchId}`, { signal }));
  if (value.batch_id !== batchId || value.patient_id !== patientId || value.pages.some(page => page.batch_id !== batchId))
    throw new Error('批次或原页身份与当前资料不一致。');
  return value;
}
export async function prepareBatch(identity: BatchIdentity, signal: AbortSignal) {
  batchTarget(identity);
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    '/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (value.request_id !== identity.request_id) throw new Error('批次提交意图与原请求不一致。');
  return value;
}
export async function submitBatch(identity: BatchIdentity, intent: IntentView, signal: AbortSignal) {
  const target = batchTarget(identity);
  if (intent.request_id !== identity.request_id) throw new Error('批次意图和原请求身份不一致。');
  const value = validateApiValue<WriteReceipt>(schema + 'WriteReceipt', await fetchJson<unknown>('/api/v2' + target, {
    method: 'POST', body: identity.payload, signal, idempotencyKey: identity.request_id,
    writeIntent: intent.intent_id, intentGeneration: intent.generation }));
  const type = identity.operation_id.startsWith('archive_page_') ? 'archive_page' : 'archive_batch';
  if (value.request_id !== identity.request_id || value.resource_type !== type || value.task_id !== null
      || identity.operation_id !== 'archive_batch_create' && !target.includes(value.resource_id))
    throw new Error('批次回执与原请求或资料身份不一致。');
  return value;
}
export async function batchRequest(identifier: string, lookup?: { operation: BatchIdentity['operation_id']; target: string }, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', identifier);
  const query = lookup ? '?' + new URLSearchParams({ lookup: 'request_id', operation_id: lookup.operation, target_key: lookup.target }) : '';
  const value = validateApiValue<RequestView>(schema + 'RequestView', await fetchJson<unknown>(`/api/v2/requests/${identifier}${query}`, { signal }));
  const input = value.operation_id === 'archive_batch_create' ? value.batch_input : value.batch_action_input;
  if (!input || lookup && (value.request_id !== identifier || value.operation_id !== lookup.operation || value.target_key !== lookup.target)
      || !lookup && value.intent_id !== identifier) throw new Error('原批次请求缺少准确身份或可恢复正文。');
  const identity = { operation_id: value.operation_id, request_id: value.request_id, target_key: value.target_key,
    schema_version: '2.0.0', payload: input } as BatchIdentity;
  batchTarget(identity);
  return { request: value, identity };
}
export async function retryBatchRequest(request: RequestView, signal: AbortSignal) {
  if (!request.may_retry || !request.generation) throw new Error('当前原请求没有重新准备资格。');
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    `/api/v2/write-intents/${request.intent_id}/retry`, { method: 'POST', signal, body: { expected_generation: request.generation } }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id
      || BigInt(value.generation) !== BigInt(request.generation) + 1n) throw new Error('重新准备意图与原批次请求不一致。');
  return value;
}
