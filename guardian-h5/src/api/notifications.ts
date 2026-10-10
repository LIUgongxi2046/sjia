import { fetchJson } from './client';
import { compileApiSchemas, validateApiValue } from './schema';
import type { IntentView } from '../../../frontend-design/types';

export interface NotificationReceipt { receipt_id: string; provider: string; provider_event_id: string;
  status: 'accepted' | 'delivered' | 'failed'; occurred_at: string; received_at: string; evidence_sha256: string }
export interface NotificationAttempt { attempt_id: string; attempt_number: number; permit_id: string; authorized_at: string; expires_at: string;
  status: 'authorized' | 'started' | 'accepted' | 'failed' | 'uncertain' | 'cancelled'; provider_request_id: string | null;
  started_at: string | null; finished_at: string | null; proof_state: 'available' | 'unavailable'; receipts: NotificationReceipt[] }
export interface NotificationRecord { recipient_id: string; patient_id: string; alert_id: string; relation_id: string; channel: string; created_at: string;
  delivery_status: 'not_sent' | 'pending' | 'accepted' | 'delivered' | 'failed' | 'uncertain' | 'cancelled' | 'unknown'; read_at: string | null;
  content_state: 'available' | 'configuration_pending' | 'unavailable'; current_validity: 'valid' | 'invalid'; trace_state: 'available' | 'unavailable'; attempts: NotificationAttempt[]; snapshot_version: string }
export interface NotificationHistory { patient_id: string; items: NotificationRecord[]; next_cursor: string | null; snapshot_version: string;
  capability_state: 'available' | 'unavailable'; capability_reason: string | null }
export interface NotificationCases { items: { patient_id: string; display_id: string }[]; next_cursor: string | null;
  snapshot_version: string; capability_state: 'available' | 'unavailable'; capability_reason: string | null }
export interface NotificationIdentity { operation_id: 'notification_v2_send'; schema_version: '2.0.0'; target_key: string; request_id: string; payload: { expected_snapshot: string } }
export interface NotificationRequest { intent_id: string; request_id: string; operation_id: 'notification_v2_send'; target_key: string;
  state: 'pending' | 'committed' | 'failed' | 'unknown'; generation: string; expires_at: string; payload_sha256: string; may_retry: boolean;
  error_code: string | null; notification_input: { expected_snapshot: string }; receipt: { resource_type: 'notification_attempt'; resource_id: string; version: string; saved_at: string; request_id: string; recipient_id: string } | null }
export interface NotificationMessage { recipient_id: string; patient_id: string; alert_id: string; attempt_id: string;
  body: { approved_text: string; deep_link: string; content_id: string; content_version: string }; delivery_ticket: string; expires_at: string }
const schema = 'notification.schema.json#/$defs/';
const protocol = 'protocol.schema.json#/$defs/';

