// 家庭端后端接口：会话、自建登录、首页、预警、字段配置、记录与摘要。
import { fetchJson, getSessionCsrfToken } from './client';
import type { RecordSearchPage } from './records';
import type { HomeView as FormalHomeView } from '../../../frontend-design/types';

export interface SessionView {
  principal_id: string;
  account_status: string;
  expires_at: string;
  audience: string;
  capabilities: string[];
  csrf_token: string;
  selected_patient_id: string | null;
}

export interface AuthBootstrap {
  csrf_token: string;
  return_paths: string[];
  schema_version: string;
}

export type HomeView = FormalHomeView;

export interface AlertItem {
  alert_id: string;
  created_at: string;
  validity: string;
  channel: string;
  read_at: string | null;
}

export interface AlertDetailView {
  assessed_at: string | null;
  alert_id: string;
  created_at: string;
  validity: string;
  read_at: string | null;
  content_state: string;
  content: {
    content_id: string;
    content_version: string;
    approved_text: string;
    source_refs: Array<{ record_version_id: string; field_id: string; source_revision_id: string; source_version: string }>;
    contact: { state: string; contact_name?: string; channel?: string; channel_value?: string; valid_until?: string };
  } | null;
}

export interface AlertStats {
  total: number;
  unread: number;
}

export interface FieldDefinition {
  field_code: string;
  label: string;
  value_type: string;
  writable: boolean;
  allowed_values: string[];
}

export interface FieldConfigView {
  patient_id: string;
  kind: string;
  config_version: string | null;
  checksum: string | null;
  state: string;
  reason_code: string | null;
  fields: FieldDefinition[];
  watch_field_codes: string[];
}

export interface RelationItem {
  relation_id: string;
  patient_id: string;
  display_id: string;
  status: string;
  version: string;
  valid_from: string;
  valid_until: string | null;
  actions: string[];
}

export interface RelationView extends RelationItem { auth_epoch: string }
export interface ApplicationItem {
  application_id: string;
  version: string;
  status: 'pending' | 'approved' | 'rejected' | 'withdrawn';
  patient_id: string | null;
  reason: string | null;
  note: string | null;
  requested_access: 'read' | 'read_write' | 'review_decides' | null;
}
export interface ApplicationView extends ApplicationItem { evidence_ref: string; created_at: string }

export function bootstrap(): Promise<AuthBootstrap> {
  return fetchJson<AuthBootstrap>('/api/auth/bootstrap');
}

export function readSession(): Promise<SessionView> {
  return fetchJson<SessionView>('/api/session');
}

export async function savePatientSelection(patientId: string): Promise<void> {
  const saved = await fetchJson<{ selected_patient_id: string }>('/api/patient-selection', {
    method: 'POST', body: { patient_id: patientId },
  });
  if (saved.selected_patient_id !== patientId) throw new Error('保存的患儿选择与当前操作不一致。');
}

export function login(username: string, password: string, csrfToken?: string): Promise<SessionView> {
  return fetchJson<SessionView>('/api/auth/login', { method: 'POST', body: { username, password }, csrfToken });
}

export function register(username: string, password: string, csrfToken?: string): Promise<SessionView> {
  return fetchJson<SessionView>('/api/auth/register', { method: 'POST', body: { username, password }, csrfToken });
}

export function logout(csrfToken?: string): Promise<Record<string, never>> {
  return fetchJson<Record<string, never>>('/api/session/logout', { method: 'POST', body: {}, csrfToken });
}

export async function listRelations(signal?: AbortSignal): Promise<{ relations: RelationItem[] }> {
  const { validateRelationRead } = await import('./relationReadSchema');
  return validateRelationRead('RelationList', await fetchJson<unknown>('/api/relations', { signal }));
}

export async function relationRead(relationId: string, signal?: AbortSignal): Promise<RelationView> {
  const { validateRelationRead } = await import('./relationReadSchema');
  const result = validateRelationRead<RelationView>('RelationView', await fetchJson<unknown>(`/api/relations/${relationId}`, { signal }));
  if (result.relation_id !== relationId) throw new Error('服务返回资料与当前关联对象不一致。');
  return result;
}

