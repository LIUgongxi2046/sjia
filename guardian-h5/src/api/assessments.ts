import type { IntentView } from '../../../frontend-design/types';
import type { AssessmentCreationOptions, AssessmentIntentInput, AssessmentView, ClinicalDirectoryView, RequestView, TaskView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'v2-protocol.schema.json#/$defs/';
const terminal = new Set<TaskView['status']>(['completed', 'failed', 'cancelled', 'superseded']);

export async function readClinicalDirectory(action: ClinicalDirectoryView['action'], signal?: AbortSignal) {
  validateApiValue(schema + 'ClinicalDirectoryQuery', { action });
  const value = validateApiValue<ClinicalDirectoryView>(schema + 'ClinicalDirectoryView',
    await fetchJson<unknown>(`/api/v2/support/clinical-scopes?${new URLSearchParams({ action })}`, { signal }));
  if (value.action !== action || new Set(value.patients.map(item => item.patient_id)).size !== value.patients.length
      || value.patients.some(item => new Set(item.records.map(record => record.record_version_id)).size !== item.records.length
        || item.records.some(record => !item.purposes.includes(record.purpose)))
      || action === 'execute_assessment' && value.patients.some(item => item.records.length))
    throw new Error('受控资料目录与当前动作或准确范围不一致。');
  return value;
}

export function assessmentPlanKey(plan: AssessmentCreationOptions['plans'][number]) {
  return [plan.rule.config_id, plan.window.config_id, plan.verification.config_id].join(':');
}

export async function readAssessmentCreationOptions(patientId: string, knownAt: string, mode: 'current' | 'replay', signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', patientId);
  const query = { known_at: knownAt, mode }; validateApiValue(schema + 'AssessmentCreationQuery', query);
  const value = validateApiValue<AssessmentCreationOptions>(schema + 'AssessmentCreationOptions',
    await fetchJson<unknown>(`/api/v2/support/patients/${encodeURIComponent(patientId)}/assessment-options?${new URLSearchParams(query)}`, { signal }));
  if (value.patient_id !== patientId || value.mode !== mode || Date.parse(value.known_at) !== Date.parse(knownAt)
      || new Set(value.plans.map(assessmentPlanKey)).size !== value.plans.length
      || new Set(value.observation_periods.map(item => item.observation_period_id)).size !== value.observation_periods.length
      || new Set(value.observation_periods.map(item => item.period_id)).size !== value.observation_periods.length)
    throw new Error('评估选项与当前病例、资料截止时点或准确配置不一致。');
  for (const plan of value.plans) {
    if (new Set(plan.selection_variables.map(item => item.variable_code)).size !== plan.selection_variables.length
        || plan.selection_variables.some(item => new Set(item.candidates.map(candidate => candidate.fact_version_id)).size !== item.candidates.length)
        || new Set(plan.prior_selections.map(item => item.task_id)).size !== plan.prior_selections.length
        || plan.prior_selections.some(item => Date.parse(item.saved_at) > Date.parse(knownAt)
          || new Set(item.choices.map(choice => choice.variable_code)).size !== item.choices.length
          || item.choices.some(choice => choice.prior_task_id !== null
            || !plan.selection_variables.some(variable => variable.variable_code === choice.variable_code))))
      throw new Error('明确选择的项目、候选或原保存身份不一致。');
  }
  return value;
}

export function assessmentTarget(identity: AssessmentIntentInput) {
  validateApiValue(schema + 'AssessmentIntentInput', identity);
  const match = /^\/support\/patients\/([0-9a-f-]{36})\/assessments$/u.exec(identity.target_key);
  if (!match) throw new Error('静态评估目标无效。');
  validateApiValue(schema + 'Uuid', match[1]);
  if (new Date(identity.payload.assessment_at) > new Date(identity.payload.known_at))
    throw new Error('评估时点不能晚于资料截止时点。');
  const choices = identity.payload.clinical_selections ?? [];
  if (new Set(choices.map(item => item.variable_code)).size !== choices.length
      || choices.some(item => !item.reason.trim() || identity.payload.mode === 'current' && item.prior_task_id !== null
        || identity.payload.mode === 'replay' && item.prior_task_id === null))
    throw new Error('专业选择身份、原因或历史引用无效。');
  return match[1];
}

function checkTask(value: unknown): TaskView {
  const task = validateApiValue<TaskView>(schema + 'TaskView', value);
  if (task.task_type !== 'assessment' || BigInt(task.status_version) > 9223372036854775807n
      || terminal.has(task.status) !== (task.poll_after_ms === null)
      || task.poll_after_ms !== null && !Number.isSafeInteger(task.poll_after_ms))
    throw new Error('静态评估任务状态无效。');
  if (task.status === 'completed') {
    if (task.result_type !== 'assessment' || task.result_ref === null) throw new Error('静态评估任务缺少保存结果。');
  } else if (task.result_type !== null || task.result_ref !== null) throw new Error('静态评估尚未完成，结果引用不可采用。');
  return task;
}

export async function prepareAssessment(identity: AssessmentIntentInput, signal?: AbortSignal) {
  assessmentTarget(identity);
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (intent.request_id !== identity.request_id) throw new Error('静态评估意图与原请求不一致。');
  return intent;
}

export async function submitAssessment(identity: AssessmentIntentInput, intent: IntentView, signal?: AbortSignal) {
  assessmentTarget(identity); validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('静态评估提交身份不一致。');
  const task = checkTask(await fetchJson<unknown>(`/api/v2${identity.target_key}`, { method: 'POST', body: identity.payload,
    signal, idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation }));
  if (task.request_id !== identity.request_id) throw new Error('静态评估任务与原提交不一致。');
  return task;
}

export async function readAssessmentTask(taskId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', taskId);
  const task = checkTask(await fetchJson<unknown>(`/api/v2/support/assessment-tasks/${encodeURIComponent(taskId)}`, { signal }));
  if (task.task_id !== taskId) throw new Error('静态评估任务与读取目标不一致。');
  return task;
}

export async function readAssessment(assessmentId: string, signal?: AbortSignal): Promise<AssessmentView> {
  validateApiValue(schema + 'Uuid', assessmentId);
  const result = validateApiValue<AssessmentView>(schema + 'AssessmentView',
    await fetchJson<unknown>(`/api/v2/support/assessments/${encodeURIComponent(assessmentId)}`, { signal }));
  checkTask(result.task);
  if (!Array.isArray(result.clinical_selections)) throw new Error('评估缺少保存的明确选择状态。');
  if (result.assessment_id !== assessmentId || result.task.result_ref !== assessmentId
      || result.task.status !== 'completed') throw new Error('静态评估结果与保存任务不一致。');
  if (result.validity === 'invalid') {
    if (result.output !== null || result.inputs.length !== 0 || result.exclusions.length !== 0 || result.clinical_selections.length !== 0 || result.reason_code === null)
      throw new Error('失效评估含有不能采用的内容。');
  } else {
    if (result.output === null || result.reason_code !== null) throw new Error('静态评估缺少当前有效输出。');
    if (result.kind === 'classification' && (result.output.classification_result === null
        || result.output.classification_conditions === null || result.output.ms_total !== null
        || result.output.ms_contributions !== null || result.output.ms_threshold_result !== null))
      throw new Error('静态分类输出类型不一致。');
    if (result.kind === 'ms' && (result.output.classification_result !== null || result.output.classification_conditions !== null
        || result.output.ms_contributions === null || (result.output.ms_total === null) !== (result.output.ms_threshold_result === null)))
      throw new Error('MS 输出类型不一致。');
    if (result.computation_status !== 'completed' && result.output.ms_total !== null)
      throw new Error('资料不足评估含有完整 MS 总分。');
    if (new Set(result.inputs.map(input => input.variable_code)).size !== result.inputs.length)
      throw new Error('静态评估输入项目重复。');
    if (new Set(result.clinical_selections.map(item => item.variable_code)).size !== result.clinical_selections.length
        || result.clinical_selections.some(item => !result.inputs.some(input => input.variable_code === item.variable_code
          && input.input_snapshot.reference.fact_version_id === item.fact_version_id)
          || result.mode === 'replay' && (item.prior_task_id === null || Date.parse(item.saved_at) > Date.parse(result.known_at))
          || result.mode === 'current' && item.prior_task_id !== null))
      throw new Error('保存的专业选择与采用输入或截止时点不一致。');
  }
  return result;
}

function checkReceipt(identity: AssessmentIntentInput, value: unknown): WriteReceipt {
  const receipt = validateApiValue<WriteReceipt>(schema + 'WriteReceipt', value);
  if (receipt.resource_type !== 'task' || receipt.resource_id !== receipt.task_id || receipt.version !== '1'
      || receipt.request_id !== identity.request_id) throw new Error('静态评估保存回执与原请求不一致。');
  return receipt;
}

function checkRequest(value: unknown) {
  const request = validateApiValue<RequestView>(schema + 'RequestView', value);
  if (request.operation_id !== 'assessment_v2_create' || !request.assessment_input || !request.generation
      || !request.payload_sha256 || !request.expires_at) throw new Error('静态评估原请求身份或正文不可读取。');
  const identity: AssessmentIntentInput = { operation_id: 'assessment_v2_create', target_key: request.target_key,
    request_id: request.request_id, schema_version: '2.0.0', payload: request.assessment_input };
  assessmentTarget(identity);
  if (request.receipt) checkReceipt(identity, request.receipt);
  if ((request.state === 'committed') !== (request.receipt !== null)) throw new Error('静态评估提交状态与保存回执不一致。');
  return request;
}

export async function readAssessmentRequest(intentId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', intentId);
  const request = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(intentId)}`, { signal }));
  if (request.intent_id !== intentId) throw new Error('静态评估恢复与原意图不一致。');
  return request;
}

export async function findAssessmentRequest(requestId: string, patientId: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Uuid', requestId); validateApiValue(schema + 'Uuid', patientId);
  const lookup = { lookup: 'request_id', operation_id: 'assessment_v2_create', target_key: `/support/patients/${patientId}/assessments` };
  validateApiValue(schema + 'AssessmentRequestLookup', lookup);
  const request = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(requestId)}?${new URLSearchParams(lookup)}`, { signal }));
  if (request.request_id !== requestId || request.target_key !== lookup.target_key) throw new Error('静态评估恢复与原请求范围不一致。');
  return request;
}

export async function retryAssessmentRequest(request: RequestView, signal?: AbortSignal) {
  checkRequest(request);
  if (!request.may_retry || !request.generation) throw new Error('当前静态评估请求不具备重试条件。');
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>(`/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, {
      method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (intent.intent_id !== request.intent_id || intent.request_id !== request.request_id
      || BigInt(intent.generation) !== BigInt(request.generation) + 1n) throw new Error('静态评估重试与原意图不一致。');
  return intent;
}
