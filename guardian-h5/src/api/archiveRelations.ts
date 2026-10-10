import isEqual from 'fast-deep-equal';
import type { IntentView } from '../../../frontend-design/types';
import type { RelationView, WriteReceipt } from '../../../frontend-design/v2-types';
import type { RelationDirectory, RelationIdentity, RelationRequestView } from '../../../frontend-design/archive-relations-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'archive-relations.schema.json#/$defs/';
const inputs = { archive_relation_create: 'RelationInput', archive_relation_update: 'RelationChangeInput', archive_relation_verify: 'RelationVerificationInput' };

function identityCheck(identity: RelationIdentity) {
  validateApiValue(`${schema}RelationIntentInput`, identity);
  validateApiValue(`${schema}${inputs[identity.operation_id]}`, identity.payload);
  const pattern = identity.operation_id === 'archive_relation_create' ? /^\/patients\/([0-9a-f-]{36})\/archive-relations$/
    : identity.operation_id === 'archive_relation_update' ? /^\/archive-relations\/([0-9a-f-]{36})\/relationships$/
      : /^\/support\/archive-relations\/([0-9a-f-]{36})\/verification$/;
  const found = pattern.exec(identity.target_key);
  if (!found) throw new Error('资料关系操作与准确提交目标不一致。');
  validateApiValue(`${schema}Uuid`, found[1]);
}

export async function readRelationDirectory(patientId: string, cursor: string | null, signal?: AbortSignal): Promise<RelationDirectory> {
  validateApiValue(`${schema}Uuid`, patientId);
  const query = new URLSearchParams(cursor ? { cursor } : {});
  const result = validateApiValue<RelationDirectory>(`${schema}RelationDirectory`, await fetchJson<unknown>(
    `/api/v2/patients/${encodeURIComponent(patientId)}/archive-relations/search${query.size ? `?${query}` : ''}`, { signal }));
  if (result.patient_id !== patientId) throw new Error('关系目录与当前患儿不一致。');
  const sources = new Map(result.sources.map(value => [value.id, value]));
  for (const candidate of result.candidates) {
    if (candidate.status !== 'proposed' || sources.get(candidate.left_id)?.kind !== candidate.source_kind
      || sources.get(candidate.right_id)?.kind !== candidate.source_kind) throw new Error('候选关系缺少准确来源。');
  }
  for (const relation of result.relations) if (relation.patient_id !== patientId) throw new Error('关系记录包含其他患儿。');
  return result;
}

export async function readArchiveRelation(relationId: string, patientId: string, signal?: AbortSignal, version?: string): Promise<RelationView> {
  validateApiValue(`${schema}Uuid`, relationId);
  if (version !== undefined) validateApiValue(`${schema}Version`, version);
  const result = validateApiValue<RelationView>('v2-protocol.schema.json#/$defs/RelationView', await fetchJson<unknown>(
    `/api/v2/archive-relations/${encodeURIComponent(relationId)}${version ? `?version=${encodeURIComponent(version)}` : ''}`, { signal }));
  if (result.relation_id !== relationId || result.patient_id !== patientId || version && result.version !== version)
    throw new Error('资料关系身份或历史版本不一致。');
  return result;
}

export async function prepareRelationRequest(identity: RelationIdentity, signal?: AbortSignal): Promise<IntentView> {
  identityCheck(identity);
  const result = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    '/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (result.request_id !== identity.request_id) throw new Error('关系意图与原请求不一致。');
  return result;
}

export async function submitRelationRequest(identity: RelationIdentity, intent: IntentView, signal?: AbortSignal): Promise<WriteReceipt> {
  identityCheck(identity);
  validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('关系提交身份与原意图不一致。');
  const receipt = validateApiValue<WriteReceipt>('v2-protocol.schema.json#/$defs/WriteReceipt', await fetchJson<unknown>(
    `/api/v2${identity.target_key}`, { method: 'POST', body: identity.payload, signal,
      idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation }));
  if (receipt.resource_type !== 'archive_relation' || receipt.request_id !== identity.request_id || receipt.task_id !== null)
    throw new Error('关系保存回执与原请求不一致。');
  if (identity.operation_id === 'archive_relation_create' ? receipt.version !== '1'
    : !('expected_version' in identity.payload) || BigInt(receipt.version) !== BigInt(identity.payload.expected_version) + 1n)
    throw new Error('关系保存版本与准确原版本不一致。');
  return receipt;
}

export async function readRelationRequest(identifier: string, signal?: AbortSignal,
  lookup?: { operation_id: RelationIdentity['operation_id']; target_key: string }): Promise<RelationRequestView> {
  validateApiValue(`${schema}Uuid`, identifier);
  const query = lookup ? new URLSearchParams({ lookup: 'request_id', ...lookup }) : null;
  const result = validateApiValue<RelationRequestView>(`${schema}RelationRequestView`, await fetchJson<unknown>(
    `/api/v2/requests/${encodeURIComponent(identifier)}${query ? `?${query}` : ''}`, { signal }));
  identityCheck({ operation_id: result.operation_id, target_key: result.target_key, request_id: result.request_id,
    schema_version: '2.0.0', payload: result.relation_input });
  if (lookup ? result.request_id !== identifier || result.operation_id !== lookup.operation_id || result.target_key !== lookup.target_key
    : result.intent_id !== identifier) throw new Error('查询结果与准确原请求不一致。');
  if (result.receipt && (result.receipt.request_id !== result.request_id || result.receipt.resource_type !== 'archive_relation'))
    throw new Error('原请求回执与关系保存结果不一致。');
  return result;
}

export async function retryRelationRequest(request: RelationRequestView, signal?: AbortSignal): Promise<IntentView> {
  if (!request.may_retry || request.receipt) throw new Error('原关系请求尚不具备重新提交条件。');
  const result = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    `/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, { method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (result.intent_id !== request.intent_id || result.request_id !== request.request_id
    || BigInt(result.generation) !== BigInt(request.generation) + 1n) throw new Error('关系重试与原请求版本不一致。');
  return result;
}

export function relationPayloadMatches(saved: RelationView, payload: RelationIdentity['payload']): boolean {
  return Object.entries(payload).every(([key, value]) => key === 'expected_version' || isEqual(saved[key as keyof RelationView], value));
}
