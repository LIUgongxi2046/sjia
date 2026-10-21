import { fetchJson, setSessionCsrfToken } from './client';
import { validateApiValue } from './schema';
import type { IntentView } from '../../../frontend-design/types';
import type { DispositionApprovalInput, DispositionExecuteInput, DispositionRetainInput, DispositionRetryInput, DispositionView, Purpose, PurposeCopyEvidenceView, PurposeCopyInput, PurposeCopyOptionsView, PurposeCopyView, PurposeProviderCopyInput, PurposeReviewInput, PurposeRevokeInput, SupportLoginInput, SupportSessionView, TaskView, WriteReceipt } from '../../../frontend-design/v2-types';

export type PurposeOperation = 'purpose_copy_create' | 'purpose_copy_review' | 'purpose_copy_revoke' | 'simulation_provider_copy_create' | 'disposition_approve' | 'disposition_execute' | 'disposition_retain' | 'disposition_retry';
export interface PurposeRequestView {
  intent_id: string; request_id: string; operation_id: PurposeOperation; target_key: string; prepared_at: string;
  state: 'pending' | 'processing' | 'committed' | 'failed' | 'unknown' | 'not_found';
  may_retry: boolean; receipt: WriteReceipt | null; error_code: string | null;
}

export interface PurposeRequestIdentity {
  requestId: string; operation: PurposeOperation; target: string; payload: PurposeCopyInput | PurposeReviewInput | PurposeRevokeInput | PurposeProviderCopyInput | DispositionApprovalInput | DispositionExecuteInput | DispositionRetainInput | DispositionRetryInput;
}

async function checkedRequest<T>(path: string, schema: string, options: Parameters<typeof fetchJson>[1]): Promise<T> {
  const value = await fetchJson<unknown>(path, options);
  return validateApiValue<T>(schema, value);
}

export async function loginSupport(payload: SupportLoginInput, signal?: AbortSignal): Promise<SupportSessionView> {
  validateApiValue('v2-protocol.schema.json#/$defs/SupportLoginInput', payload);
  const session = await checkedRequest<SupportSessionView>('/api/v2/support/session/login',
    'v2-protocol.schema.json#/$defs/SupportSessionView', { method: 'POST', body: payload, signal, sessionControl: true });
  setSessionCsrfToken(session.csrf_token);
  return session;
}

export function readSupportSession(signal?: AbortSignal): Promise<SupportSessionView> {
  return checkedRequest('/api/v2/support/session', 'v2-protocol.schema.json#/$defs/SupportSessionView', { signal });
}

export function readPurposeCopy(copyId: string, signal?: AbortSignal): Promise<PurposeCopyView> {
  return checkedRequest(`/api/v2/support/purpose-copies/${encodeURIComponent(copyId)}`,
    'v2-protocol.schema.json#/$defs/PurposeCopyView', { signal });
}

export function readPurposeOptions(fileId: string, purpose: Purpose, signal?: AbortSignal): Promise<PurposeCopyOptionsView> {
  return checkedRequest(`/api/v2/support/files/${encodeURIComponent(fileId)}/purposes/${encodeURIComponent(purpose)}/options`,
    'v2-protocol.schema.json#/$defs/PurposeCopyOptionsView', { signal });
}

export function readPurposeTask(taskId: string, signal?: AbortSignal): Promise<TaskView> {
  return checkedRequest(`/api/v2/tasks/${encodeURIComponent(taskId)}`, 'v2-protocol.schema.json#/$defs/TaskView', { signal });
}

export function readSupportDisposition(id: string, signal?: AbortSignal): Promise<DispositionView> {
  return checkedRequest(`/api/v2/support/dispositions/${encodeURIComponent(id)}`, 'v2-protocol.schema.json#/$defs/DispositionView', { signal });
}

export function readPurposeEvidence(copyId: string, signal?: AbortSignal): Promise<PurposeCopyEvidenceView> {
  return checkedRequest(`/api/v2/support/purpose-copies/${encodeURIComponent(copyId)}/evidence`,
    'v2-protocol.schema.json#/$defs/PurposeCopyEvidenceView', { signal });
}

export function preparePurposeRequest(identity: PurposeRequestIdentity, signal?: AbortSignal): Promise<IntentView> {
  return checkedRequest('/api/v2/write-intents', 'protocol.schema.json#/$defs/IntentView', {
    method: 'POST', signal, body: { request_id: identity.requestId, operation_id: identity.operation,
      target_key: identity.target, schema_version: '2.0.0', payload: identity.payload },
  });
}

export function submitPurposeRequest(identity: PurposeRequestIdentity, intent: IntentView, signal?: AbortSignal): Promise<WriteReceipt> {
  if (intent.request_id !== identity.requestId) throw new Error('提交身份与当前意图不一致。');
  if (!identity.target.startsWith('/support/')) throw new Error('资料提交目标无效。');
  return checkedRequest(`/api/v2${identity.target}`, 'v2-protocol.schema.json#/$defs/WriteReceipt', {
    method: 'POST', signal, body: identity.payload, idempotencyKey: identity.requestId,
    writeIntent: intent.intent_id, intentGeneration: intent.generation,
  });
}

export function readPurposeRequest(intentId: string, signal?: AbortSignal): Promise<PurposeRequestView> {
  return checkedRequest(`/api/v2/requests/${encodeURIComponent(intentId)}`, 'v2-protocol.schema.json#/$defs/RequestView', { signal });
}

export function retryPurposeRequest(intent: IntentView, signal?: AbortSignal): Promise<IntentView> {
  return checkedRequest(`/api/v2/write-intents/${encodeURIComponent(intent.intent_id)}/retry`, 'protocol.schema.json#/$defs/IntentView', {
    method: 'POST', signal, body: { expected_generation: intent.generation },
  });
}