export function checkNotificationSchemas() { return compileApiSchemas(['Receipt', 'Attempt', 'Record', 'History', 'CaseDirectory', 'SendInput', 'IntentInput', 'RequestReceipt', 'Request', 'Message', 'Acknowledgement', 'AlertRecord'].map(name => schema + name)); }
function checkedRecord(value: unknown) {
  const record = validateApiValue<NotificationRecord>(schema + 'Record', value);
  if (new Set(record.attempts.map(item => item.attempt_id)).size !== record.attempts.length
      || new Set(record.attempts.map(item => item.attempt_number)).size !== record.attempts.length
      || record.attempts.some(item => new Set(item.receipts.map(row => row.receipt_id)).size !== item.receipts.length))
    throw new Error('通知尝试或回执身份不一致。');
  if (['accepted', 'delivered'].includes(record.delivery_status) && (record.trace_state !== 'available'
      || !record.attempts.length || record.attempts.some(item => item.proof_state !== 'available')))
    throw new Error('通知受理或送达状态缺少准确证明。');
  return record;
}
function target(id: string) { validateApiValue(protocol + 'Uuid', id); return `/support/notification-records/${id}/send`; }
function checkedRequest(value: unknown) {
  const request = validateApiValue<NotificationRequest>(schema + 'Request', value);
  if ((request.state === 'committed') !== (request.receipt !== null) || request.receipt &&
      (request.receipt.request_id !== request.request_id || target(request.receipt.recipient_id) !== request.target_key))
    throw new Error('通知提交状态与原请求证明不一致。');
  return request;
}
export function notificationIdentity(record: NotificationRecord): NotificationIdentity {
  checkedRecord(record);
  return { operation_id: 'notification_v2_send', schema_version: '2.0.0', target_key: target(record.recipient_id),
    request_id: crypto.randomUUID(), payload: { expected_snapshot: record.snapshot_version } };
}
export async function prepareNotification(identity: NotificationIdentity) {
  validateApiValue(schema + 'IntentInput', identity);
  const value = validateApiValue<IntentView>(protocol + 'IntentView', await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity }));
  if (value.request_id !== identity.request_id) throw new Error('通知意图与原请求不一致。');
  return value;
}
export async function submitNotification(identity: NotificationIdentity, intent: IntentView) {
  validateApiValue(schema + 'IntentInput', identity);
  if (intent.request_id !== identity.request_id || intent.status !== 'prepared') throw new Error('通知提交身份不一致。');
  return checkedRecord(await fetchJson<unknown>('/api/v2' + identity.target_key, { method: 'POST', body: identity.payload,
    writeIntent: intent.intent_id, idempotencyKey: identity.request_id, intentGeneration: intent.generation }));
}
export async function findNotificationRequest(requestId: string, recipientId: string, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', requestId);
  const query = { lookup: 'request_id', operation_id: 'notification_v2_send', target_key: target(recipientId) };
  const value = checkedRequest(await fetchJson<unknown>(`/api/v2/requests/${requestId}?${new URLSearchParams(query)}`, { signal }));
  if (value.request_id !== requestId || value.target_key !== query.target_key) throw new Error('通知结果与原请求不一致。');
  return value;
}
export async function retryNotification(request: NotificationRequest) {
  checkedRequest(request);
  if (!request.may_retry || request.state === 'unknown' || request.state === 'committed') throw new Error('当前通知请求不能重试。');
  const value = validateApiValue<IntentView>(protocol + 'IntentView', await fetchJson<unknown>(`/api/v2/write-intents/${request.intent_id}/retry`,
    { method: 'POST', body: { expected_generation: request.generation } }));
  if (value.request_id !== request.request_id || value.intent_id !== request.intent_id || BigInt(value.generation) !== BigInt(request.generation) + 1n)
    throw new Error('通知重试身份不一致。');
  return value;
}
export async function cancelNotificationRequest(request: NotificationRequest, reason: string) {
  checkedRequest(request);
  if (request.state !== 'pending' || !reason.trim()) throw new Error('取消未提交意图需要当前版本与完整理由。');
  const value = checkedRequest(await fetchJson<unknown>(`/api/v2/support/notification-intents/${request.intent_id}/cancel`,
    { method: 'POST', body: { expected_generation: request.generation, reason } }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || value.state !== 'failed'
      || value.error_code !== 'NOTIFICATION_INTENT_CANCELLED' || value.receipt !== null || value.may_retry)
    throw new Error('通知意图取消状态与原请求不一致。');
  return value;
}
export async function readAlertNotification(alertId: string, patientId: string, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', alertId); validateApiValue(protocol + 'Uuid', patientId);
  const value = validateApiValue<{ patient_id: string; alert_id: string; record: NotificationRecord | null }>(schema + 'AlertRecord',
    await fetchJson<unknown>(`/api/v2/alerts/${alertId}/notification`, { signal }));
  if (value.patient_id !== patientId || value.alert_id !== alertId || value.record && (value.record.alert_id !== alertId || value.record.patient_id !== patientId))
    throw new Error('通知资料与当前提示身份不一致。');
  if (value.record) checkedRecord(value.record);
  return value.record;
}
export async function readNotificationMessage(record: NotificationRecord, signal?: AbortSignal) {
  checkedRecord(record);
  const value = validateApiValue<NotificationMessage>(schema + 'Message', await fetchJson<unknown>(`/api/v2/notification-records/${record.recipient_id}/message`, { signal }));
  if (value.recipient_id !== record.recipient_id || value.patient_id !== record.patient_id || value.alert_id !== record.alert_id
      || value.body.deep_link !== `/alerts/${record.alert_id}` || !record.attempts.some(row => row.attempt_id === value.attempt_id && row.proof_state === 'available'))
    throw new Error('站内正文与准确通知身份不一致。');
  return value;
}
export async function confirmNotificationDelivery(message: NotificationMessage, signal?: AbortSignal) {
  validateApiValue(schema + 'Message', message);
  const body = { delivery_ticket: message.delivery_ticket }; validateApiValue(schema + 'Acknowledgement', body);
  const value = checkedRecord(await fetchJson<unknown>(`/api/v2/notification-records/${message.recipient_id}/delivered`, { method: 'POST', body, signal }));
  if (value.recipient_id !== message.recipient_id || value.alert_id !== message.alert_id || value.delivery_status !== 'delivered')
    throw new Error('当前送达回执尚未保存。');
  return value;
}
export async function readNotificationCases(cursor?: string, signal?: AbortSignal) {
  const value = validateApiValue<NotificationCases>(schema + 'CaseDirectory', await fetchJson<unknown>(
    `/api/v2/support/notification-scopes${cursor ? `?${new URLSearchParams({ cursor })}` : ''}`, { signal }));
  if (new Set(value.items.map(item => item.patient_id)).size !== value.items.length) throw new Error('通知资料范围包含重复身份。');
  return value;
}
export async function readNotificationHistory(patientId: string, support: boolean, cursor?: string, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', patientId);
  const value = validateApiValue<NotificationHistory>(schema + 'History', await fetchJson<unknown>(
    `/api/v2/${support ? 'support/' : ''}patients/${encodeURIComponent(patientId)}/notification-records${cursor ? `?${new URLSearchParams({ cursor })}` : ''}`, { signal }));
  if (value.patient_id !== patientId || new Set(value.items.map(item => item.recipient_id)).size !== value.items.length
      || value.items.some(item => item.patient_id !== patientId)) throw new Error('通知记录与准确患儿范围不一致。');
  value.items.forEach(checkedRecord);
  return value;
}
export async function readNotificationRecord(id: string, patientId: string, support: boolean, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', id); validateApiValue(protocol + 'Uuid', patientId);
  const value = checkedRecord(await fetchJson<unknown>(`/api/v2/${support ? 'support/' : ''}notification-records/${encodeURIComponent(id)}`, { signal }));
  if (value.recipient_id !== id || value.patient_id !== patientId) throw new Error('通知详情与准确接收身份不一致。');
  return value;
}
