import isEqual from 'fast-deep-equal';
import type { IntentView } from '../../../frontend-design/types';
import type { DatasetInput, DatasetList, DatasetQuery, DatasetView, EvaluationIntentInput,
  LabelInput, PartitionInput, RequestView, WriteReceipt } from '../../../frontend-design/v2-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schemas = 'v2-protocol.schema.json#/$defs/';
const operations = ['dataset_create', 'dataset_partition', 'dataset_label', 'dataset_freeze'] as const;
const units = ['files', 'pages', 'documents', 'patients', 'encounters', 'sampling_events',
  'mas_events', 'observation_periods', 'labels'];

function checkVersion(value: string) {
  if (BigInt(value) > 9223372036854775807n) throw new Error('评价资料版本超出当前保存范围。');
}

export function evaluationTarget(identity: EvaluationIntentInput): string {
  validateApiValue(schemas + 'EvaluationIntentInput', identity);
  const names = { dataset_create: 'DatasetInput', dataset_partition: 'PartitionInput',
    dataset_label: 'LabelInput', dataset_freeze: 'DatasetFreezeInput' };
  validateApiValue(schemas + names[identity.operation_id], identity.payload);
  if (identity.payload.expected_version !== null) checkVersion(identity.payload.expected_version);
  if (identity.operation_id === 'dataset_create') {
    if (identity.target_key !== '/support/datasets') throw new Error('评价集合创建目标无效。');
    const input = identity.payload as DatasetInput;
    if (!input.purpose_copy_ids.length) throw new Error('评价集合需要准确获准用途副本。');
  } else {
    const suffix = { dataset_partition: 'partitions', dataset_label: 'labels', dataset_freeze: 'freeze' }[identity.operation_id];
    const match = new RegExp(`^/support/datasets/([0-9a-f-]{36})/${suffix}$`).exec(identity.target_key);
    if (!match) throw new Error('评价集合操作目标无效。');
    validateApiValue(schemas + 'Uuid', match[1]);
  }
  return identity.target_key;
}

export function checkDataset(value: unknown): DatasetView {
  const dataset = validateApiValue<DatasetView>(schemas + 'DatasetView', value);
  checkVersion(dataset.version);
  if (!dataset.purpose_copy_ids.length || dataset.coverage.units.length !== units.length
      || !isEqual([...dataset.coverage.units.map(item => item.unit)].sort(), [...units].sort()))
    throw new Error('评价资料缺少完整用途副本或独立统计范围。');
  const unknown = Object.entries(dataset.counts).filter(([, count]) => count === null).map(([name]) => name).sort();
  if (!isEqual(unknown, [...dataset.coverage.unrecorded_units].sort())) throw new Error('评价资料未知统计量与范围不一致。');
  if ((dataset.status === 'frozen') !== (dataset.frozen_at !== null)) throw new Error('评价冻结状态与保存时间不一致。');
  const partitions = new Map(dataset.partitions.map(item => [item.partition_id, item]));
  if (partitions.size !== dataset.partitions.length
      || new Set(dataset.partitions.map(item => item.patient_id)).size !== dataset.partitions.length
      || new Set(dataset.labels.map(item => item.label_id)).size !== dataset.labels.length)
    throw new Error('评价资料分组或标签身份重复。');
  for (const label of dataset.labels) {
    if (partitions.get(label.partition_id)?.patient_id !== label.patient_id)
      throw new Error('评价标签与准确患儿分组不一致。');
    if (label.label_state !== 'unknown' && (!label.reference_answer_ref || !label.reference_recognition_at
        || !label.confirmed_by || !label.confirmed_at)) throw new Error('评价标签缺少独立答案及确认依据。');
  }
  return dataset;
}

export async function searchEvaluationDatasets(query: DatasetQuery, signal?: AbortSignal): Promise<DatasetList> {
  validateApiValue(schemas + 'DatasetQuery', query);
  const value = await fetchJson<unknown>('/api/v2/support/datasets/search', { method: 'POST', body: query, signal });
  const result = validateApiValue<DatasetList>(schemas + 'DatasetList', value);
  if (new Set(result.items.map(item => item.dataset_id)).size !== result.items.length)
    throw new Error('评价集合目录包含重复身份。');
  result.items.forEach(checkDataset);
  return result;
}

export async function readEvaluationDataset(datasetId: string, version?: string, signal?: AbortSignal): Promise<DatasetView> {
  validateApiValue(schemas + 'Uuid', datasetId);
  let query = '';
  if (version !== undefined) { validateApiValue(schemas + 'Version', version); checkVersion(version); query = `?version=${encodeURIComponent(version)}`; }
  const dataset = checkDataset(await fetchJson<unknown>(`/api/v2/support/datasets/${encodeURIComponent(datasetId)}${query}`, { signal }));
  if (dataset.dataset_id !== datasetId || version !== undefined && dataset.version !== version)
    throw new Error('评价资料读取与准确集合版本不一致。');
  return dataset;
}

export async function prepareEvaluationRequest(identity: EvaluationIntentInput, signal?: AbortSignal): Promise<IntentView> {
  evaluationTarget(identity);
  const result = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView',
    await fetchJson<unknown>('/api/v2/write-intents', { method: 'POST', body: identity, signal }));
  if (result.request_id !== identity.request_id) throw new Error('评价提交意图与当前请求身份不一致。');
  return result;
}

