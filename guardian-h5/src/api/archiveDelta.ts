import isEqual from 'fast-deep-equal';
import type { IntentView } from '../../../frontend-design/types';
import type { BatchList, DocumentList, WriteReceipt } from '../../../frontend-design/v2-types';
import type { DeltaIdentity, DeltaRequest, DeltaTask, DeltaView } from '../../../frontend-design/archive-delta-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'archive-delta.schema.json#/$defs/';

function target(identity: DeltaIdentity) {
  validateApiValue(`${schema}Intent`, identity);
  const value = /^\/archive-batches\/([0-9a-f-]{36})\/delta-summaries$/.exec(identity.target_key);
  if (!value) throw new Error('摘要操作与当前批次不一致。');
  validateApiValue(`${schema}Uuid`, value[1]);
  return value[1];
}

export async function readDeltaBatches(patientId: string, cursor?: string, signal?: AbortSignal): Promise<BatchList> {
  validateApiValue(`${schema}Uuid`, patientId);
  const value = validateApiValue<BatchList>('v2-protocol.schema.json#/$defs/BatchList', await fetchJson<unknown>(
    `/api/v2/patients/${encodeURIComponent(patientId)}/archive-batches/search`, { method: 'POST', body: { limit: 20, ...(cursor ? { cursor } : {}) }, signal }));
  if (value.items.some(row => row.patient_id !== patientId || row.pages.some(page => page.batch_id !== row.batch_id)))
    throw new Error('批次与当前患儿或原页身份不一致。');
  return value;
}

export async function readDeltaDocumentHistory(documentId: string, patientId: string, cursor?: string, signal?: AbortSignal): Promise<DocumentList> {
  validateApiValue(`${schema}Uuid`, documentId);
  const query = new URLSearchParams({ limit: '20', ...(cursor ? { cursor } : {}) });
  const value = validateApiValue<DocumentList>('v2-protocol.schema.json#/$defs/DocumentList', await fetchJson<unknown>(
    `/api/v2/archive-documents/${encodeURIComponent(documentId)}/versions?${query}`, { signal }));
  if (value.items.some(row => row.document_id !== documentId || row.patient_id !== patientId)) throw new Error('文书版本与当前患儿不一致。');
  return value;
}

export async function prepareDelta(identity: DeltaIdentity, signal: AbortSignal) {
  target(identity);
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (intent.request_id !== identity.request_id) throw new Error('摘要意图与原请求身份不一致。');
  return intent;
}

export async function submitDelta(identity: DeltaIdentity, intent: IntentView, signal: AbortSignal) {
  target(identity);
  validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('摘要意图与提交身份不一致。');
  const receipt = validateApiValue<WriteReceipt>('v2-protocol.schema.json#/$defs/WriteReceipt', await fetchJson<unknown>(
    `/api/v2${identity.target_key}`, { method: 'POST', body: identity.payload, signal, idempotencyKey: identity.request_id,
      writeIntent: intent.intent_id, intentGeneration: intent.generation }));
  if (receipt.resource_type !== 'archive_delta' || receipt.request_id !== identity.request_id || receipt.version !== '1' || !receipt.task_id)
    throw new Error('摘要回执与实际任务或原请求不一致。');
  return receipt;
}

export async function readDelta(deltaId: string, patientId: string, signal: AbortSignal) {
  validateApiValue(`${schema}Uuid`, deltaId);
  const value = validateApiValue<DeltaView>(`${schema}View`, await fetchJson<unknown>(`/api/v2/archive-delta-summaries/${encodeURIComponent(deltaId)}`, { signal }));
  if (value.delta_id !== deltaId || value.patient_id !== patientId || value.status === 'superseded' && (value.validity !== 'invalid' || value.items.length)
    || value.items.some(item => item.source_refs.some(source => item.before?.fact_version_id === source.fact_version_id && source.field_id !== item.before.field_id
      || item.after?.fact_version_id === source.fact_version_id && source.field_id !== item.after.field_id))) throw new Error('摘要范围、失效状态或事实来源不一致。');
  const task = validateApiValue<DeltaTask>(`${schema}Task`, await fetchJson<unknown>(`/api/v2/archive-delta-tasks/${encodeURIComponent(value.task_id)}`, { signal }));
  if (task.task_id !== value.task_id || task.patient_id !== patientId || task.result_ref !== deltaId || task.status !== value.status)
    throw new Error('摘要与实际任务的保存结果不一致，请重新读取。');
  return value;
}

export async function readDeltaRequest(intentId: string, signal: AbortSignal, identity?: Pick<DeltaIdentity, 'request_id' | 'target_key'>) {
  validateApiValue(`${schema}Uuid`, intentId);
  const query = identity ? `?${new URLSearchParams({ lookup: 'request_id', operation_id: 'archive_delta_create', target_key: identity.target_key })}` : '';
  const value = validateApiValue<DeltaRequest>(`${schema}Request`, await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(intentId)}${query}`, { signal }));
  target({ operation_id: value.operation_id, target_key: value.target_key, request_id: value.request_id, schema_version: '2.0.0', payload: value.delta_input });
  if (!identity && value.intent_id !== intentId || identity && (value.request_id !== identity.request_id || value.target_key !== identity.target_key)
    || value.receipt && (value.receipt.resource_type !== 'archive_delta' || value.receipt.request_id !== value.request_id || !value.receipt.task_id))
    throw new Error('摘要原请求与提交目标或回执不一致。');
  return value;
}

export async function retryDelta(request: DeltaRequest, signal: AbortSignal) {
  if (!request.may_retry || request.receipt) throw new Error('摘要原请求当前不具备重新提交条件。');
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    `/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, { method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || BigInt(value.generation) !== BigInt(request.generation) + 1n)
    throw new Error('摘要重新提交与原意图身份不一致。');
  return value;
}

export function checkDeltaReceipt(value: DeltaView, receipt: WriteReceipt, identity: DeltaIdentity) {
  const batch = target(identity);
  if (value.delta_id !== receipt.resource_id || value.task_id !== receipt.task_id || value.batch_id !== batch
    || value.baseline_batch_id !== identity.payload.baseline_batch_id
    || identity.payload.baseline_document_version_ids.length && !isEqual([...value.baseline_document_version_ids].sort(), [...identity.payload.baseline_document_version_ids].sort()))
    throw new Error('摘要重新读取的内容与原批次、基准和回执不一致。');
}
