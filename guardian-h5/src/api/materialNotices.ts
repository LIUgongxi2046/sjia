import type { ReminderTarget, ReminderView } from '../../../frontend-design/v2-types';
import type { NotificationReceipt } from './notifications';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

export interface MaterialNotice {
  notice_id: string; patient_id: string; reminder_id: string; reminder_version: string;
  target_type: ReminderTarget; target_id: string; target_state: ReminderView['target_state'];
  status: 'pending' | 'accepted' | 'delivered' | 'failed' | 'uncertain' | 'cancelled'; channel: 'in_app';
  scheduled_at: string; created_at: string; delivered_at: string | null; read_at: string | null;
  receipts: NotificationReceipt[]; current_setting: boolean; snapshot_version: string;
}
export interface MaterialNoticeHistory { items: MaterialNotice[]; next_cursor: string | null; snapshot_version: string }
export interface MaterialNoticeMessage {
  notice_id: string; reminder_id: string; patient_id: string; approved_text: string; deep_link: string;
  content_id: string; content_version: string; delivery_ticket: string; expires_at: string;
}
const schema = 'material-reminder-notice.schema.json#/$defs/';
const uuid = (value: string) => validateApiValue('protocol.schema.json#/$defs/Uuid', value);
function checkedNotice(value: unknown) {
  const result = validateApiValue<MaterialNotice>(schema + 'Notice', value);
  if (new Set(result.receipts.map(row => row.receipt_id)).size !== result.receipts.length
      || new Set(result.receipts.map(row => row.status)).size !== result.receipts.length
      || ['accepted', 'delivered'].includes(result.status) && !result.receipts.some(row => row.status === 'accepted')
      || result.status === 'delivered' && (!result.delivered_at || !result.receipts.some(row => row.status === 'delivered'))
      || result.read_at !== null && result.delivered_at === null) throw new Error('资料提醒的受理、送达或阅读证明不一致。');
  return result;
}
export async function searchMaterialNotices(patientId: string, reminderId?: string, cursor?: string, signal?: AbortSignal) {
  uuid(patientId); if (reminderId) uuid(reminderId);
  const query = { ...(reminderId ? { reminder_id: reminderId } : {}), ...(cursor ? { cursor } : {}) };
  validateApiValue(schema + 'Query', query);
  const value = validateApiValue<MaterialNoticeHistory>(schema + 'History', await fetchJson<unknown>(
    `/api/v2/patients/${patientId}/material-reminder-notices/search`, { method: 'POST', body: query, signal }));
  if (value.items.some(row => row.patient_id !== patientId || reminderId && row.reminder_id !== reminderId)
      || new Set(value.items.map(row => row.notice_id)).size !== value.items.length) throw new Error('资料提醒通知目录的本人目标不一致。');
  value.items.forEach(checkedNotice);
  return value;
}
export async function readMaterialNotice(noticeId: string, patientId: string, signal?: AbortSignal) {
  uuid(noticeId); uuid(patientId);
  const value = checkedNotice(await fetchJson<unknown>(`/api/v2/material-reminder-notices/${noticeId}`, { signal }));
  if (value.notice_id !== noticeId || value.patient_id !== patientId) throw new Error('资料提醒通知身份与当前患儿不一致。');
  return value;
}
export async function readMaterialNoticeMessage(notice: MaterialNotice, signal?: AbortSignal) {
  checkedNotice(notice);
  const value = validateApiValue<MaterialNoticeMessage>(schema + 'Message', await fetchJson<unknown>(
    `/api/v2/material-reminder-notices/${notice.notice_id}/message`, { signal }));
  if (value.notice_id !== notice.notice_id || value.patient_id !== notice.patient_id || value.reminder_id !== notice.reminder_id
      || value.deep_link !== `/account/reminders?reminder=${notice.reminder_id}`) throw new Error('资料提醒正文与当前本人目标不一致。');
  return value;
}
export async function deliverMaterialNotice(message: MaterialNoticeMessage, signal?: AbortSignal) {
  validateApiValue(schema + 'Message', message);
  const value = checkedNotice(await fetchJson<unknown>(`/api/v2/material-reminder-notices/${message.notice_id}/delivered`, {
    method: 'POST', body: { delivery_ticket: message.delivery_ticket }, signal }));
  if (value.notice_id !== message.notice_id || value.patient_id !== message.patient_id || value.reminder_id !== message.reminder_id
      || value.status !== 'delivered' || value.delivered_at === null) throw new Error('资料提醒送达回执与原通知不一致。');
  return value;
}
export async function markMaterialNoticeRead(notice: MaterialNotice, signal?: AbortSignal) {
  checkedNotice(notice);
  const value = checkedNotice(await fetchJson<unknown>(`/api/v2/material-reminder-notices/${notice.notice_id}/read`,
    { method: 'POST', body: {}, signal }));
  if (value.notice_id !== notice.notice_id || value.patient_id !== notice.patient_id || value.read_at === null)
    throw new Error('资料提醒的阅读结果与原通知不一致。');
  return value;
}
export async function retryMaterialNotice(notice: MaterialNotice) {
  checkedNotice(notice);
  const value = checkedNotice(await fetchJson<unknown>(`/api/v2/material-reminder-notices/${notice.notice_id}/retry`,
    { method: 'POST', body: {} }));
  if (value.notice_id !== notice.notice_id || value.patient_id !== notice.patient_id || value.reminder_version !== notice.reminder_version)
    throw new Error('资料提醒再次核验与原通知不一致。');
  return value;
}
