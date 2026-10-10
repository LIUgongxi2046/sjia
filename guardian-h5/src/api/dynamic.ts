import type { IntentView } from '../../../frontend-design/types';
import type { DynamicCreationOptions, DynamicCreationPlan, DynamicIntentInput, DynamicTriggerHistory, DynamicView, RequestView, TaskView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'v2-protocol.schema.json#/$defs/';
const terminal = new Set<TaskView['status']>(['completed', 'failed', 'cancelled', 'superseded']);
export function dynamicPlanKey(plan: DynamicCreationPlan) {
  return [plan.rule.config_id, plan.input_rule.config_id, plan.window.config_id, plan.verification.config_id].join(':');
}
export function dynamicTarget(identity: DynamicIntentInput) {
  validateApiValue(schema + 'DynamicIntentInput', identity);
  const match = /^\/support\/patients\/([0-9a-f-]{36})\/alert-evaluations$/u.exec(identity.target_key);
  if (!match) throw new Error('动态评估目标无效。');
  validateApiValue(schema + 'Uuid', match[1]);
  if (Date.parse(identity.payload.assessment_at) > Date.parse(identity.payload.known_at))
    throw new Error('评估时点不能晚于资料截止时点。');
  const choices = identity.payload.clinical_selections ?? [];
  if (new Set(choices.map(item => `${item.input_role}:${item.variable_code}`)).size !== choices.length
      || choices.some(item => !item.reason.trim() || identity.payload.mode === 'current' && item.prior_task_id !== null
        || identity.payload.mode === 'replay' && item.prior_task_id === null)) throw new Error('动态专业选择身份或历史引用无效。');
  return match[1];
}
function checkTask(value: unknown) {
  const task = validateApiValue<TaskView>(schema + 'TaskView', value);
  if (task.task_type !== 'alert_evaluation' || BigInt(task.status_version) > 9223372036854775807n
      || terminal.has(task.status) !== (task.poll_after_ms === null)
      || task.poll_after_ms !== null && !Number.isSafeInteger(task.poll_after_ms)
      || task.status === 'completed' && (task.result_type !== 'alert_evaluation' || task.result_ref === null)
      || task.status !== 'completed' && (task.result_type !== null || task.result_ref !== null))
    throw new Error('动态评估任务状态或结果引用无效。');
  return task;
}
export async function readDynamicOptions(patientId: string, knownAt: string, mode: 'current' | 'replay', signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', patientId);
  const query = { known_at: knownAt, mode }; validateApiValue(schema + 'AssessmentCreationQuery', query);
  const options = validateApiValue<DynamicCreationOptions>(schema + 'DynamicCreationOptions', await fetchJson<unknown>(
    `/api/v2/support/patients/${encodeURIComponent(patientId)}/alert-evaluation-options?${new URLSearchParams(query)}`, { signal }));
  if (options.patient_id !== patientId || options.mode !== mode || Date.parse(options.known_at) !== Date.parse(knownAt)
      || new Set(options.plans.map(dynamicPlanKey)).size !== options.plans.length
      || new Set(options.observation_periods.map(item => item.period_id)).size !== options.observation_periods.length)
    throw new Error('动态评估选项与准确病例、时点或配置不一致。');
  for (const plan of options.plans) {
    if (new Set(plan.selection_variables.map(item => `${item.input_role}:${item.variable_code}`)).size !== plan.selection_variables.length
        || plan.selection_variables.some(item => new Set(item.candidates.map(candidate => candidate.fact_version_id)).size !== item.candidates.length)
        || plan.prior_selections.some(item => Date.parse(item.saved_at) > Date.parse(knownAt)
          || item.choices.some(choice => choice.prior_task_id !== null))) throw new Error('动态选择候选或原保存身份不一致。');
  }
  return options;
}
export async function prepareDynamic(identity: DynamicIntentInput, signal?: AbortSignal) {
  dynamicTarget(identity);
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (intent.request_id !== identity.request_id) throw new Error('动态意图与原请求不一致。');
  return intent;
}
export async function submitDynamic(identity: DynamicIntentInput, intent: IntentView, signal?: AbortSignal) {
  dynamicTarget(identity);
  if (intent.request_id !== identity.request_id || intent.status !== 'prepared') throw new Error('动态意图与原提交不一致。');
  const task = checkTask(await fetchJson<unknown>('/api/v2' + identity.target_key, { method: 'POST', body: identity.payload, signal,
    writeIntent: intent.intent_id, idempotencyKey: identity.request_id, intentGeneration: intent.generation }));
  if (task.request_id !== identity.request_id) throw new Error('动态任务与原提交不一致。');
  return task;
}
export async function readDynamicTask(taskId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', taskId);
  const task = checkTask(await fetchJson<unknown>(`/api/v2/support/alert-evaluation-tasks/${encodeURIComponent(taskId)}`, { signal }));
  if (task.task_id !== taskId) throw new Error('动态任务身份不一致。');
  return task;
}
export async function readDynamic(evaluationId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', evaluationId);
  const result = validateApiValue<DynamicView>(schema + 'DynamicView', await fetchJson<unknown>(
    `/api/v2/support/alert-evaluations/${encodeURIComponent(evaluationId)}`, { signal }));
  checkTask(result.task);
  if (result.evaluation_id !== evaluationId || result.task.result_ref !== evaluationId || result.task.status !== 'completed'
      || BigInt(result.context_sequence) > 9223372036854775807n) throw new Error('动态结果与保存任务不一致。');
  if (result.validity === 'invalid') {
    if (result.signals.length || result.combinations !== null || result.context_states !== null || result.lifecycle !== null
        || Object.keys(result.selected_refs).length || result.exclusions.length || result.clinical_selections.length
        || result.relation_refs.length || result.relation_proofs.length
        || result.automatic_trigger != null
        || result.decision !== 'unknown' || result.reason_code === null) throw new Error('失效动态评估含有不能采用的内容。');
  } else {
    if (!result.combinations || !result.context_states || !result.lifecycle || result.reason_code !== null
        || result.decision !== result.combinations.trigger.state) throw new Error('动态结果缺少完整保存依据。');
    if (new Set(result.signals.map(item => item.signal_id)).size !== result.signals.length) throw new Error('动态信号身份重复。');
    for (const signal of result.signals) {
      const fields = [...new Set(signal.inputs.map(item => item.reference.field_id))].sort();
      if (JSON.stringify(fields) !== JSON.stringify([...signal.input_field_ids].sort())
          || signal.inputs.some((item, position) => item.position !== position)
          || signal.pair_results.some((item, position) => item.position !== position)) throw new Error('动态信号来源或序列位置不一致。');
      if (signal.assessment_id && (!signal.assessment_kind || signal.assessment_reference?.id !== signal.assessment_id))
        throw new Error('引用的静态评估身份或计算类型不一致。');
    }
    if (result.mode === 'replay' && (result.lifecycle.state !== 'replay' || result.lifecycle.action !== 'blocked'
        || result.lifecycle.event_id !== null || result.lifecycle.episode_id !== null)) throw new Error('历史回放含有家庭发布或周期采用结果。');
  }
  return result;
}
export async function readDynamicTriggers(patientId: string, signal?: AbortSignal, cursor?: string) {
  validateApiValue(schema + 'Uuid', patientId);
  const query: Record<string, string> = cursor ? { cursor } : {};
  validateApiValue(schema + 'DynamicTriggerQuery', query);
  const history = validateApiValue<DynamicTriggerHistory>(schema + 'DynamicTriggerHistory', await fetchJson<unknown>(
    `/api/v2/support/patients/${encodeURIComponent(patientId)}/alert-evaluation-triggers?${new URLSearchParams(query)}`, { signal }));
  if (history.patient_id !== patientId || new Set(history.items.map(item => item.task_id)).size !== history.items.length
      || history.items.some(item => (item.status === 'completed') !== (item.evaluation_task_id !== null)))
    throw new Error('自动触发记录与病例或实际评估任务不一致。');
  return history;
}
function checkRequest(value: unknown) {
  const request = validateApiValue<RequestView>(schema + 'RequestView', value);
  if (request.operation_id !== 'alert_evaluation_v2_create' || !request.dynamic_input || !request.generation
      || !request.payload_sha256 || !request.expires_at) throw new Error('动态原请求身份或正文不可读取。');
  dynamicTarget({ operation_id: 'alert_evaluation_v2_create', target_key: request.target_key, request_id: request.request_id,
    schema_version: '2.0.0', payload: request.dynamic_input });
  if (request.receipt) {
    const receipt = validateApiValue<WriteReceipt>(schema + 'WriteReceipt', request.receipt);
    if (receipt.resource_type !== 'task' || receipt.resource_id !== receipt.task_id || receipt.version !== '1'
        || receipt.request_id !== request.request_id) throw new Error('动态保存回执与原请求不一致。');
  }
  if ((request.state === 'committed') !== (request.receipt !== null)) throw new Error('动态提交状态与保存回执不一致。');
  return request;
}
export async function readDynamicRequest(intentId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', intentId);
  const request = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(intentId)}`, { signal }));
  if (request.intent_id !== intentId) throw new Error('动态恢复与原意图不一致。');
  return request;
}
export async function findDynamicRequest(requestId: string, patientId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', requestId); validateApiValue(schema + 'Uuid', patientId);
  const lookup = { lookup: 'request_id', operation_id: 'alert_evaluation_v2_create', target_key: `/support/patients/${patientId}/alert-evaluations` };
  validateApiValue(schema + 'DynamicRequestLookup', lookup);
  const request = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(requestId)}?${new URLSearchParams(lookup)}`, { signal }));
  if (request.request_id !== requestId || request.target_key !== lookup.target_key) throw new Error('动态恢复与原请求范围不一致。');
  return request;
}
export async function retryDynamicRequest(request: RequestView, signal?: AbortSignal) {
  checkRequest(request);
  if (!request.may_retry || !request.generation) throw new Error('当前动态请求不具备重试条件。');
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>(`/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, {
      method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (intent.intent_id !== request.intent_id || intent.request_id !== request.request_id
      || BigInt(intent.generation) !== BigInt(request.generation) + 1n) throw new Error('动态重试与原意图不一致。');
  return intent;
}