function checkReceipt(identity: EvaluationIntentInput, value: unknown): WriteReceipt {
  const receipt = validateApiValue<WriteReceipt>(schemas + 'WriteReceipt', value);
  const expected = identity.payload.expected_version === null ? 1n : BigInt(identity.payload.expected_version) + 1n;
  const targetId = identity.operation_id === 'dataset_create'
    ? (identity.payload as DatasetInput).dataset_id : identity.target_key.split('/')[3];
  if (receipt.resource_type !== 'evaluation_dataset' || receipt.task_id !== null
      || receipt.request_id !== identity.request_id || BigInt(receipt.version) !== expected
      || targetId !== null && receipt.resource_id !== targetId) throw new Error('评价保存回执与准确提交版本不一致。');
  return receipt;
}

export async function submitEvaluationRequest(identity: EvaluationIntentInput, intent: IntentView,
  signal?: AbortSignal): Promise<WriteReceipt> {
  const target = evaluationTarget(identity);
  validateApiValue('protocol.schema.json#/$defs/IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('评价提交身份与保存意图不一致。');
  return checkReceipt(identity, await fetchJson<unknown>(`/api/v2${target}`, { method: 'POST', body: identity.payload,
    signal, idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation }));
}

function checkRequest(value: unknown): RequestView {
  const result = validateApiValue<RequestView>(schemas + 'RequestView', value);
  if (!operations.includes(result.operation_id as EvaluationIntentInput['operation_id']) || !result.dataset_input
      || !result.generation || !result.expires_at || !result.payload_sha256)
    throw new Error('评价请求缺少准确身份或原始正文。');
  const identity: EvaluationIntentInput = { operation_id: result.operation_id as EvaluationIntentInput['operation_id'],
    target_key: result.target_key, request_id: result.request_id, schema_version: '2.0.0', payload: result.dataset_input };
  evaluationTarget(identity);
  if (result.receipt) checkReceipt(identity, result.receipt);
  if ((result.state === 'committed') !== (result.receipt !== null)) throw new Error('评价提交状态与准确回执不一致。');
  return result;
}

export async function readEvaluationRequest(intentId: string, signal?: AbortSignal): Promise<RequestView> {
  validateApiValue(schemas + 'Uuid', intentId);
  const result = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(intentId)}`, { signal }));
  if (result.intent_id !== intentId) throw new Error('评价请求读取与原提交身份不一致。');
  return result;
}

export async function findEvaluationRequest(requestId: string, operationId: EvaluationIntentInput['operation_id'],
  targetKey: string, signal?: AbortSignal): Promise<RequestView> {
  validateApiValue(schemas + 'Uuid', requestId);
  const lookup = { lookup: 'request_id', operation_id: operationId, target_key: targetKey };
  validateApiValue(schemas + 'EvaluationRequestLookup', lookup);
  const result = checkRequest(await fetchJson<unknown>(`/api/v2/requests/${encodeURIComponent(requestId)}?${new URLSearchParams(lookup)}`, { signal }));
  if (result.request_id !== requestId || result.operation_id !== operationId || result.target_key !== targetKey)
    throw new Error('评价原请求与本人目标不一致。');
  return result;
}

export async function retryEvaluationRequest(request: RequestView, signal?: AbortSignal): Promise<IntentView> {
  checkRequest(request);
  if (!request.may_retry || !request.generation) throw new Error('当前评价请求不具备重新提交条件。');
  const input = { expected_generation: request.generation };
  validateApiValue(schemas + 'EvaluationRetryInput', input);
  const intent = validateApiValue<IntentView>('protocol.schema.json#/$defs/IntentView', await fetchJson<unknown>(
    `/api/v2/write-intents/${encodeURIComponent(request.intent_id)}/retry`, { method: 'POST', body: input, signal }));
  if (intent.intent_id !== request.intent_id || intent.request_id !== request.request_id
      || BigInt(intent.generation) !== BigInt(request.generation) + 1n) throw new Error('评价重新提交意图与原请求不一致。');
  return intent;
}

export function evaluationPayloadMatches(dataset: DatasetView, identity: EvaluationIntentInput): boolean {
  evaluationTarget(identity);
  if (identity.operation_id === 'dataset_create') {
    const input = identity.payload as DatasetInput;
    return dataset.protocol_ref === input.protocol_ref && dataset.source_manifest_ref === input.source_manifest_ref
      && dataset.label_version_ref === input.label_version_ref && isEqual(dataset.purpose_copy_ids, input.purpose_copy_ids);
  }
  if (identity.operation_id === 'dataset_partition') {
    const input = identity.payload as PartitionInput;
    return dataset.partitions.some(item => item.patient_id === input.patient_id && item.partition === input.partition
      && item.authorization_id === input.authorization_id && item.source_refs.every(ref => ref.evidence.reference === input.evidence_ref));
  }
  if (identity.operation_id === 'dataset_label') {
    const input = identity.payload as LabelInput;
    return dataset.labels.some(item => item.patient_id === input.patient_id && item.observation_period_id === input.observation_period_id
      && item.label_state === input.label_state && item.reference_answer_ref === input.reference_answer_ref
      && (item.reference_recognition_at === null || input.reference_recognition_at === null
        ? item.reference_recognition_at === input.reference_recognition_at
        : Date.parse(item.reference_recognition_at) === Date.parse(input.reference_recognition_at))
      && isEqual(item.limitations, input.limitations));
  }
  return dataset.status === 'frozen' && dataset.frozen_at !== null;
}