export function readHome(patientId: string): Promise<HomeView> {
  return fetchJson<HomeView>(`/api/patients/${patientId}/home`);
}

export function listAlerts(patientId: string): Promise<{ alerts: AlertItem[] }> {
  return fetchJson<{ alerts: AlertItem[] }>(`/api/patients/${patientId}/alerts`);
}

export function readAlertStats(patientId: string): Promise<AlertStats> {
  return fetchJson<AlertStats>(`/api/patients/${patientId}/alerts/stats`);
}

export async function markAlertRead(alertId: string, csrfToken?: string, signal?: AbortSignal): Promise<{ alert_id: string; read_at: string | null }> {
  const { validateFamilyRead } = await import('./familyReadSchema');
  const value = await fetchJson<unknown>(`/api/alerts/${alertId}/read`, { method: 'POST', body: {}, csrfToken, signal });
  return validateFamilyRead('AlertReadReceipt', value);
}

export function readFieldConfig(patientId: string, kind: string): Promise<FieldConfigView> {
  return fetchJson<FieldConfigView>(`/api/patients/${patientId}/field-config?kind=${encodeURIComponent(kind)}`);
}

const pendingRecordRequests = new Map<string, string>();
export async function createRecord(patientId: string, payload: Record<string, unknown>, csrfToken?: string): Promise<Record<string, unknown>> {
  const target = `/api/patients/${patientId}/records`;
  const key = `${target}:${JSON.stringify(payload)}`;
  let requestId = pendingRecordRequests.get(key);
  if (!requestId) {
    requestId = crypto.randomUUID();
    pendingRecordRequests.set(key, requestId);
  }
  const intent = await fetchJson<{ intent_id: string; generation: string }>('/api/write-intents', { method: 'POST', body: { operation_id: 'record_create', target_key: target, request_id: requestId, payload, schema_version: 'back-json-1' }, csrfToken });
  const result = await fetchJson<Record<string, unknown>>(target, { method: 'POST', body: payload, csrfToken, writeIntent: intent.intent_id, intentGeneration: intent.generation, idempotencyKey: requestId });
  pendingRecordRequests.delete(key);
  return result;
}

export function clearPendingRecordRequests() { pendingRecordRequests.clear(); }

export async function readRecord(recordId: string, signal?: AbortSignal): Promise<import('./records').RecordView> {
  const value = await fetchJson<unknown>(`/api/records/${recordId}`, { signal });
  const { validateRecordRead } = await import('./recordSearchSchema');
  return validateRecordRead('RecordView', value);
}

export interface RecordHistoryView {
  patient_id: string;
  record_id: string;
  current_version: string;
  versions: Array<{ record_version_id: string; version: string; origin_type: string; recorded_at: string; quality_status: string }>;
}

export async function readRecordVersion(versionId: string, signal?: AbortSignal): Promise<import('./records').RecordView> {
  const value = await fetchJson<unknown>(`/api/record-versions/${versionId}`, { signal });
  const { validateRecordRead } = await import('./recordSearchSchema');
  return validateRecordRead('RecordView', value);
}

export async function recordHistory(recordId: string, signal?: AbortSignal): Promise<RecordHistoryView> {
  const value = await fetchJson<unknown>(`/api/records/${recordId}/history`, { signal });
  const { validateRecordRead } = await import('./recordSearchSchema');
  return validateRecordRead('RecordHistoryView', value);
}

