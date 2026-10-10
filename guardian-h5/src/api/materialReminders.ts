import { Temporal } from '@js-temporal/polyfill';
import type { IntentView } from '../../../frontend-design/types';
import type { ReminderChangeInput, ReminderInput, ReminderIntentInput, ReminderList, ReminderTargetList,
  ReminderTargetQuery, ReminderView, RequestView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';
export { reminderClock, reminderInstant, reminderLocal } from './reminderTime';
export type { LocalTimeChoice } from './reminderTime';

async function checked<T>(path: string, schema: string, options: Parameters<typeof fetchJson>[1]): Promise<T> {
  return validateApiValue<T>(`v2-protocol.schema.json#/$defs/${schema}`, await fetchJson<unknown>(path, options));
}

function target(identity: ReminderIntentInput): string {
  validateApiValue('v2-protocol.schema.json#/$defs/ReminderIntentInput', identity);
  const pattern = identity.operation_id === 'material_reminder_create'
    ? /^\/patients\/([0-9a-f-]{36})\/material-reminders$/ : /^\/material-reminders\/([0-9a-f-]{36})\/settings$/;
  const matched = pattern.exec(identity.target_key);
  if (!matched) throw new Error('提醒提交目标与当前操作不一致。');
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', matched[1]);
  return identity.target_key;
}

export async function searchReminders(patientId: string, cursor?: string, signal?: AbortSignal): Promise<ReminderList> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', patientId);
  const value = await checked<ReminderList>(`/api/v2/patients/${patientId}/material-reminders/search`, 'ReminderList',
    { method: 'POST', body: cursor ? { cursor } : {}, signal });
  if (value.items.some(item => item.patient_id !== patientId) || new Set(value.items.map(item => item.reminder_id)).size !== value.items.length)
    throw new Error('提醒目录包含不一致的患儿或重复身份。');
  return value;
}

export async function searchReminderTargets(patientId: string, query: ReminderTargetQuery, signal?: AbortSignal): Promise<ReminderTargetList> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', patientId);
  validateApiValue('v2-protocol.schema.json#/$defs/ReminderTargetQuery', query);
  const value = await checked<ReminderTargetList>(`/api/v2/patients/${patientId}/material-reminder-targets/search`, 'ReminderTargetList',
    { method: 'POST', body: query, signal });
  if (value.items.some(item => item.patient_id !== patientId || item.target_type !== query.target_type)
      || new Set(value.items.map(item => item.target_id)).size !== value.items.length) throw new Error('提醒目标目录与当前患儿或类别不一致。');
  return value;
}

export async function readReminder(reminderId: string, patientId: string, signal?: AbortSignal): Promise<ReminderView> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', reminderId);
  const value = await checked<ReminderView>(`/api/v2/material-reminders/${reminderId}`, 'ReminderView', { signal });
  if (value.reminder_id !== reminderId || value.patient_id !== patientId) throw new Error('提醒设置与当前患儿或身份不一致。');
  return value;
}

export async function prepareReminderRequest(identity: ReminderIntentInput, signal?: AbortSignal): Promise<IntentView> {
  target(identity);
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (value.request_id !== identity.request_id) throw new Error('提醒保存意图与原请求不一致。');
  return value;
}

export async function submitReminderRequest(identity: ReminderIntentInput, intent: IntentView, signal?: AbortSignal): Promise<WriteReceipt> {
  const path = target(identity);
  validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('提醒保存身份与意图不一致。');
  const value = await checked<WriteReceipt>(`/api/v2${path}`, 'WriteReceipt', { method: 'POST', body: identity.payload,
    idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation, signal });
  if (value.resource_type !== 'material_reminder' || value.request_id !== identity.request_id || value.task_id !== null)
    throw new Error('提醒保存回执与原请求不一致。');
  if (identity.operation_id === 'material_reminder_create') {
    if (value.version !== '1') throw new Error('提醒创建没有返回准确初始版本。');
  } else {
    const input = validateApiValue<ReminderChangeInput>('v2-protocol.schema.json#/$defs/ReminderChangeInput', identity.payload);
    if (path !== `/material-reminders/${value.resource_id}/settings` || BigInt(value.version) !== BigInt(input.expected_version) + 1n)
      throw new Error('提醒保存回执与原版本不一致。');
  }
  return value;
}

