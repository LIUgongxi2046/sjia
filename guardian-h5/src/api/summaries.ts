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
export interface SummaryTrendPoint {
  field_id: string; record_version_id: string; source_definition_revision_id: string | null;
  plot_at: string | null; raw_value: string | null; raw_unit: string | null; plottable: boolean; reason_codes: string[];
}
export interface SummaryTrendSeries {
  concept_code: string | null; field_code: string; points: SummaryTrendPoint[];
  segments: Array<{ from_field_id: string; to_field_id: string; connect: boolean; reason_codes: string[] }>;
}
export interface SummaryTrendProjection {
  schema_version: 'summary-raw-trend-1'; snapshot_sha256: string; clinical_interpretation: false; series: SummaryTrendSeries[];
}
export interface SummarySnapshot {
  record_id: string; record_version_id: string; kind: SummaryKind | 'professional'; origin_type: string;
  source_organization: string | null; quality_status: string; field: RecordFieldView;
  confirmation_snapshot_state: 'recorded' | 'not_recorded';
  source_anchor_snapshot_state: 'recorded' | 'not_recorded';
  source_definition_revision_id?: string | null;
}
export interface SummaryView extends SummaryListItem {
  trend_projection?: SummaryTrendProjection | null;
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
export interface SummaryReceipt { resource_type: 'summary' | 'task'; resource_id: string; version: '1'; saved_at: string; request_id: string }
export interface SummaryRequest {
  task?: SummaryTask | null;
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
  verifySummaryTrendBinding(summary);
  return summary;
}

export function verifySummaryTrendBinding(summary: SummaryView) {
  const projection = summary.trend_projection;
  if (!projection) return;
  if ((summary.validity !== 'valid' || summary.needs_regeneration) && projection.series.length) throw new Error('失效摘要不能显示趋势。');
  const byField = new Map(summary.items.map(item => [item.field_id, item]));
  const seen = new Set<string>();
  for (const series of projection.series) {
    const ids = new Set<string>();
    for (const point of series.points) {
      const item = byField.get(point.field_id);
      if (!item || seen.has(point.field_id) || point.record_version_id !== item.record_version_id
        || point.raw_value !== item.snapshot.field.raw_numeric || point.raw_unit !== item.snapshot.field.raw_unit
        || series.field_code !== item.snapshot.field.field_code || series.concept_code !== item.snapshot.field.concept_code
        || point.source_definition_revision_id !== (item.snapshot.source_definition_revision_id ?? null)) throw new Error('摘要趋势与保存的依据版本不一致。');
      const medical = item.snapshot.field.medical_time;
      if (point.plot_at !== null && (!medical?.instant || !Number.isFinite(Date.parse(point.plot_at))
        || Date.parse(point.plot_at) !== Date.parse(medical.instant) || medical.resolution_status !== 'resolved'
        || !['minute', 'second'].includes(medical.precision))) throw new Error('摘要趋势时间与原医学时间不一致。');
      if (point.plottable && (point.plot_at === null || point.raw_value === null || !point.raw_unit
        || item.snapshot.field.value_state !== 'known' || item.snapshot.field.qualifier !== 'eq' || point.reason_codes.length)) throw new Error('摘要趋势点缺少有效原值或医学时间。');
      seen.add(point.field_id); ids.add(point.field_id);
    }
    for (const segment of series.segments) if (!ids.has(segment.from_field_id) || !ids.has(segment.to_field_id)
      || segment.from_field_id === segment.to_field_id || (segment.connect && segment.reason_codes.length)) throw new Error('摘要趋势连线依据无效。');
  }
}

export async function readSummaryTask(taskId: string, signal: AbortSignal): Promise<SummaryTask> {
  const task = await checked<SummaryTask>(`/api/tasks/${taskId}`, 'SummaryTaskView', { signal });
  if (task.task_id !== taskId) throw new Error('摘要任务与当前查询不一致。');
  return task;
}

export function cancelSummaryTask(taskId: string, csrfToken: string, signal: AbortSignal) {
  return fetchJson(`/api/tasks/${taskId}/cancel`, { method: 'POST', csrfToken, signal, body: {} });
}

export async function waitForSummaryTask(initial: SummaryTask, requestId: string, signal: AbortSignal,
  onState: (task: SummaryTask) => void): Promise<SummaryTask> {
  let task = initial;
  const until = Date.now() + 6 * 60_000;
  for (;;) {
    if (signal.aborted) throw new DOMException('查询已暂停', 'AbortError');
    if (task.request_id !== requestId) throw new Error('摘要任务与原请求不一致。');
    onState(task);
    if (!['accepted', 'queued', 'running'].includes(task.status)) return task;
    if (Date.now() >= until) throw new Error('摘要仍在处理中，请稍后查询原请求，不要重复生成。');
    await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException('查询已暂停', 'AbortError')); };
      const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); },
        Math.max(250, Math.min(task.poll_after_ms ?? 1500, 5000)));
      signal.addEventListener('abort', abort, { once: true });
      if (signal.aborted) abort();
    });
    const next = await readSummaryTask(task.task_id, signal);
    if (BigInt(next.status_version) < BigInt(task.status_version)) throw new Error('摘要任务状态版本发生倒退，请重新查询。');
    task = next;
  }
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
