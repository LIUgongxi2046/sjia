import { ApiError, checkRequestGuard, fetchJson } from './client';
import { validateApiValue } from './schema';
import { automaticPollingBudgetMs, canContinueTaskPolling, requiredTaskPollingInterval } from './taskPolling';

interface ValidationTask {
  task_id: string; task_type: string; request_id: string | null; accepted_at: string;
  status: string; status_version: string; poll_after_ms: number | null; allowed_actions: string[];
  result_type: string | null; result_ref: string | null; error_code: string | null;
}
interface UploadGroup {
  group_id: string; patient_id: string; version: string; status: string;
  files: {file_id: string; ordinal: number}[];
}
function checkedTask(value: unknown, groupId: string, previous?: ValidationTask): ValidationTask {
  const task = validateApiValue<ValidationTask>('protocol.schema.json#/$defs/TaskView', value);
  if (task.task_type !== 'file_validation' || task.request_id !== groupId || previous && task.task_id !== previous.task_id
    || task.status === 'completed' && (task.result_type !== 'file_group' || task.result_ref !== groupId)
    || task.status !== 'completed' && (task.result_type !== null || task.result_ref !== null)) {
    throw new Error('原件校验任务回执与当前上传组不一致。');
  }
  if (previous && (BigInt(task.status_version) < BigInt(previous.status_version)
    || task.status_version === previous.status_version && (task.status !== previous.status
      || task.result_type !== previous.result_type || task.result_ref !== previous.result_ref || task.error_code !== previous.error_code))) {
    throw new Error('原件校验任务状态版本倒退或发生冲突。');
  }
  return task;
}
async function delay(milliseconds: number, signal: AbortSignal) {
  await new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, milliseconds);
    if (signal.aborted) abort(); else signal.addEventListener('abort', abort, {once:true});
  });
}

export async function waitForFileValidationTask(initial: unknown, groupId: string, signal?: AbortSignal, isCurrent?: () => boolean) {
  checkRequestGuard({ signal, isCurrent });
  let task = checkedTask(initial, groupId);
  const started = performance.now();
  const deadline = new AbortController();
  const expiry = new Error('原件校验自动查询已经结束，可以重新读取原上传组确认当前状态。');
  const timer = setTimeout(() => deadline.abort(expiry), automaticPollingBudgetMs);
  const combined = signal ? AbortSignal.any([signal, deadline.signal]) : deadline.signal;
  const guard = { signal: combined, isCurrent };
  try {
    while (['accepted', 'queued', 'running'].includes(task.status)) {
      const interval = requiredTaskPollingInterval(task.poll_after_ms);
      if (!canContinueTaskPolling(performance.now() - started, interval)) throw expiry;
      checkRequestGuard(guard);
      await delay(interval, combined);
      checkRequestGuard(guard);
      if (performance.now() - started >= automaticPollingBudgetMs) throw expiry;
      const next = checkedTask(await fetchJson(`/api/tasks/${task.task_id}`, guard), groupId, task);
      checkRequestGuard(guard);
      if (performance.now() - started >= automaticPollingBudgetMs) throw expiry;
      task = next;
    }
    return task;
  } finally {
    clearTimeout(timer);
  }
}

// 不可变上传组标识在刷新后继续作为原完成请求的身份。
export async function completeValidatedFileGroup(groupId: string, csrfToken?: string, signal?: AbortSignal, expectedPatientId?: string, isCurrent?: () => boolean) {
  const guard = { signal, isCurrent };
  checkRequestGuard(guard);
  const group = validateApiValue<UploadGroup>('v2-protocol.schema.json#/$defs/UploadGroupView',
    await fetchJson<unknown>(`/api/uploads/${groupId}`, {...guard}));
  if (group.group_id !== groupId || expectedPatientId !== undefined && group.patient_id !== expectedPatientId || !group.files.length) throw new Error('完整原件组尚未取得。');
  if (group.status === 'available') return {group_id:groupId,status:'available'};
  if (group.status === 'failed' || group.status === 'disposed' || group.status === 'superseded') throw new Error('原件校验未完成，请保留原上传组并重新选择原件建立新的上传组。');
  const target = `/api/uploads/${groupId}/complete`;
  const payload = {expected_version:group.version,file_ids:[...group.files].sort((a,b)=>a.ordinal-b.ordinal).map(file=>file.file_id)};
  let task: ValidationTask | null = null;
  for (let recovery=0; task===null; recovery++) {
    const intent = validateApiValue<{intent_id:string;generation:string}>('protocol.schema.json#/$defs/IntentView',
      await fetchJson('/api/write-intents',{method:'POST',body:{operation_id:'file_group_complete',target_key:target,
        request_id:groupId,schema_version:'1.0.0',payload},csrfToken,...guard}));
    try {
      task = checkedTask(await fetchJson(target,{method:'POST',body:payload,writeIntent:intent.intent_id,
        intentGeneration:intent.generation,idempotencyKey:groupId,csrfToken,...guard}),groupId);
    } catch(error) {
      if (!(error instanceof ApiError) || error.code !== 'FILE_VALIDATION_INTENT_EXPIRED' || recovery > 0) throw error;
      const original = validateApiValue<{intent_id:string;request_id:string;operation_id:string;target_key:string;generation:string;may_retry:boolean;task:unknown}>('file-validation.schema.json',
        await fetchJson(`/api/requests/${intent.intent_id}`,{...guard}));
      if(original.intent_id!==intent.intent_id||original.request_id!==groupId||original.operation_id!=='file_group_complete'||original.target_key!==target)throw new Error('原件校验原请求不一致。');
      if(original.task!==null)task=checkedTask(original.task,groupId);
      else if(original.may_retry)await fetchJson(`/api/write-intents/${intent.intent_id}/retry`,{method:'POST',body:{expected_generation:original.generation},csrfToken,...guard});
      else throw new Error('原件校验提交结果仍未明确，请重新读取原上传组。');
    }
  }
  task = await waitForFileValidationTask(task, groupId, signal, isCurrent);
  if(task.status!=='completed')throw new Error(`原件校验未完成：${task.error_code??task.status}。请保留原上传组并重新选择原件建立新上传组。`);
  checkRequestGuard(guard);
  return {group_id:groupId,status:'available'};
}
