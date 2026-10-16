import isEqual from 'fast-deep-equal';
import type { IntentView } from '../../../frontend-design/types';
import type { RegroupIdentity, RegroupOptions, RegroupReceipt, RegroupRequest } from '../../../frontend-design/archive-regroup-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';
const operation = 'archive_documents_regroup';
const schema = <T,>(name: string, value: unknown) => validateApiValue<T>(`v2-protocol.schema.json#/$defs/${name}`, value);
const checked = async <T,>(name: string, path: string, options: Parameters<typeof fetchJson>[1]) => schema<T>(name, await fetchJson(path, options));
export function regroupTarget(patientId: string) { schema('Uuid', patientId); return `/patients/${patientId}/archive-document-regroupings`; }
export async function readRegroupOptions(batchId: string, patientId: string, signal?: AbortSignal) {
  schema('Uuid', batchId);
  const value = await checked<RegroupOptions>('RegroupOptions', `/api/v2/archive-batches/${batchId}/regroup-options`, { signal });
  if (value.patient_id !== patientId || value.batch_id !== batchId || value.documents.some(item => item.patient_id !== patientId || item.batch_id !== batchId)
    || new Set(value.documents.map(item => item.document_id)).size !== value.documents.length) throw new Error('可重组文书与当前患儿或批次不一致。');
  return value;
}
function identityChecked(identity: RegroupIdentity) {
  schema('RegroupIntentInput', identity);
  const patient = /^\/patients\/([0-9a-f-]{36})\/archive-document-regroupings$/.exec(identity.target_key)?.[1];
  if (!patient || regroupTarget(patient) !== identity.target_key) throw new Error('文书重组目标不一致。');
  return patient;
}
export function checkRegroupReceipt(receipt: RegroupReceipt, identity: RegroupIdentity) {
  const patient = identityChecked(identity), input = identity.payload;
  schema('RegroupReceipt', receipt);
  const result = receipt.result, reused = input.outputs.flatMap(item => item.document_id ? [item.document_id] : []);
  if (receipt.request_id !== identity.request_id || receipt.version !== '1' || receipt.task_id !== null
    || result.regrouping_id !== receipt.resource_id || result.patient_id !== patient || result.batch_id !== input.batch_id
    || !isEqual(result.unassigned_page_ids, input.unassigned_page_ids)
    || !isEqual([...result.retired_document_ids].sort(), input.sources.map(item => item.document_id).filter(id => !reused.includes(id)).sort())
    || !isEqual(result.sources.map(item => ({ document_id: item.document_id, expected_version: item.version })).sort((a, b) => a.document_id.localeCompare(b.document_id)),
      [...input.sources].sort((a, b) => a.document_id.localeCompare(b.document_id)))
    || result.outputs.length !== input.outputs.length || new Set(result.outputs.map(item => item.document_id)).size !== result.outputs.length
    || result.outputs.some((item, index) => !isEqual(item.page_ids, input.outputs[index].page_ids)
      || (input.outputs[index].document_id ? item.document_id !== input.outputs[index].document_id
        || BigInt(item.version) !== BigInt(input.sources.find(source => source.document_id === item.document_id)!.expected_version) + 1n : item.version !== '1')))
    throw new Error('重组回执与原请求的文书、页序或版本不一致。');
  return receipt;
}
export async function prepareRegroup(identity: RegroupIdentity, signal?: AbortSignal) {
  identityChecked(identity);
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (value.request_id !== identity.request_id) throw new Error('重组意图身份不一致。');
  return value;
}
export async function submitRegroup(identity: RegroupIdentity, intent: IntentView, signal?: AbortSignal) {
  identityChecked(identity);
  if (intent.request_id !== identity.request_id) throw new Error('重组请求身份不一致。');
  return checkRegroupReceipt(await checked<RegroupReceipt>('RegroupReceipt', `/api/v2${identity.target_key}`, { method: 'POST', body: identity.payload,
    signal, writeIntent: intent.intent_id, idempotencyKey: identity.request_id, intentGeneration: intent.generation }), identity);
}
export async function readRegroupRequest(patientId: string, batchId: string, identifier: string, lookup: boolean, signal?: AbortSignal) {
  schema('Uuid', identifier);
  const target = regroupTarget(patientId);
  const query = lookup ? `?${new URLSearchParams({ lookup: 'request_id', operation_id: operation, target_key: target })}` : '';
  const value = await checked<RegroupRequest>('RegroupRequestView', `/api/v2/requests/${identifier}${query}`, { signal });
  if (value.operation_id !== operation || value.target_key !== target || value.regroup_input.batch_id !== batchId
      || (lookup ? value.request_id : value.intent_id) !== identifier) throw new Error('重组恢复结果与原请求、患儿或批次不一致。');
  if (value.receipt) checkRegroupReceipt(value.receipt, { operation_id: operation, target_key: target,
    request_id: value.request_id, schema_version: '2.0.0', payload: value.regroup_input });
  if ((value.state === 'committed') !== !!value.receipt) throw new Error('重组结果状态和回执不一致。');
  return value;
}
export async function retryRegroup(request: RegroupRequest, signal?: AbortSignal) {
  if (!request.may_retry) throw new Error('当前请求尚未获准重新提交。');
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson(`/api/v2/write-intents/${request.intent_id}/retry`,
    { method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || BigInt(value.generation) !== BigInt(request.generation) + 1n)
    throw new Error('重组重试身份或代次不一致。');
  return value;
}
