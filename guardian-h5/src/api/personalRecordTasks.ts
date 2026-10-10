import type { IntentView } from '../../../frontend-design/types';
import type { PersonalTaskIdentity, PersonalTaskList, PersonalTaskOptions, PersonalTaskReceipt,
  PersonalTaskRequest, PersonalTaskView } from '../../../frontend-design/personal-record-task-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'personal-record-task.schema.json#/$defs/';
const uuid = (value: string) => validateApiValue('protocol.schema.json#/$defs/Uuid', value);
async function checked<T>(path: string, name: string, options: Parameters<typeof fetchJson>[1] = {}) {
  return validateApiValue<T>(schema + name, await fetchJson<unknown>(path, options));
}
function target(identity: PersonalTaskIdentity) {
  validateApiValue(schema + 'IntentInput', identity);
  const pattern = identity.operation_id === 'personal_record_task_create'
    ? /^\/patients\/([0-9a-f-]{36})\/personal-record-tasks$/ : /^\/personal-record-tasks\/([0-9a-f-]{36})\/status$/;
  const match = pattern.exec(identity.target_key);
  if (!match) throw new Error('本人记录任务的操作与提交目标不一致。');
  uuid(match[1]);
  return identity.target_key;
}
function checkRequest(value: PersonalTaskRequest) {
  target({ operation_id: value.operation_id, target_key: value.target_key, request_id: value.request_id,
    schema_version: '2.0.0', payload: value.record_task_input });
  if ((value.state === 'committed') !== (value.receipt !== null)
      || value.receipt !== null && value.receipt.request_id !== value.request_id
      || ['committed', 'unknown'].includes(value.state) && value.may_retry) throw new Error('本人记录任务的原请求结果不一致。');
  return value;
}
export async function searchPersonalTasks(patientId: string, cursor?: string, signal?: AbortSignal) {
  uuid(patientId);
  const value = await checked<PersonalTaskList>(`/api/v2/patients/${patientId}/personal-record-tasks/search`, 'List',
    { method: 'POST', body: cursor ? { cursor } : {}, signal });
  if (value.items.some(item => item.patient_id !== patientId) || new Set(value.items.map(item => item.task_id)).size !== value.items.length)
    throw new Error('本人记录任务目录的患儿或身份不一致。');
  return value;
}
export async function readPersonalTask(taskId: string, patientId: string, signal?: AbortSignal) {
  uuid(taskId); uuid(patientId);
  const value = await checked<PersonalTaskView>(`/api/v2/personal-record-tasks/${taskId}`, 'View', { signal });
  if (value.task_id !== taskId || value.patient_id !== patientId) throw new Error('本人记录任务与当前患儿不一致。');
  return value;
}
export async function personalTaskOptions(patientId: string, signal?: AbortSignal) {
  uuid(patientId);
  return checked<PersonalTaskOptions>(`/api/v2/patients/${patientId}/personal-record-task-retention`, 'RetentionOptions', { signal });
}
export async function preparePersonalTask(identity: PersonalTaskIdentity) {
  target(identity);
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity }));
  if (value.request_id !== identity.request_id) throw new Error('本人记录任务意图与原请求不一致。');
  return value;
}
export async function submitPersonalTask(identity: PersonalTaskIdentity, intent: IntentView) {
  const path = target(identity);
  if (intent.request_id !== identity.request_id) throw new Error('本人记录任务的提交身份不一致。');
  const value = await checked<PersonalTaskReceipt>(`/api/v2${path}`, 'Receipt', { method: 'POST', body: identity.payload,
    idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation });
  if (value.request_id !== identity.request_id) throw new Error('本人记录任务回执与原请求不一致。');
  return value;
}
export async function readPersonalTaskRequest(requestId: string, operation: PersonalTaskIdentity['operation_id'],
  path: string, signal?: AbortSignal) {
  uuid(requestId);
  const params = new URLSearchParams({ lookup: 'request_id', operation_id: operation, target_key: path });
  const value = checkRequest(await checked<PersonalTaskRequest>(`/api/v2/requests/${requestId}?${params}`, 'Request', { signal }));
  if (value.request_id !== requestId || value.operation_id !== operation || value.target_key !== path)
    throw new Error('本人记录任务的原请求查询与当前操作不一致。');
  return value;
}
export async function retryPersonalTask(request: PersonalTaskRequest) {
  if (!request.may_retry || ['committed', 'unknown'].includes(request.state)) throw new Error('本人记录任务原请求当前无法重新提交。');
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>(`/api/v2/write-intents/${request.intent_id}/retry`, { method: 'POST',
      body: { expected_generation: request.generation } }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id
      || BigInt(value.generation) !== BigInt(request.generation) + 1n) throw new Error('本人记录任务的重新提交版本不一致。');
  return value;
}
export async function cancelPersonalTaskRequest(request: PersonalTaskRequest) {
  if (['committed', 'unknown'].includes(request.state)) throw new Error('当前原请求无法取消。');
  const value = checkRequest(await checked<PersonalTaskRequest>(`/api/v2/personal-record-task-intents/${request.intent_id}/cancel`,
    'Request', { method: 'POST', body: { expected_generation: request.generation } }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || value.generation !== request.generation
      || value.error_code !== 'PERSONAL_RECORD_TASK_INTENT_CANCELLED' || value.state !== 'failed' || value.may_retry)
    throw new Error('本人记录任务的原请求取消结果需要核验。');
  return value;
}
