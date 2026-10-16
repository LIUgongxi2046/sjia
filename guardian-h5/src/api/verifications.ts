import type { IntentView } from '../../../frontend-design/types';
import type { RequestView, VerificationCreationQuery, VerificationCreationScopeView, VerificationIntentInput, VerificationOptionsView, VerificationRequestLookup, VerificationResourceView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';
import { validateOriginalSource } from './originalSourceSchema';

const schema = 'v2-protocol.schema.json#/$defs/';

export function verificationOriginalPath(resource: VerificationResourceView) {
  validateApiValue(schema + 'VerificationResourceView', resource);
  return `/api/v2/support/verification-resources/${resource.resource_type}/${encodeURIComponent(resource.resource_id)}/originals`;
}

export async function readVerificationOriginals(resource: VerificationResourceView, signal?: AbortSignal) {
  const value = validateOriginalSource(await fetchJson<unknown>(verificationOriginalPath(resource), { signal }));
  if (value.patient_id !== resource.patient_id || value.record_id !== resource.record_id || value.record_version_id !== resource.record_version_id)
    throw new Error('原件来源与准确核对版本不一致。');
  return value;
}

export async function readVerificationCreationScope(versionId: string, purpose: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', versionId);
  if (!purpose.trim()) throw new Error('需要填写准确资料用途。');
  const value = validateApiValue<VerificationCreationScopeView>(schema + 'VerificationCreationScopeView',
    await fetchJson<unknown>(`/api/v2/support/record-versions/${encodeURIComponent(versionId)}/verification-scope?${new URLSearchParams({ purpose } satisfies VerificationCreationQuery)}`, { signal }));
  if (value.record_version_id !== versionId || value.purpose !== purpose
      || new Set(value.fields.map(field => field.field_id)).size !== value.fields.length
      || new Set(value.assignees.map(item => item.principal_id)).size !== value.assignees.length
      || value.assignees.some(item => new Set(item.field_ids).size !== item.field_ids.length
        || item.field_ids.some(id => !value.fields.some(field => field.field_id === id))))
    throw new Error('核对创建范围与准确资料不一致。');
  return value;
}

export function verificationTarget(identity: VerificationIntentInput) {
  validateApiValue(schema + 'VerificationIntentInput', identity);
  if (identity.operation_id === 'verification_v2_request_create' && identity.target_key === '/support/verification-requests'
      || identity.operation_id === 'verification_v2_submit' && identity.target_key === '/support/verifications') return;
  const match = /^\/support\/verifications\/([0-9a-f-]{36})\/revoke$/u.exec(identity.target_key);
  if (identity.operation_id !== 'verification_v2_revoke' || !match) throw new Error('专业核对目标无效。');
  validateApiValue(schema + 'Uuid', match[1]);
}

function receiptFor(identity: VerificationIntentInput, value: unknown) {
  const receipt = validateApiValue<WriteReceipt>(schema + 'WriteReceipt', value);
  const kind = identity.operation_id === 'verification_v2_request_create' ? 'verification_request' : 'verification';
  if (receipt.resource_type !== kind || receipt.request_id !== identity.request_id || receipt.version !== '1' || receipt.task_id !== null)
    throw new Error('专业核对保存回执与原请求不一致。');
  if (identity.operation_id === 'verification_v2_revoke' && identity.target_key !== `/support/verifications/${receipt.resource_id}/revoke`)
    throw new Error('核对撤销回执与准确证明不一致。');
  return receipt;
}

export async function prepareVerification(identity: VerificationIntentInput, signal?: AbortSignal) {
  verificationTarget(identity);
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (intent.request_id !== identity.request_id) throw new Error('专业核对意图与原请求不一致。');
  return intent;
}

export async function submitVerification(identity: VerificationIntentInput, intent: IntentView, signal?: AbortSignal) {
  verificationTarget(identity); validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('专业核对提交身份不一致。');
  return receiptFor(identity, await fetchJson<unknown>(`/api/v2${identity.target_key}`, { method: 'POST', body: identity.payload,
    signal, idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation }));
}

export async function readVerificationResource(kind: 'verification_request' | 'verification', id: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', id);
  const path = kind === 'verification_request' ? 'verification-requests' : 'verifications';
  const value = validateApiValue<VerificationResourceView>(schema + 'VerificationResourceView',
    await fetchJson<unknown>(`/api/v2/support/${path}/${encodeURIComponent(id)}`, { signal }));
  if (value.resource_type !== kind || value.resource_id !== id || new Set(value.fields.map(field => field.field_id)).size !== value.fields.length)
    throw new Error('专业核对读取与准确对象不一致。');
  if (kind === 'verification' && (value.results!.length !== value.fields.length
      || new Set(value.results!.map(item => item.field_id)).size !== value.results!.length
      || value.results!.some(item => !value.fields.some(field => field.field_id === item.field_id))))
    throw new Error('专业核对结果与完整字段范围不一致。');
  return value;
}

export async function readVerificationOptions(request: VerificationResourceView, signal?: AbortSignal) {
  if (request.resource_type !== 'verification_request') throw new Error('核对选项需要准确请求。');
  const value = validateApiValue<VerificationOptionsView>(schema + 'VerificationOptionsView',
    await fetchJson<unknown>(`/api/v2/support/verification-requests/${encodeURIComponent(request.resource_id)}/options`, { signal }));
  if (value.verification_request_id !== request.resource_id || value.record_version_id !== request.record_version_id
      || value.patient_id !== request.patient_id || value.purpose !== request.purpose
      || new Set(value.policies.map(item => item.policy_id)).size !== value.policies.length
      || new Set(value.evidence.map(item => item.source_revision_id)).size !== value.evidence.length
      || value.evidence.some(item => item.allowed_policy_ids.some(id => !value.policies.some(policy => policy.policy_id === id))))
    throw new Error('核对选项与准确请求范围不一致。');
  return value;
}

function checkedRequest(value: unknown) {
  const request = validateApiValue<RequestView>(schema + 'RequestView', value);
  if (!request.operation_id.startsWith('verification_v2_') || !request.verification_input || !request.generation
      || !request.payload_sha256 || !request.expires_at) throw new Error('专业核对原请求身份或正文不可读取。');
  const identity: VerificationIntentInput = { operation_id: request.operation_id as VerificationIntentInput['operation_id'],
    target_key: request.target_key, request_id: request.request_id, schema_version: '2.0.0', payload: request.verification_input };
  verificationTarget(identity);
  if (request.receipt) receiptFor(identity, request.receipt);
  if ((request.state === 'committed') !== (request.receipt !== null)) throw new Error('专业核对提交状态与保存回执不一致。');
  return request;
}

export async function readVerificationRequest(intentId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', intentId);
  const request = checkedRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(intentId)}`, { signal }));
  if (request.intent_id !== intentId) throw new Error('专业核对恢复与原意图不一致。');
  return request;
}

export async function findVerificationRequest(identity: Pick<VerificationIntentInput, 'request_id' | 'operation_id' | 'target_key'>, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', identity.request_id);
  const query = { lookup: 'request_id', operation_id: identity.operation_id, target_key: identity.target_key } satisfies VerificationRequestLookup;
  validateApiValue(schema + 'VerificationRequestLookup', query);
  const request = checkedRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(identity.request_id)}?${new URLSearchParams(query)}`, { signal }));
  if (request.request_id !== identity.request_id || request.operation_id !== identity.operation_id || request.target_key !== identity.target_key)
    throw new Error('专业核对恢复与原请求范围不一致。');
  return request;
}

export async function retryVerificationRequest(request: RequestView, signal?: AbortSignal) {
  checkedRequest(request);
  if (!request.may_retry || !request.generation) throw new Error('当前专业核对请求不具备重试条件。');
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>(`/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, {
      method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (intent.intent_id !== request.intent_id || intent.request_id !== request.request_id
      || BigInt(intent.generation) !== BigInt(request.generation) + 1n) throw new Error('专业核对重试与原意图不一致。');
  return intent;
}
