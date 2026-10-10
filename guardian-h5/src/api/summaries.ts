import { fetchJson } from './client';
import type { RecordFieldView } from './records';
import type { ApplicationIntent } from './application';
import type { SummaryInput } from '../../../frontend-design/types';

export type { SummaryInput };
export type SummaryKind = SummaryInput['record_kinds'][number];
export interface SummaryListItem {
  summary_id: string; patient_id: string; task_id: string; from_date: string; to_date: string;
  validity: 'valid' | 'invalid'; generated_at: string; needs_regeneration: boolean;
}
export interface SummarySnapshot {
  record_id: string; record_version_id: string; kind: SummaryKind | 'professional'; origin_type: string;
  source_organization: string | null; quality_status: string; field: RecordFieldView;
  confirmation_snapshot_state: 'recorded' | 'not_recorded';
  source_anchor_snapshot_state: 'recorded' | 'not_recorded';
}
export interface SummaryView extends SummaryListItem {
  display_id: string; scope_timezone: string; include_unknown_time: boolean; record_kinds: SummaryKind[] | null;
  include_source_status: boolean | null; environment: string | null;
  template_id: string; visibility_policy_id: string; template_checksum: string | null; visibility_checksum: string | null;
  items: Array<{ record_version_id: string; field_id: string; ordinal: number; snapshot: SummarySnapshot }>;
}
export interface SummaryTask {
  task_id: string; task_type: 'summary'; request_id: string | null; accepted_at: string; status: string;
  status_version: string; poll_after_ms: number | null; allowed_actions: string[];
  result_type: 'summary' | null; result_ref: string | null; error_code: string | null;
}
export interface SummaryReceipt { resource_type: 'summary'; resource_id: string; version: '1'; saved_at: string; request_id: string }
export interface SummaryRequest {
  patient_id: string; intent_id: string; request_id: string; operation_id: 'summary_create'; target_key: string;
  prepared_at: string; state: 'pending' | 'committed' | 'failed'; may_retry: boolean; receipt: SummaryReceipt | null;
  error_code: string | null; generation: string; expires_at: string; payload_sha256: string; summary_input: SummaryInput | null;
}
export interface SummaryList { patient_id: string; summaries: SummaryListItem[] }
export interface SummaryRequestList { patient_id: string; requests: SummaryRequest[] }
export type SummaryIntent = ApplicationIntent;
export type SummarySchema = 'SummaryInput' | 'SummaryList' | 'SummaryView' | 'SummaryTaskView' | 'SummaryRequestView' | 'SummaryRequestList';

async function checked<T>(path: string, schema: SummarySchema | 'IntentView', options: Parameters<typeof fetchJson>[1]): Promise<T> {
  const { validateSummary } = await import('./summarySchema');
  return validateSummary<T>(schema, await fetchJson<unknown>(path, options));
}

export function summaryTarget(patientId: string) { return `/api/patients/${patientId}/summaries`; }

export async function prepareSummary(patientId: string, requestId: string, payload: SummaryInput, csrfToken: string, signal: AbortSignal): Promise<SummaryIntent> {
  const { validateSummary } = await import('./summarySchema');
  validateSummary('SummaryInput', payload);
  const result = await checked<SummaryIntent>('/api/write-intents', 'IntentView', { method: 'POST', csrfToken, signal,
    body: { request_id: requestId, operation_id: 'summary_create', target_key: summaryTarget(patientId), schema_version: '1.0.0', payload } });
  if (result.request_id !== requestId) throw new Error('服务返回了其他摘要请求的意图。');
  return result;
}

export async function submitSummary(patientId: string, intent: SummaryIntent, payload: SummaryInput, csrfToken: string, signal: AbortSignal): Promise<SummaryTask> {
  const task = await checked<SummaryTask>(summaryTarget(patientId), 'SummaryTaskView', { method: 'POST', csrfToken, signal,
    body: payload, writeIntent: intent.intent_id, intentGeneration: intent.generation, idempotencyKey: intent.request_id });
  if (task.request_id !== intent.request_id) throw new Error('服务返回的摘要任务与原请求不一致。');
  return task;
}

export async function readSummary(patientId: string, summaryId: string, signal: AbortSignal): Promise<SummaryView> {
  const summary = await checked<SummaryView>(`/api/summaries/${summaryId}`, 'SummaryView', { signal });
  if (summary.patient_id !== patientId || summary.summary_id !== summaryId) throw new Error('摘要所属患儿或对象与当前请求不一致。');
  for (const item of summary.items) if (item.field_id !== item.snapshot.field.field_id || item.record_version_id !== item.snapshot.record_version_id) {
    throw new Error('摘要项目与保存的来源版本不一致。');
  }
  return summary;
}

export async function readSummaryTask(taskId: string, signal: AbortSignal): Promise<SummaryTask> {
  const task = await checked<SummaryTask>(`/api/tasks/${taskId}`, 'SummaryTaskView', { signal });
  if (task.task_id !== taskId) throw new Error('摘要任务与当前查询不一致。');
  return task;
}

export async function readSummaryRequest(patientId: string, intentId: string, signal: AbortSignal): Promise<SummaryRequest> {
  const result = await checked<SummaryRequest>(`/api/requests/${intentId}`, 'SummaryRequestView', { signal });
  if (result.intent_id !== intentId || result.patient_id !== patientId || result.target_key !== summaryTarget(patientId)) throw new Error('摘要原请求与当前患儿不一致。');
  return result;
}

export async function retrySummary(request: SummaryRequest, csrfToken: string, signal: AbortSignal): Promise<SummaryIntent> {
  const intent = await checked<SummaryIntent>(`/api/write-intents/${request.intent_id}/retry`, 'IntentView', {
    method: 'POST', csrfToken, signal, body: { expected_generation: request.generation } });
  if (intent.intent_id !== request.intent_id || intent.request_id !== request.request_id
    || BigInt(intent.generation) !== BigInt(request.generation) + 1n || intent.status !== 'prepared') throw new Error('服务返回的摘要续期意图与原请求不一致。');
  return intent;
}

export function verifySummaryScope(summary: SummaryView, input: SummaryInput) {
  if (summary.from_date !== input.from_date || summary.to_date !== input.to_date || summary.scope_timezone !== input.scope_timezone
    || summary.include_unknown_time !== input.include_unknown_time || summary.include_source_status !== input.include_source_status
    || summary.record_kinds === null || summary.record_kinds.length !== input.record_kinds.length
    || summary.record_kinds.some(kind => !input.record_kinds.includes(kind))) throw new Error('重新读取的摘要范围与原请求不一致。');
  if (summary.items.some(item => item.snapshot.kind !== 'professional' && !input.record_kinds.includes(item.snapshot.kind))) throw new Error('摘要含有未选择的资料类别。');
}
