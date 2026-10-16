import { fetchJson } from './client';
import type { ApplicationView } from './endpoints';
import type { ApplicationDeclaration, ApplicationInput, ApplicationAction, RequestView } from '../../../frontend-design/types';

export type { ApplicationDeclaration, ApplicationInput, ApplicationAction };
export function sameApplicationDeclaration(expected: ApplicationDeclaration | null | undefined, actual: ApplicationDeclaration | null | undefined) {
  if (!expected || !actual) return !expected && !actual;
  return expected.schema_version === actual.schema_version && expected.declared_patient_name === actual.declared_patient_name
    && expected.declared_relationship === actual.declared_relationship && expected.guardian_scope_acknowledged === actual.guardian_scope_acknowledged;
}
export interface ApplicationReceipt { resource_type: 'relation_application'; resource_id: string; version: string; saved_at: string; request_id: string }
export interface ApplicationIntent { intent_id: string; request_id: string; generation: string; status: 'prepared' | 'committed' | 'failed'; expires_at: string; payload_sha256: string }
export interface ApplicationRequest extends RequestView {
  intent_id: string; request_id: string; operation_id: 'application_create' | 'application_withdraw'; target_key: string;
  prepared_at: string; state: 'pending' | 'committed' | 'failed'; may_retry: boolean; receipt: ApplicationReceipt | null;
  error_code: string | null; generation: string; expires_at: string; payload_sha256: string;
  application_input: ApplicationInput | ApplicationAction | null;
}

export function applicationWithdrawalTarget(applicationId: string) {
  return `/api/relation-applications/${applicationId}/withdraw`;
}

async function checked<T>(path: string, schema: Parameters<typeof import('./relationReadSchema').validateRelationRead>[0],
  options: Parameters<typeof fetchJson>[1]): Promise<T> {
  const { validateRelationRead } = await import('./relationReadSchema');
  return validateRelationRead<T>(schema, await fetchJson<unknown>(path, options));
}

export async function prepareApplication(requestId: string, payload: ApplicationInput, csrfToken: string, signal: AbortSignal): Promise<ApplicationIntent> {
  const { validateRelationRead } = await import('./relationReadSchema');
  validateRelationRead('ApplicationInput', payload);
  const result = await checked<ApplicationIntent>('/api/write-intents', 'IntentView', { method: 'POST', csrfToken, signal,
    body: { request_id: requestId, operation_id: 'application_create', target_key: '/api/relation-applications', schema_version: '1.0.0', payload } });
  if (result.request_id !== requestId) throw new Error('服务返回的保存意图与当前申请请求不一致。');
  return result;
}

export async function submitApplication(intent: ApplicationIntent, payload: ApplicationInput, csrfToken: string, signal: AbortSignal): Promise<ApplicationReceipt> {
  const result = await checked<ApplicationReceipt>('/api/relation-applications', 'WriteReceipt', { method: 'POST', csrfToken, signal,
    body: payload, writeIntent: intent.intent_id, idempotencyKey: intent.request_id, intentGeneration: intent.generation });
  if (result.request_id !== intent.request_id || result.version !== '1') throw new Error('服务保存回执与当前申请创建请求不一致。');
  return result;
}

export async function prepareApplicationWithdrawal(requestId: string, applicationId: string, payload: ApplicationAction,
  csrfToken: string, signal: AbortSignal): Promise<ApplicationIntent> {
  const { validateRelationRead } = await import('./relationReadSchema');
  validateRelationRead('ApplicationAction', payload);
  const result = await checked<ApplicationIntent>('/api/write-intents', 'IntentView', { method: 'POST', csrfToken, signal,
    body: { request_id: requestId, operation_id: 'application_withdraw', target_key: applicationWithdrawalTarget(applicationId), schema_version: '1.0.0', payload } });
  if (result.request_id !== requestId) throw new Error('服务返回的保存意图与当前撤回请求不一致。');
  return result;
}

export async function submitApplicationWithdrawal(intent: ApplicationIntent, applicationId: string, payload: ApplicationAction,
  csrfToken: string, signal: AbortSignal): Promise<ApplicationReceipt> {
  const { validateRelationRead } = await import('./relationReadSchema');
  validateRelationRead('ApplicationAction', payload);
  const result = await checked<ApplicationReceipt>(applicationWithdrawalTarget(applicationId), 'WriteReceipt', { method: 'POST', csrfToken, signal,
    body: payload, writeIntent: intent.intent_id, idempotencyKey: intent.request_id, intentGeneration: intent.generation });
  if (result.resource_id !== applicationId || result.request_id !== intent.request_id
    || BigInt(result.version) !== BigInt(payload.expected_version) + 1n) throw new Error('服务保存回执与当前撤回对象、请求或版本不一致。');
  return result;
}

export async function readApplication(applicationId: string, signal: AbortSignal): Promise<ApplicationView> {
  const result = await checked<ApplicationView>(`/api/relation-applications/${applicationId}`, 'ApplicationView', { signal });
  if (result.application_id !== applicationId) throw new Error('服务返回了其他申请对象。');
  return result;
}

export async function readApplicationRequest(intentId: string, signal: AbortSignal): Promise<ApplicationRequest> {
  const result = await checked<ApplicationRequest>(`/api/requests/${intentId}`, 'ApplicationRequestView', { signal });
  if (result.intent_id !== intentId) throw new Error('服务返回了其他申请请求。');
  return result;
}

export async function retryApplication(request: ApplicationRequest, csrfToken: string, signal: AbortSignal): Promise<ApplicationIntent> {
  const result = await checked<ApplicationIntent>(`/api/write-intents/${request.intent_id}/retry`, 'IntentView', {
    method: 'POST', csrfToken, signal, body: { expected_generation: request.generation } });
  if (result.intent_id !== request.intent_id || result.request_id !== request.request_id
    || BigInt(result.generation) !== BigInt(request.generation) + 1n || result.status !== 'prepared') throw new Error('服务返回的申请重试意图与当前请求不一致。');
  return result;
}
