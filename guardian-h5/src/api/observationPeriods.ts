import type { IntentView } from '../../../frontend-design/types';
import type { WriteReceipt } from '../../../frontend-design/v2-types';
import type { ObservationPeriodView, PeriodIdentity, PeriodRequest } from '../../../frontend-design/observation-period-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'observation-period.schema.json#/$defs/';

function patientOf(identity: PeriodIdentity) {
  validateApiValue(`${schema}Intent`, identity);
  const route = /^\/patients\/([0-9a-f-]{36})\/observation-period\/versions$/.exec(identity.target_key);
  if (!route) throw new Error('观察时段与提交目标不一致。');
  validateApiValue('protocol.schema.json#/$defs/Uuid', route[1]);
  return route[1];
}

export async function readObservationPeriod(patientId: string, signal: AbortSignal, versionId?: string) {
  validateApiValue('protocol.schema.json#/$defs/Uuid', patientId);
  if (versionId) validateApiValue('protocol.schema.json#/$defs/Uuid', versionId);
  const path = versionId ? `/observation-period-versions/${encodeURIComponent(versionId)}`
    : `/patients/${encodeURIComponent(patientId)}/observation-period`;
  const value = validateApiValue<ObservationPeriodView>(`${schema}View`, await fetchJson<unknown>(`/api/v2${path}`, { signal }));
  if (value.patient_id !== patientId || versionId && value.version_id !== versionId
    || new Set(value.samplings.map(item => item.fact.fact_version_id)).size !== value.samplings.length
    || value.samplings.some(item => item.source.field_id !== item.fact.field_id || item.source.record_version_id !== item.fact.record_version_id))
    throw new Error('观察时段、采样资料或来源身份不一致。');
  const facts = new Set(value.samplings.map(item => item.fact.fact_version_id));
  if (value.intervals.some(item => !facts.has(item.before_fact_version_id) || !facts.has(item.after_fact_version_id))
    || value.unknown_time_fact_ids.some(id => !facts.has(id))) throw new Error('采样间隔缺少准确的原始资料。');
  return value;
}

export async function preparePeriod(identity: PeriodIdentity, signal: AbortSignal) {
  patientOf(identity);
  const result = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (result.request_id !== identity.request_id) throw new Error('观察时段意图与原请求身份不一致。');
  return result;
}

export async function submitPeriod(identity: PeriodIdentity, intent: IntentView, signal: AbortSignal) {
  patientOf(identity);
  if (intent.request_id !== identity.request_id) throw new Error('观察时段提交与原意图身份不一致。');
  const value = validateApiValue<WriteReceipt>('v2-protocol.schema.json#/$defs/WriteReceipt', await fetchJson<unknown>(`/api/v2${identity.target_key}`, {
    method: 'POST', body: identity.payload, signal, idempotencyKey: identity.request_id,
    writeIntent: intent.intent_id, intentGeneration: intent.generation,
  }));
  if (value.resource_type !== 'observation_period' || value.request_id !== identity.request_id || value.task_id !== null)
    throw new Error('观察时段保存回执与原请求不一致。');
  return value;
}

export async function readPeriodRequest(intentId: string, signal: AbortSignal, identity?: Pick<PeriodIdentity, 'request_id' | 'target_key'>) {
  validateApiValue('protocol.schema.json#/$defs/Uuid', intentId);
  const query = identity ? `?${new URLSearchParams({ lookup: 'request_id', operation_id: 'observation_period_change', target_key: identity.target_key })}` : '';
  const value = validateApiValue<PeriodRequest>(`${schema}Request`, await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(intentId)}${query}`, { signal }));
  patientOf({ operation_id: value.operation_id, target_key: value.target_key, request_id: value.request_id, schema_version: '2.0.0', payload: value.period_input });
  if (!identity && value.intent_id !== intentId || identity && (value.request_id !== identity.request_id || value.target_key !== identity.target_key)
    || value.receipt && (value.receipt.resource_type !== 'observation_period' || value.receipt.request_id !== value.request_id))
    throw new Error('观察时段原请求、正文与保存回执不一致。');
  return value;
}

export async function retryPeriod(request: PeriodRequest, signal: AbortSignal) {
  if (!request.may_retry || request.receipt) throw new Error('观察时段原请求尚不具备重试条件。');
  const value = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    `/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, { method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || BigInt(value.generation) !== BigInt(request.generation) + 1n)
    throw new Error('观察时段重试没有保持原请求身份。');
  return value;
}
