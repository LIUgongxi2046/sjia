import isEqual from 'fast-deep-equal';
import type { IntentView, MedicalTimeInput } from '../../../frontend-design/types';
import type { DocumentInput, DocumentInputsView, DocumentIntentInput, DocumentView, GroupingInput, RequestView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

export interface DocumentRequestIdentity extends DocumentIntentInput {}
export async function readDocumentInputs(batchId: string, patientId: string, signal?: AbortSignal) {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', batchId);
  const value = await checked<DocumentInputsView>(`/api/v2/archive-batches/${batchId}/document-inputs`, 'DocumentInputsView', { signal });
  if (value.batch_id !== batchId || value.patient_id !== patientId || new Set(value.episodes.map(item => item.episode_id)).size !== value.episodes.length) throw new Error('就诊来源与当前患儿或批次不一致。');
  return value;
}

function documentTarget(identity: DocumentRequestIdentity): string {
  validateApiValue('v2-protocol.schema.json#/$defs/DocumentIntentInput', identity);
  const path = identity.target_key;
  const expected = identity.operation_id === 'archive_document_create'
    ? /^\/patients\/([0-9a-f-]{36})\/archive-documents$/
    : /^\/archive-documents\/([0-9a-f-]{36})\/grouping$/;
  const match = expected.exec(path);
  if (!match) throw new Error('文书提交目标与当前操作不一致。');
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', match[1]);
  return path;
}

async function checked<T>(path: string, schema: string, options: Parameters<typeof fetchJson>[1]): Promise<T> {
  const value = await fetchJson<unknown>(path, options);
  return validateApiValue<T>(`v2-protocol.schema.json#/$defs/${schema}`, value);
}

export async function readArchiveDocument(documentId: string, patientId: string, signal?: AbortSignal): Promise<DocumentView> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', documentId);
  const document = await checked<DocumentView>(`/api/v2/archive-documents/${encodeURIComponent(documentId)}`,
    'DocumentView', { signal });
  if (document.document_id !== documentId || document.patient_id !== patientId) throw new Error('文书来源与当前患儿或文书身份不一致。');
  return document;
}

export async function prepareDocumentRequest(identity: DocumentRequestIdentity, signal?: AbortSignal): Promise<IntentView> {
  documentTarget(identity);
  const value = await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal });
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', value);
  if (intent.request_id !== identity.request_id) throw new Error('文书提交意图与当前请求身份不一致。');
  return intent;
}

export async function submitDocumentRequest(identity: DocumentRequestIdentity, intent: IntentView,
  signal?: AbortSignal): Promise<WriteReceipt> {
  const target = documentTarget(identity);
  validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('文书提交身份与保存意图不一致。');
  const receipt = await checked<WriteReceipt>(`/api/v2${target}`, 'WriteReceipt', { method: 'POST',
    body: identity.payload, signal, idempotencyKey: identity.request_id,
    writeIntent: intent.intent_id, intentGeneration: intent.generation });
  if (receipt.resource_type !== 'archive_document' || receipt.request_id !== identity.request_id || receipt.task_id !== null)
    throw new Error('文书保存回执与当前提交不一致。');
  if (identity.operation_id === 'archive_document_group') {
    const input = validateApiValue<GroupingInput>('v2-protocol.schema.json#/$defs/GroupingInput', identity.payload);
    if (target !== `/archive-documents/${receipt.resource_id}/grouping` || BigInt(receipt.version) !== BigInt(input.expected_version) + 1n)
      throw new Error('文书更正回执与准确原版本不一致。');
  } else if (receipt.version !== '1') throw new Error('文书创建没有返回准确初始版本。');
  return receipt;
}

export async function readDocumentRequest(intentId: string, signal?: AbortSignal): Promise<RequestView> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', intentId);
  const result = await checked<RequestView>(`/api/v2/requests/${encodeURIComponent(intentId)}`, 'RequestView', { signal });
  return checkDocumentRequest(result, intentId);
}

export async function findDocumentRequest(requestId: string, operationId: DocumentIntentInput['operation_id'], targetKey: string,
  signal?: AbortSignal): Promise<RequestView> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', requestId);
  const lookup = { lookup: 'request_id', operation_id: operationId, target_key: targetKey };
  validateApiValue('v2-protocol.schema.json#/$defs/DocumentRequestLookup', lookup);
  const query = new URLSearchParams(lookup);
  const result = checkDocumentRequest(await checked<RequestView>(`/api/v2/requests/${encodeURIComponent(requestId)}?${query}`, 'RequestView', { signal }));
  if (result.request_id !== requestId || result.operation_id !== operationId || result.target_key !== targetKey)
    throw new Error('原请求查询与当前请求身份或目标不一致。');
  return result;
}

function checkDocumentRequest(result: RequestView, intentId?: string): RequestView {
  if (intentId !== undefined && result.intent_id !== intentId || !result.document_input || !result.generation || !result.expires_at || !result.payload_sha256
      || !['archive_document_create', 'archive_document_group'].includes(result.operation_id))
    throw new Error('文书保存请求缺少准确身份或可恢复内容。');
  documentTarget({ operation_id: result.operation_id as DocumentIntentInput['operation_id'], target_key: result.target_key,
    request_id: result.request_id, schema_version: '2.0.0', payload: result.document_input });
  if (result.receipt && (result.receipt.resource_type !== 'archive_document' || result.receipt.request_id !== result.request_id))
    throw new Error('文书结果回执与原请求身份不一致。');
  return result;
}

export async function retryDocumentRequest(request: RequestView, signal?: AbortSignal): Promise<IntentView> {
  if (!request.may_retry || !request.generation) throw new Error('当前文书请求不具备重新提交条件。');
  const value = await fetchJson<unknown>(`/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, {
    method: 'POST', body: { expected_generation: request.generation }, signal });
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', value);
  if (intent.intent_id !== request.intent_id || intent.request_id !== request.request_id
      || BigInt(intent.generation) !== BigInt(request.generation) + 1n) throw new Error('文书重新提交意图与原请求不一致。');
  return intent;
}

export function documentPayloadMatches(document: DocumentView, input: DocumentInput): boolean {
  return document.batch_id === input.batch_id && document.document_type === input.document_type
    && document.organization === input.organization
    && isEqual(document.page_ids, input.page_ids)
    && isEqual([...document.episode_ids].sort(), [...input.episode_ids].sort())
    && documentTimeMatches(document.documented_time, input.documented_time)
    && (input.confirmed_text === undefined || document.confirmed_text === input.confirmed_text)
    && (input.proposed_metadata === undefined || isEqual(document.proposed_metadata, input.proposed_metadata));
}

function documentTimeMatches(saved: DocumentView['documented_time'], input: MedicalTimeInput | null): boolean {
  if (saved === null || input === null) return saved === input;
  for (const [key, value] of Object.entries(input)) {
    if (saved.resolution_status === 'resolved' && input.resolution_status === 'local_only'
      && (key === 'instant' || key === 'resolution_status')) continue;
    if (key === 'local_datetime' && value !== null && saved.local_datetime !== null) {
      if (Date.parse(`${saved.local_datetime}Z`) !== Date.parse(`${value}Z`)) return false;
    } else if (!isEqual(saved[key as keyof typeof saved], value)) return false;
  }
  return true;
}
