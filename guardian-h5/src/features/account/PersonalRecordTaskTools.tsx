import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { IntentView } from '../../../../frontend-design/types';
import type { PersonalTaskIdentity, PersonalTaskList, PersonalTaskOptions, PersonalTaskReceipt,
  PersonalTaskRequest, PersonalTaskView } from '../../../../frontend-design/personal-record-task-types';
import { ApiError } from '../../api/client';
import { cancelPersonalTaskRequest, personalTaskOptions, preparePersonalTask, readPersonalTask,
  readPersonalTaskRequest, retryPersonalTask, searchPersonalTasks, submitPersonalTask } from '../../api/personalRecordTasks';
import { UI2ReadState } from '../../components/UI2ReadState';

const statuses = { pending: '待处理', completed: '已经完成', cancelled: '已经取消' };
interface Resources { list: PersonalTaskList; options: PersonalTaskOptions; selected: PersonalTaskView | null }

export function PersonalRecordTaskTools({ patientId, enabled, linkedTaskId, onChanged }: {
  patientId: string; enabled: boolean; linkedTaskId: string | null; onChanged: () => void;
}) {
  const [params, setParams] = useSearchParams();
  const requestId = params.get('task_request');
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [attempt, setAttempt] = useState(0);
  const [cursors, setCursors] = useState<string[]>([]);
  const [resource, setResource] = useState<{ data: Resources | null; loading: boolean; error: Error | null }>(
    { data: null, loading: true, error: null });
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [policy, setPolicy] = useState('');
  const [identity, setIdentity] = useState<PersonalTaskIdentity | null>(null);
  const [intent, setIntent] = useState<IntentView | null>(null);
  const [request, setRequest] = useState<PersonalTaskRequest | null>(null);
  const [saved, setSaved] = useState<{ receipt: PersonalTaskReceipt; current: PersonalTaskView } | null>(null);
  const [absent, setAbsent] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const mounted = useRef(true);
  const automatic = useRef<string | null>(null);
  const reading = useRef<AbortController | null>(null);
  const frozen = !!requestId;
  const data = visible ? resource.data : null;
  const editable = enabled && !!data && visible && !busy && !frozen;

  useEffect(() => {
    mounted.current = true;
    const changed = () => { const current = document.visibilityState === 'visible'; setVisible(current);
      if (!current) { reading.current?.abort(); setRequest(null); setSaved(null); automatic.current = null; } };
    document.addEventListener('visibilitychange', changed);
    return () => { mounted.current = false; reading.current?.abort(); document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ data: null, loading: visible, error: null });
    if (visible) void Promise.all([searchPersonalTasks(patientId, cursors.at(-1), controller.signal),
      personalTaskOptions(patientId, controller.signal), linkedTaskId ? readPersonalTask(linkedTaskId, patientId, controller.signal) : Promise.resolve(null)])
      .then(([list, options, selected]) => { if (!controller.signal.aborted) setResource({ data: { list, options, selected }, loading: false, error: null }); }, failure => {
        if (!(failure instanceof Error)) throw failure;
        if (!controller.signal.aborted) setResource({ data: null, loading: false, error: failure });
      });
    return () => controller.abort();
  }, [patientId, linkedTaskId, cursors, visible, attempt]);

  function run(operation: () => Promise<void>) {
    if (!visible || busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(null);
    void operation().then(undefined, failure => { if (!(failure instanceof Error)) throw failure;
      if (mounted.current && document.visibilityState === 'visible') {
        if (failure instanceof ApiError && failure.status === 404 && failure.code === 'PERSONAL_RECORD_TASK_REQUEST_NOT_FOUND') {
          setAbsent(true); setIdentity(null); setIntent(null);
        } else setError(failure);
      }
    }).finally(() => { busyRef.current = false; if (mounted.current) setBusy(false); });
  }
  function routeFromUrl(): Pick<PersonalTaskIdentity, 'operation_id' | 'target_key'> {
    const operation = params.get('task_operation');
    const target = params.get('task_target');
    if (!target || !['create', 'change'].includes(operation ?? '')) throw new Error('本人记录任务原请求缺少准确操作和目标。');
    if (operation === 'create' && target !== patientId) throw new Error('本人记录任务原请求不属于当前患儿。');
    return operation === 'create' ? { operation_id: 'personal_record_task_create', target_key: `/patients/${target}/personal-record-tasks` }
      : { operation_id: 'personal_record_task_change', target_key: `/personal-record-tasks/${target}/status` };
  }
  async function adopt(receipt: PersonalTaskReceipt, currentIdentity: PersonalTaskIdentity, notify = false) {
    const current = await readPersonalTask(receipt.resource_id, patientId);
    if (BigInt(current.version) < BigInt(receipt.version)) throw new Error('本人记录任务重新读取的版本早于保存回执。');
    if ('title' in currentIdentity.payload) {
      if (current.title !== currentIdentity.payload.title || current.description !== currentIdentity.payload.description
          || current.retention_policy_id !== currentIdentity.payload.retention_policy_id) throw new Error('本人记录任务保存内容与重新读取结果不一致。');
    } else if (current.status !== (currentIdentity.payload.action === 'complete' ? 'completed' : 'cancelled'))
      throw new Error('本人记录任务的保存状态与当前记录不一致。');
    if (mounted.current && document.visibilityState === 'visible') {
      setSaved({ receipt, current }); setAttempt(value => value + 1); if (notify) onChanged();
    }
  }
  function inspect() { run(async () => {
    if (!requestId) throw new Error('本人记录任务原请求身份不存在。');
    const expected = routeFromUrl();
    const controller = new AbortController(); reading.current = controller; setAbsent(false); setRequest(null); setSaved(null);
    const result = await readPersonalTaskRequest(requestId, expected.operation_id, expected.target_key, controller.signal);
    if (controller.signal.aborted || !mounted.current) return;
    const value: PersonalTaskIdentity = { ...expected, request_id: result.request_id, schema_version: '2.0.0', payload: result.record_task_input };
    setIdentity(value); setRequest(result);
    setIntent({ intent_id: result.intent_id, request_id: result.request_id, generation: result.generation,
      expires_at: result.expires_at, payload_sha256: result.payload_sha256,
      status: result.state === 'committed' ? 'committed' : result.state === 'failed' ? 'failed' : 'prepared' });
    if ('title' in value.payload) { setTitle(value.payload.title); setDescription(value.payload.description ?? ''); setPolicy(value.payload.retention_policy_id); }
    if (result.receipt) await adopt(result.receipt, value);
  }); }
  useEffect(() => {
    if (data && requestId && automatic.current !== requestId && !busyRef.current) { automatic.current = requestId; inspect(); }
  }, [data, requestId]);

  async function commit(value: PersonalTaskIdentity, prepared?: IntentView) {
    const original = prepared ?? await preparePersonalTask(value);
    if (mounted.current) setIntent(original);
    if (original.status !== 'prepared' && original.status !== 'committed' || Date.parse(original.expires_at) <= Date.now() && original.status !== 'committed')
      throw new Error('本人记录任务原请求需要重新核验。');
    await adopt(await submitPersonalTask(value, original), value, true);
  }
  function save(task?: PersonalTaskView, action?: 'complete' | 'cancel') { run(async () => {
    if (!editable || !data) throw new Error('当前本人记录任务无法保存。');
    const payload = task ? { expected_version: task.version, action: action! }
      : { title, description: description === '' ? null : description, retention_policy_id: policy };
    if (task ? !action || !task.allowed_actions.includes(action) : !policy || !data.options.items.some(row => row.policy_id === policy))
      throw new Error('本人记录任务的当前操作或保留政策尚未核验。');
    const value: PersonalTaskIdentity = { operation_id: task ? 'personal_record_task_change' : 'personal_record_task_create',
      target_key: task ? `/personal-record-tasks/${task.task_id}/status` : `/patients/${patientId}/personal-record-tasks`,
      request_id: crypto.randomUUID(), schema_version: '2.0.0', payload };
    setIdentity(value); setSaved(null); setRequest(null); automatic.current = value.request_id;
    setParams(current => { const next = new URLSearchParams(current); next.set('task_request', value.request_id);
      next.set('task_operation', task ? 'change' : 'create'); next.set('task_target', task?.task_id ?? patientId); return next; }, { replace: true });
    await commit(value);
  }); }
  function continueRequest(retry: boolean) { run(async () => {
    if (!enabled || !identity || !request || request.state !== 'pending' && !request.may_retry)
      throw new Error('本人记录任务原请求当前无法继续。');
    if (retry ? !request.may_retry : request.may_retry || !intent) throw new Error('本人记录任务原请求需要重新核验。');
    await commit(identity, retry ? await retryPersonalTask(request) : intent!);
  }); }
  function cancelRequest() { run(async () => {
    if (!enabled || !request || ['committed', 'unknown'].includes(request.state)) throw new Error('当前原请求无法取消。');
    const value = await cancelPersonalTaskRequest(request);
    if (mounted.current && document.visibilityState === 'visible') setRequest(value);
  }); }
  function clearRequest() {
    if (busy || !saved && !absent && request?.error_code !== 'PERSONAL_RECORD_TASK_INTENT_CANCELLED') throw new Error('本人记录任务原请求结果尚未明确。');
    setParams(current => { const next = new URLSearchParams(current); ['task_request', 'task_operation', 'task_target'].forEach(key => next.delete(key)); return next; }, { replace: true });
    setIdentity(null); setIntent(null); setRequest(null); setSaved(null); setAbsent(false); setError(null); automatic.current = null;
  }
  function taskRow(task: PersonalTaskView) { return <div key={task.task_id} className="status-panel" role="status">
    <h3>{task.title}</h3>{task.description && <p>{task.description}</p>}<p>{statuses[task.status]} · 版本 {task.version}</p>
    <p>保存时间：{task.updated_at}</p><div className="two-buttons">
      <button type="button" className="button secondary" disabled={!editable || !task.allowed_actions.includes('complete')} onClick={() => save(task, 'complete')}>完成记录任务</button>
      <button type="button" className="button secondary" disabled={!editable || !task.allowed_actions.includes('cancel')} onClick={() => save(task, 'cancel')}>取消记录任务</button></div></div>; }

  return <section className="card form-card"><h2>本人记录任务</h2>
    <UI2ReadState loading={resource.loading} error={resource.error} onRetry={() => setAttempt(value => value + 1)} />
    {data?.selected && taskRow(data.selected)}
    {data && <><details><summary>创建本人明确设置的记录任务</summary>
      <label className="field"><span>任务名称</span><input aria-label="本人记录任务名称" maxLength={200} autoComplete="off" value={visible ? title : ''} disabled={!editable} onChange={event => setTitle(event.target.value)} /></label>
      <label className="field"><span>本人填写的说明</span><textarea aria-label="本人记录任务说明" maxLength={4000} value={visible ? description : ''} disabled={!editable} onChange={event => setDescription(event.target.value)} /></label>
      <label className="field"><span>资料保留政策</span><select aria-label="本人记录任务保留政策" value={policy} disabled={!editable || data.options.capability_state !== 'available'} onChange={event => setPolicy(event.target.value)}>
        <option value="" disabled>请选择当前获准政策</option>{data.options.items.map(row => <option key={row.policy_id} value={row.policy_id}>{row.policy_key} · 版本 {row.version}</option>)}</select></label>
      {data.options.capability_reason && <p role="status">{data.options.capability_reason}</p>}
      <button type="button" className="button" disabled={!editable || !title.trim() || !policy} onClick={() => save()}>保存本人记录任务</button></details>
      {data.list.items.filter(row => row.task_id !== linkedTaskId).map(taskRow)}{!data.list.items.length && !data.selected && <p>当前没有本人记录任务。</p>}
      <div className="two-buttons"><button type="button" className="button secondary" disabled={busy || frozen || !cursors.length} onClick={() => setCursors(value => value.slice(0, -1))}>上一页记录任务</button>
        <button type="button" className="button secondary" disabled={busy || frozen || !data.list.next_cursor} onClick={() => setCursors(value => [...value, data.list.next_cursor!])}>下一页记录任务</button></div></>}
    {visible && error && <p role="alert">{error.message}</p>}
    {visible && frozen && !saved && <section className="status-panel pending" role="status"><h3>本人记录任务原请求</h3><p>{absent ? '原请求没有准备或提交。' : request ? `${request.state}；${request.error_code ?? '原请求结果已经读取。'}` : '原请求结果待核验。'}</p>
      <button type="button" className="text-action" disabled={busy} onClick={inspect}>查询本人任务原请求</button>
      {request?.state === 'pending' && !request.may_retry && <button type="button" className="button secondary" disabled={!enabled || busy} onClick={() => continueRequest(false)}>继续提交原本人任务请求</button>}
      {request?.may_retry && <button type="button" className="button secondary" disabled={!enabled || busy} onClick={() => continueRequest(true)}>重新提交原本人任务请求</button>}
      {request && !['committed', 'unknown'].includes(request.state) && request.error_code !== 'PERSONAL_RECORD_TASK_INTENT_CANCELLED'
        && <button type="button" className="text-action" disabled={!enabled || busy} onClick={cancelRequest}>取消未提交的本人任务请求</button>}
      {(absent || request?.error_code === 'PERSONAL_RECORD_TASK_INTENT_CANCELLED') && <button type="button" className="text-action" disabled={busy} onClick={clearRequest}>更正本人任务输入</button>}</section>}
    {visible && saved && <section className="status-panel success" role="status"><h3>本人记录任务已经保存</h3><p>原保存版本 {saved.receipt.version}；当前版本 {saved.current.version}；{statuses[saved.current.status]}</p>
      <button type="button" className="text-action" disabled={busy} onClick={clearRequest}>继续查看本人记录任务</button></section>}
  </section>;
}