export interface RecordCorrectionReceipt { patient_id: string; record_id: string; record_version_id: string; version: number }
export async function correctRecord(patientId: string, recordId: string, reason: string, fields: Array<Record<string, unknown>>, expectedVersion: number, csrfToken?: string, draft: { draft_id?: string; draft_version?: string } = {}, signal?: AbortSignal, familyConfirmation?: true): Promise<RecordCorrectionReceipt> {
  const { validateCorrectionValue } = await import('./recordSearchSchema');
  const body = { reason, fields, expected_version: expectedVersion, ...(draft.draft_id === undefined ? {} : { draft_id: draft.draft_id }), ...(draft.draft_version === undefined ? {} : { draft_version: draft.draft_version }), ...(familyConfirmation === true ? { family_confirmation: true } : {}) };
  validateCorrectionValue('RecordCorrectionInput', body);
  const target = `/api/records/${encodeURIComponent(recordId)}/corrections`;
  const requestId = crypto.randomUUID();
  const intent = familyConfirmation === true ? await fetchJson<{ intent_id: string; generation: string }>('/api/write-intents', {
    method: 'POST', body: { operation_id: 'record_correct', target_key: target, request_id: requestId, payload: body, schema_version: 'back-json-1' }, csrfToken, signal }) : null;
  const value = await fetchJson<unknown>(target, { method: 'POST', body, csrfToken, signal,
    ...(intent ? { writeIntent: intent.intent_id, intentGeneration: intent.generation, idempotencyKey: requestId } : {}) });
  const result = validateCorrectionValue<RecordCorrectionReceipt>('RecordCorrectionReceipt', value);
  if (result.patient_id !== patientId || result.record_id !== recordId || result.version !== expectedVersion + 1) throw new Error('更正回执与本次患儿、记录或原版本不一致。');
  return result;
}

export interface VoiceCandidates {
  temperature: number | null;
  temperature_unit: string | null;
  symptoms: string[];
  observed_time: string | null;
  note: string | null;
}

export function structureVoice(text: string, csrfToken?: string, signal?: AbortSignal): Promise<{ candidates: VoiceCandidates }> {
  return fetchJson<{ candidates: VoiceCandidates }>('/api/recognition/voice-structure', { method: 'POST', body: { text }, csrfToken, signal });
}

export interface ReportCandidate {
  name: string;
  value: string | null;
  comparator: '=' | '<' | '<=' | '≤' | '>' | '>=' | '≥' | null;
  unit: string | null;
  reference: string | null;
  method: string | null;
  source_text: string;
}

export async function recognizeReport(fileId: string, csrfToken?: string): Promise<{ candidates: { items: ReportCandidate[] } }> {
  const result = await fetchJson<unknown>('/api/recognition/report-ocr', { method: 'POST', body: { file_id: fileId }, csrfToken, signal: AbortSignal.timeout(130000) });
  const { validateApiValue } = await import('./schema');
  return validateApiValue('report-recognition.schema.json', result);
}

export interface TherapyCandidate { name: string; source_text: string }
export async function recognizeTherapy(fileId: string, csrfToken?: string): Promise<{ candidates: { items: TherapyCandidate[] } }> {
  const result = await fetchJson<unknown>('/api/recognition/therapy-ocr', { method: 'POST', body: { file_id: fileId }, csrfToken, signal: AbortSignal.timeout(130000) });
  const { validateApiValue } = await import('./schema');
  return validateApiValue('therapy-recognition.schema.json', result);
}

export function createFileGroup(patientId: string, csrfToken?: string, authorizationId?: string, clientGroupId?: string): Promise<{ group_id: string; version: string; status: string }> {
  return fetchJson<{ group_id: string; version: string; status: string }>(`/api/patients/${patientId}/uploads`, { method: 'POST', body: {
    ...(authorizationId ? { authorization_id: authorizationId } : {}), ...(clientGroupId ? { client_group_id: clientGroupId } : {}) }, csrfToken });
}

export function completeFileGroup(groupId: string, csrfToken?: string): Promise<{ group_id: string; status: string }> {
  return fetchJson(`/api/uploads/${groupId}/complete`, { method: 'POST', body: {}, csrfToken });
}

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export async function uploadFile(groupId: string, uploadId: string, data: ArrayBuffer, mediaType: string): Promise<{ file_id: string; checksum: string; size_bytes: number }> {
  const checksum = await sha256Hex(data);
  const url = `/api/uploads/${groupId}/files/${uploadId}?checksum=${checksum}&size=${data.byteLength}&media_type=${encodeURIComponent(mediaType)}`;
  const response = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/octet-stream', 'X-CSRFToken': getSessionCsrfToken() ?? '' },
    body: data,
    credentials: 'same-origin',
  });
  if (!response.ok) {
    let message = `上传失败（${response.status}）`;
    try {
      const body = (await response.json()) as { message?: string };
      if (body.message) message = body.message;
    } catch {
      // 保持默认错误信息
    }
    throw new Error(message);
  }
  return (await response.json()) as { file_id: string; checksum: string; size_bytes: number };
}

