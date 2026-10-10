import type { IntentView } from '../../../frontend-design/types';
import type { EvaluationDenominatorsView, EvaluationResultsView, EvaluationRunIntentInput,
  EvaluationRunView, RequestView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schemas = 'v2-protocol.schema.json#/$defs/';

function checkVersion(value: string) {
  if (BigInt(value) > 9223372036854775807n) throw new Error('评价运行版本超出当前保存范围。');
}

export function evaluationRunTarget(identity: EvaluationRunIntentInput) {
  validateApiValue(schemas + 'EvaluationRunIntentInput', identity);
  checkVersion(identity.payload.expected_version);
  const match = /^\/support\/datasets\/([0-9a-f-]{36})\/runs$/u.exec(identity.target_key);
  if (!match) throw new Error('评价运行目标无效。');
  validateApiValue(schemas + 'Uuid', match[1]);
  return match[1];
}

function runReceipt(identity: EvaluationRunIntentInput, value: unknown): WriteReceipt {
  const receipt = validateApiValue<WriteReceipt>(schemas + 'WriteReceipt', value);
  if (receipt.resource_type !== 'evaluation_run' || receipt.version !== '1' || receipt.task_id === null
      || receipt.request_id !== identity.request_id) throw new Error('评价运行受理回执与准确请求不一致。');
  return receipt;
}

export async function prepareEvaluationRun(identity: EvaluationRunIntentInput, signal?: AbortSignal) {
  evaluationRunTarget(identity);
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (intent.request_id !== identity.request_id) throw new Error('评价运行意图与准确请求不一致。');
  return intent;
}

export async function submitEvaluationRun(identity: EvaluationRunIntentInput, intent: IntentView, signal?: AbortSignal) {
  evaluationRunTarget(identity);
  validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('评价运行提交身份不一致。');
  return runReceipt(identity, await fetchJson<unknown>(`/api/v2${identity.target_key}`, { method: 'POST', body: identity.payload,
    signal, idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation }));
}

export async function readEvaluationRun(runId: string, signal?: AbortSignal): Promise<EvaluationRunView> {
  validateApiValue(schemas + 'Uuid', runId);
  const run = validateApiValue<EvaluationRunView>(schemas + 'EvaluationRunView',
    await fetchJson<unknown>(`/api/v2/support/evaluation-runs/${encodeURIComponent(runId)}`, { signal }));
  checkVersion(run.dataset_version); checkVersion(run.task.status_version);
  const terminal = ['completed', 'failed', 'insufficient', 'superseded'].includes(run.status);
  if (run.run_id !== runId || run.task.task_type !== 'assessment'
      || run.denominator_ref !== `/support/evaluation-runs/${runId}/denominators`
      || terminal !== (run.task.poll_after_ms === null)) throw new Error('评价运行状态或分母引用不一致。');
  if (run.status === 'completed' || run.status === 'insufficient') {
    if (run.task.status !== 'completed' || run.task.result_type !== 'evaluation_run' || run.task.result_ref !== runId
        || run.result_ref !== `/support/evaluation-runs/${runId}/results`) throw new Error('评价运行结果与当前任务不一致。');
  } else if (run.result_ref !== null || run.task.result_ref !== null || run.task.result_type !== null)
    throw new Error('评价运行尚未完成，结果引用不可采用。');
  if (run.status === 'running' && run.task.status !== 'running'
      || run.status === 'failed' && run.task.status !== 'failed'
      || run.status === 'superseded' && run.task.status !== 'superseded') throw new Error('评价运行与实际任务状态不一致。');
  return run;
}

export async function readEvaluationDenominators(runId: string, signal?: AbortSignal): Promise<EvaluationDenominatorsView> {
  validateApiValue(schemas + 'Uuid', runId);
  const value = validateApiValue<EvaluationDenominatorsView>(schemas + 'EvaluationDenominatorsView',
    await fetchJson<unknown>(`/api/v2/support/evaluation-runs/${encodeURIComponent(runId)}/denominators`, { signal }));
  const data = value.denominators;
  if (value.run_id !== runId || data.patient_ids.length === 0
      || data.patient_ids.some(id => !data.unit_ids.patients.includes(id))
      || data.eligible_label_ids.some(id => data.excluded_unknown_label_ids.includes(id) || !data.unit_ids.labels.includes(id))
      || data.excluded_unknown_label_ids.some(id => !data.unit_ids.labels.includes(id))) throw new Error('评价运行分母与准确统计单位不一致。');
  return value;
}

export async function readEvaluationResults(run: EvaluationRunView, signal?: AbortSignal): Promise<EvaluationResultsView> {
  validateApiValue(schemas + 'EvaluationRunView', run);
  if (!['completed', 'insufficient'].includes(run.status)) throw new Error('当前评价运行尚未形成可读取结果。');
  const result = validateApiValue<EvaluationResultsView>(schemas + 'EvaluationResultsView',
    await fetchJson<unknown>(`/api/v2/support/evaluation-runs/${encodeURIComponent(run.run_id)}/results`, { signal }));
  if (result.run_id !== run.run_id || result.status !== run.status) throw new Error('评价结果与当前运行身份不一致。');
  if (new Set(result.results.map(item => item.unit)).size !== 9
      || result.results.some(item => item.input_sha256 !== result.input_sha256
        || item.result_ref !== `/support/evaluation-runs/${run.run_id}/results#${item.unit}`
        || item.state === 'unrecorded' && (item.registered_items !== null || item.unit_ids.length !== 0)
        || item.state !== 'unrecorded' && item.registered_items !== item.unit_ids.length)
      || new Set(result.results.map(item => item.dataset_version_id)).size !== 1
      || new Set(result.results.map(item => item.dataset_checksum)).size !== 1)
    throw new Error('评价结果统计单位、准确版本或输入摘要不一致。');
  return result;
}

function checkRequest(value: unknown): RequestView {
  const result = validateApiValue<RequestView>(schemas + 'RequestView', value);
  if (result.operation_id !== 'dataset_run_create' || !result.evaluation_run_input || !result.generation
      || !result.expires_at || !result.payload_sha256) throw new Error('评价运行原请求身份或正文不可读取。');
  const identity: EvaluationRunIntentInput = { operation_id: 'dataset_run_create', target_key: result.target_key,
    request_id: result.request_id, schema_version: '2.0.0', payload: result.evaluation_run_input };
  evaluationRunTarget(identity);
  if (result.receipt) runReceipt(identity, result.receipt);
  if ((result.state === 'committed') !== (result.receipt !== null)) throw new Error('评价运行提交状态与保存回执不一致。');
  return result;
}

export async function readEvaluationRunRequest(intentId: string, signal?: AbortSignal) {
  validateApiValue(schemas + 'Uuid', intentId);
  const result = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(intentId)}`, { signal }));
  if (result.intent_id !== intentId) throw new Error('评价运行恢复与原意图不一致。');
  return result;
}

export async function findEvaluationRunRequest(requestId: string, datasetId: string, signal?: AbortSignal) {
  validateApiValue(schemas + 'Uuid', requestId); validateApiValue(schemas + 'Uuid', datasetId);
  const lookup = { lookup: 'request_id', operation_id: 'dataset_run_create', target_key: `/support/datasets/${datasetId}/runs` };
  validateApiValue(schemas + 'EvaluationRunRequestLookup', lookup);
  const result = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(requestId)}?${new URLSearchParams(lookup)}`, { signal }));
  if (result.request_id !== requestId || result.target_key !== lookup.target_key) throw new Error('评价运行原请求与当前集合不一致。');
  return result;
}

export async function retryEvaluationRunRequest(request: RequestView, signal?: AbortSignal) {
  checkRequest(request);
  if (!request.may_retry || !request.generation) throw new Error('当前评价运行不具备重试条件。');
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>(`/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, {
      method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (intent.intent_id !== request.intent_id || intent.request_id !== request.request_id
      || BigInt(intent.generation) !== BigInt(request.generation) + 1n) throw new Error('评价运行重试与原意图不一致。');
  return intent;
}
