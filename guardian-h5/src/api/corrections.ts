import { fetchJson } from './client';
import type { IntentInput, IntentView, RequestView, WriteReceipt } from '../../../frontend-design/types';
import { validateApiValue } from './schema';

export interface CorrectionInput { record_version_id: string; field_ids: string[]; reason: string; proposed_text: string | null }
export interface CorrectionResolution { expected_version: string; decision: 'resolved' | 'rejected'; reason: string; resolution_record_version_id: string | null }
export interface CorrectionView { id: string; version: string; status: 'pending' | 'in_review' | 'resolved' | 'rejected' | 'withdrawn' | 'superseded'; request: CorrectionInput; resolution_record_version_id: string | null }
export interface CorrectionField { field_id: string; field_code: string; concept_code: string | null; value_type: string; value_state: string; raw_text: string | null; raw_numeric: string | null; raw_unit: string | null; qualifier: string | null; categorical_value: string | null }
export interface CorrectionReview { correction: CorrectionView; patient_id: string; display_id: string; record_id: string; record_version: string; current_record_version: string; created_at: string; resolution_reason: string | null; resolved_at: string | null; fields: CorrectionField[] }
export interface CorrectionDetail extends CorrectionReview { resolution_options: { record_version_id: string; version: string; reason: string; fields: CorrectionField[] }[] }
export interface CorrectionQueue { items: CorrectionReview[]; next_cursor: string | null }
export type CorrectionOperation = 'correction_request_create' | 'correction_request_resolve';
export interface CorrectionIdentity extends IntentInput { operation_id: CorrectionOperation; payload: CorrectionInput | CorrectionResolution }
export interface CorrectionRequest extends RequestView { correction_input: CorrectionInput | CorrectionResolution }
const schema = 'protocol.schema.json#/$defs/';
export const checkCorrection = <T>(name: string, value: unknown) => validateApiValue<T>(schema + name, value);
function checkIdentity(identity: CorrectionIdentity) {
  checkCorrection('IntentInput', identity);
  checkCorrection(identity.operation_id === 'correction_request_create' ? 'CorrectionRequestInput' : 'CorrectionResolution', identity.payload);
  const pattern = identity.operation_id === 'correction_request_create' ? /^\/api\/records\/([0-9a-f-]+)\/correction-requests$/ : /^\/api\/support\/correction-requests\/([0-9a-f-]+)\/resolve$/;
  const match = pattern.exec(identity.target_key);
  if (!match) throw new Error('更正请求的操作与目标不一致。');
  checkCorrection('Uuid', match[1]);
}
export async function prepareCorrection(identity: CorrectionIdentity, signal: AbortSignal) {
  checkIdentity(identity);
  const result = checkCorrection<IntentView>('IntentView', await fetchJson('/api/write-intents', { method: 'POST', body: identity, signal }));
  if (result.request_id !== identity.request_id) throw new Error('更正提交意图与原请求不一致。');
  return result;
}
export async function submitCorrection(identity: CorrectionIdentity, intent: IntentView, signal: AbortSignal) {
  checkIdentity(identity);
  if (intent.request_id !== identity.request_id) throw new Error('更正意图身份不一致。');
  const receipt = checkCorrection<WriteReceipt>('WriteReceipt', await fetchJson(identity.target_key, { method: 'POST', body: identity.payload,
    idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation, signal }));
  if (receipt.request_id !== identity.request_id || receipt.resource_type !== 'correction_request'
    || identity.operation_id === 'correction_request_resolve' && receipt.resource_id !== identity.target_key.split('/')[4]) throw new Error('更正回执与原请求不一致。');
  return receipt;
}
export async function queryCorrection(key: Pick<CorrectionIdentity, 'operation_id' | 'target_key' | 'request_id'>, intentId: string | null, signal: AbortSignal) {
  checkCorrection('Uuid', key.request_id);
  const path = intentId ? `/api/requests/${intentId}` : `/api/requests/${key.request_id}?${new URLSearchParams({ lookup: 'request_id', operation_id: key.operation_id, target_key: key.target_key })}`;
  const value = checkCorrection<CorrectionRequest>('RequestView', await fetchJson(path, { signal }));
  if (value.request_id !== key.request_id || value.operation_id !== key.operation_id || value.target_key !== key.target_key || !value.correction_input
    || intentId && value.intent_id !== intentId || value.receipt && (value.receipt.request_id !== key.request_id || value.receipt.resource_type !== 'correction_request')) throw new Error('查询结果与原更正请求不一致。');
  return value;
}
export async function retryCorrection(request: CorrectionRequest, signal: AbortSignal) {
  if (!request.may_retry || !request.generation) throw new Error('当前请求不可重试。');
  return checkCorrection<IntentView>('IntentView', await fetchJson(`/api/write-intents/${request.intent_id}/retry`, {
    method: 'POST', body: { expected_generation: request.generation }, signal }));
}
export async function readCorrection(id: string, signal: AbortSignal) {
  checkCorrection('Uuid', id);
  const result = checkCorrection<CorrectionView>('CorrectionRequestView', await fetchJson(`/api/correction-requests/${id}`, { signal }));
  if (result.id !== id) throw new Error('更正申请与指定身份不一致。');
  return result;
}
export async function readCorrectionQueue(cursor: string | null, signal: AbortSignal) {
  return checkCorrection<CorrectionQueue>('CorrectionReviewQueue', await fetchJson('/api/support/correction-requests' + (cursor ? `?${new URLSearchParams({ cursor })}` : ''), { signal }));
}
export async function readCorrectionReview(id: string, signal: AbortSignal) {
  checkCorrection('Uuid', id);
  const value = checkCorrection<CorrectionDetail>('CorrectionReviewDetail', await fetchJson(`/api/support/correction-requests/${id}`, { signal }));
  if (value.correction.id !== id) throw new Error('专业复核资料与指定申请不一致。');
  return value;
}
