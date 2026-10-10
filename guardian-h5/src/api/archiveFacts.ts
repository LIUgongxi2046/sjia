import type { IntentView } from '../../../frontend-design/types';
import type { FactCandidateSet, FactIntentInput, FactList, FactView, PageOriginalView, PageView, RequestView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'v2-protocol.schema.json#/$defs/';
export async function readPage(pageId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', pageId);
  const value = validateApiValue<PageView>(schema + 'PageView', await fetchJson<unknown>(`/api/v2/archive-pages/${pageId}`, { signal }));
  if (value.page_id !== pageId) throw new Error('原页身份与当前资料不一致。');
  return value;
}
export async function readCandidates(page: PageView, signal?: AbortSignal) {
  const value = validateApiValue<FactCandidateSet>(schema + 'FactCandidateSet', await fetchJson<unknown>(`/api/v2/archive-pages/${page.page_id}/candidates`, { signal }));
  if (value.page_id !== page.page_id || value.page_version !== page.version || value.candidate_set_id !== page.candidate_set_id
      || new Set(value.items.map(item => item.candidate_id)).size !== value.items.length
      || value.items.some(item => item.page_id !== page.page_id || item.field.candidate_field_id !== item.candidate_id
        || item.field.anchor_id !== item.source_anchor_id || !item.source_quote)) throw new Error('候选与准确原页、来源及版本不一致。');
  return value;
}
export async function readPageFacts(page: PageView, patientId: string, signal?: AbortSignal) {
  const value = validateApiValue<FactList>(schema + 'FactList', await fetchJson<unknown>(`/api/v2/archive-pages/${page.page_id}/facts`, { signal }));
  if (value.items.some(item => item.patient_id !== patientId || item.page_id !== page.page_id || item.source.file_id !== page.file_id)
      || new Set(value.items.map(item => item.fact_id)).size !== value.items.length) throw new Error('已保存事实与当前患儿及原件不一致。');
  return value;
}
export async function readFact(factId: string, patientId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', factId);
  const value = validateApiValue<FactView>(schema + 'FactView', await fetchJson<unknown>(`/api/v2/archive-facts/${factId}`, { signal }));
  if (value.fact_id !== factId || value.patient_id !== patientId) throw new Error('事实与当前患儿不一致。');
  return value;
}
export async function readPageOriginal(pageId: string, patientId: string, signal?: AbortSignal) {
  const value = validateApiValue<PageOriginalView>(schema + 'PageOriginalView', await fetchJson<unknown>(`/api/v2/archive-pages/${pageId}/original`, { signal }));
  if (value.page.page_id !== pageId || value.patient_id !== patientId || value.file.file_id !== value.page.file_id
      || value.file.checksum !== value.page.checksum) throw new Error('原件与当前患儿、原页及校验值不一致。');
  return value;
}
function checkIdentity(identity: FactIntentInput) {
  validateApiValue(schema + 'Uuid', identity.request_id);
  validateApiValue(schema + (identity.operation_id === 'archive_candidates_confirm' ? 'FactConfirmationInput' : 'FactCorrectionInput'), identity.payload);
  const pattern = identity.operation_id === 'archive_candidates_confirm' ? /^\/archive-pages\/([0-9a-f-]{36})\/confirm$/ : /^\/archive-facts\/([0-9a-f-]{36})\/corrections$/;
  const target = pattern.exec(identity.target_key);
  if (!target) throw new Error('事实操作缺少准确目标。');
  validateApiValue(schema + 'Uuid', target[1]);
  return target[1];
}
export async function prepareFact(identity: FactIntentInput, signal: AbortSignal) {
  checkIdentity(identity);
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (value.request_id !== identity.request_id) throw new Error('事实意图与原请求不一致。');
  return value;
}
export async function submitFact(identity: FactIntentInput, intent: IntentView, signal: AbortSignal) {
  const target = checkIdentity(identity);
  if (intent.request_id !== identity.request_id) throw new Error('事实提交意图与原请求不一致。');
  const value = validateApiValue<WriteReceipt>(schema + 'WriteReceipt', await fetchJson<unknown>('/api/v2' + identity.target_key,
    { method: 'POST', body: identity.payload, signal, writeIntent: intent.intent_id, idempotencyKey: identity.request_id, intentGeneration: intent.generation }));
  if (value.request_id !== identity.request_id || value.resource_id !== target || value.task_id !== null
      || value.resource_type !== (identity.operation_id === 'archive_candidates_confirm' ? 'archive_page' : 'archive_fact')) throw new Error('事实回执与原请求不一致。');
  return value;
}
export async function factRequest(identifier: string, lookup?: { operation: FactIntentInput['operation_id']; target: string }, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', identifier);
  const query = lookup ? '?' + new URLSearchParams({ lookup: 'request_id', operation_id: lookup.operation, target_key: lookup.target }) : '';
  const value = validateApiValue<RequestView>(schema + 'RequestView', await fetchJson<unknown>(`/api/v2/requests/${identifier}${query}`, { signal }));
  if (!value.fact_input || !['archive_candidates_confirm', 'archive_fact_correct'].includes(value.operation_id)
      || lookup && (value.request_id !== identifier || value.operation_id !== lookup.operation || value.target_key !== lookup.target)
      || !lookup && value.intent_id !== identifier) throw new Error('原事实请求缺少准确正文或身份。');
  const identity = { operation_id: value.operation_id, target_key: value.target_key, request_id: value.request_id, schema_version: '2.0.0', payload: value.fact_input } as FactIntentInput;
  checkIdentity(identity);
  return { request: value, identity };
}
export async function retryFact(request: RequestView, signal: AbortSignal) {
  if (!request.may_retry || !request.generation) throw new Error('当前原请求没有重新准备资格。');
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(`/api/v2/write-intents/${request.intent_id}/retry`,
    { method: 'POST', signal, body: { expected_generation: request.generation } }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || BigInt(value.generation) !== BigInt(request.generation) + 1n) throw new Error('事实重试与原请求不一致。');
  return value;
}