export async function searchRecords(patientId: string, payload: Record<string, unknown>, options: { signal?: AbortSignal; csrfToken?: string } = {}): Promise<RecordSearchPage> {
  const { validateRecordSearchInput, validateRecordSearchPage } = await import('./recordSearchSchema');
  validateRecordSearchInput(payload);
  const value = await fetchJson<unknown>(`/api/patients/${patientId}/records/search`, { method: 'POST', body: payload, ...options });
  return validateRecordSearchPage(value);
}

export interface AnalyteTrend {
  patient_id: string;
  concept_code: string;
  trend: Array<{ field_id: string; record_version_id: string; original_name: string | null; value_state: 'known' | 'unknown' | 'invalid'; raw_numeric: string | null; raw_text: string | null; qualifier: 'eq' | 'lt' | 'le' | 'gt' | 'ge' | null; raw_unit: string | null; method: string | null; direction: 'up' | 'down' | 'same' | null; not_comparable_reason: 'unit_or_method_or_precision_mismatch' | null; comparison_reasons: string[]; origin_type: import('./records').RecordView['origin_type']; source_organization: string | null; verification_states: import('./records').RecordFieldView['verification_states']; medical_time_id: string | null; medical_time: import('./records').MedicalTimeView | null }>;
}

export async function searchAnalyte(patientId: string, conceptCode: string, options: { signal?: AbortSignal } = {}): Promise<AnalyteTrend> {
  const value = await fetchJson<unknown>(`/api/patients/${encodeURIComponent(patientId)}/analytes/${encodeURIComponent(conceptCode)}/search`, { method: 'POST', body: {}, ...options });
  const { validateAnalyteRead } = await import('./recordSearchSchema');
  const result = validateAnalyteRead(value);
  if (result.patient_id !== patientId || result.concept_code !== conceptCode) throw new Error('历次检验所属患儿或项目与本次请求不一致，本次内容未被采用。');
  return result;
}

export interface DraftView {
  draft_id: string;
  form_type: string;
  version: string;
  saved_at: string;
  status: string;
  payload: Record<string, unknown>;
  patient_id: string;
}

export function readDraft(draftId: string, signal?: AbortSignal): Promise<DraftView> {
  return fetchJson<DraftView>(`/api/drafts/${draftId}`, { signal });
}

export function listDrafts(patientId: string): Promise<{ drafts: DraftView[] }> {
  return fetchJson<{ drafts: DraftView[] }>(`/api/patients/${patientId}/drafts`);
}

export function createDraft(patientId: string, formType: string, payload: Record<string, unknown>, csrfToken?: string): Promise<DraftView> {
  return fetchJson<DraftView>(`/api/patients/${patientId}/drafts`, { method: 'POST', body: { form_type: formType, payload }, csrfToken });
}

export function updateDraft(draftId: string, expectedVersion: string, payload: Record<string, unknown>, csrfToken?: string): Promise<DraftView> {
  return fetchJson<DraftView>(`/api/drafts/${draftId}`, { method: 'PUT', body: { expected_version: expectedVersion, payload }, csrfToken });
}

export async function abandonDraft(draftId: string, expectedVersion: string, csrfToken?: string, signal?: AbortSignal): Promise<DraftView> {
  const value = await fetchJson<unknown>(`/api/drafts/${draftId}/abandon`, { method: 'POST', body: { expected_version: expectedVersion }, csrfToken, signal });
  const { validateFamilyRead } = await import('./familyReadSchema');
  return validateFamilyRead<DraftView>('DraftView', value);
}