function checkRequest(value: RequestView, intentId?: string): RequestView {
  if (intentId !== undefined && value.intent_id !== intentId || !value.reminder_input || !value.generation
      || !value.expires_at || !value.payload_sha256 || !['material_reminder_create', 'material_reminder_change'].includes(value.operation_id))
    throw new Error('提醒请求缺少准确身份或可恢复正文。');
  target({ operation_id: value.operation_id as ReminderIntentInput['operation_id'], target_key: value.target_key,
    request_id: value.request_id, schema_version: '2.0.0', payload: value.reminder_input });
  if (value.receipt && (value.receipt.resource_type !== 'material_reminder' || value.receipt.request_id !== value.request_id))
    throw new Error('提醒请求回执与原请求不一致。');
  return value;
}

export async function readReminderRequest(intentId: string, signal?: AbortSignal): Promise<RequestView> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', intentId);
  return checkRequest(await checked<RequestView>(`/api/v2/requests/${intentId}`, 'RequestView', { signal }), intentId);
}

export async function findReminderRequest(requestId: string, operationId: ReminderIntentInput['operation_id'], targetKey: string,
  signal?: AbortSignal): Promise<RequestView> {
  validateApiValue('v2-protocol.schema.json#/$defs/Uuid', requestId);
  const lookup = { lookup: 'request_id', operation_id: operationId, target_key: targetKey };
  validateApiValue('v2-protocol.schema.json#/$defs/ReminderRequestLookup', lookup);
  const value = checkRequest(await checked<RequestView>(`/api/v2/requests/${requestId}?${new URLSearchParams(lookup)}`, 'RequestView', { signal }));
  if (value.request_id !== requestId || value.operation_id !== operationId || value.target_key !== targetKey)
    throw new Error('提醒原请求查询与当前操作不一致。');
  return value;
}

export async function retryReminderRequest(request: RequestView, signal?: AbortSignal): Promise<IntentView> {
  if (!request.may_retry || !request.generation) throw new Error('当前提醒请求不具备重新提交条件。');
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    `/api/v2/write-intents/${request.intent_id}/retry`, { method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || BigInt(value.generation) !== BigInt(request.generation) + 1n)
    throw new Error('提醒重新提交意图与原请求版本不一致。');
  return value;
}


export async function cancelReminderRequest(request: RequestView): Promise<RequestView> {
  if (!request.generation || ['committed', 'unknown'].includes(request.state)) throw new Error('当前提醒原请求无法取消。');
  const value = checkRequest(await checked<RequestView>(`/api/v2/material-reminder-intents/${request.intent_id}/cancel`,
    'RequestView', { method: 'POST', body: { expected_generation: request.generation } }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || value.generation !== request.generation
      || value.state !== 'failed' || value.error_code !== 'REMINDER_INTENT_CANCELLED' || value.may_retry)
    throw new Error('提醒原请求的取消结果需要核验。');
  return value;
}

export function reminderPayloadMatches(value: ReminderView, input: ReminderInput | ReminderChangeInput): boolean {
  if ('action' in input && ['disable', 'pause'].includes(input.action)) return value.status === (input.action === 'pause' ? 'paused' : 'disabled');
  if ('target_id' in input && (value.target_id !== input.target_id || value.target_type !== input.target_type)) return false;
  if (input.due_at !== null && (value.due_at === null || !Temporal.Instant.from(input.due_at).equals(Temporal.Instant.from(value.due_at)))) return false;
  if ('action' in input && ['enable', 'snooze'].includes(input.action) && value.status !== 'enabled') return false;
  if (input.target_version !== undefined && input.target_version !== value.target_version) return false;
  if (input.timezone !== undefined && (value.timezone !== input.timezone || value.quiet_start !== input.quiet_start || value.quiet_end !== input.quiet_end)) return false;
  const enabled = 'target_id' in input ? input.enabled ?? false : input.enabled;
  return enabled === undefined || value.status === (enabled ? 'enabled' : 'disabled');
}
