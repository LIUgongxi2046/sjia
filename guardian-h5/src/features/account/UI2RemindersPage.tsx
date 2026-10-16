import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ArrowDown as ChevronDown, ShieldCheck } from '../../components/ThreeDIcon';
import { Bell, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { PersonalRecordTaskTools } from './PersonalRecordTaskTools';
import { MaterialReminderNotices } from './MaterialReminderNotices';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useReminders } from '../../api/useReminders';
import { ApiError } from '../../api/client';
import { validateApiValue } from '../../api/schema';
import { cancelReminderRequest, findReminderRequest, prepareReminderRequest, readReminder, readReminderRequest, reminderClock, reminderInstant,
  reminderLocal, reminderPayloadMatches, retryReminderRequest, submitReminderRequest } from '../../api/materialReminders';
import type { LocalTimeChoice } from '../../api/materialReminders';
import type { IntentView } from '../../../../frontend-design/types';
import type { ReminderChangeInput, ReminderInput, ReminderIntentInput, ReminderTarget, ReminderTargetView,
  ReminderView, RequestView, WriteReceipt } from '../../../../frontend-design/v2-types';

const targetTypes: [ReminderTarget, string][] = [['batch', '已产生的报告'], ['draft', '未完成的草稿'], ['record_task', '本人记录任务']];
const statuses = { enabled: '已经启用', disabled: '已经关闭', paused: '已经暂停', completed: '已经完成', cancelled: '已经取消' };
const targetStates = { pending: '待处理', completed: '已经完成', cancelled: '已经取消', changed: '版本已有更新', unavailable: '当前不可用' };
const formTypes = { observation: '观察记录', report: '报告资料', therapy: '治疗资料', correction: '更正申请' };
interface Form { version: string | null; enabled: boolean; due: string; timezone: string; quietStart: string; quietEnd: string;
  postpone: string; choice: LocalTimeChoice }
function blankForm(): Form { return { version: null, enabled: false, due: '', timezone: '', quietStart: '', quietEnd: '', postpone: '', choice: 'reject' }; }
function formFrom(value: ReminderView): Form {
  return { version: value.version, enabled: value.status === 'enabled', due: reminderLocal(value.due_at, value.timezone),
    timezone: value.timezone, quietStart: value.quiet_start ?? '', quietEnd: value.quiet_end ?? '', postpone: '', choice: 'reject' };
}
function timeText(value: string | null, timezone: string): string { return value === null ? '尚无计划时间' : `${reminderLocal(value, timezone)}（${timezone}）`; }

export function UI2RemindersPage() {
  const access = useAccess();
  return <RemindersContent key={JSON.stringify([access.principalId, access.patientId])} />;
}

function RemindersContent() {
  const access = useAccess();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const reminderId = params.get('reminder');
  const intentId = params.get('request');
  const clientId = params.get('client_request');
  const [type, setType] = useState<ReminderTarget>('batch');
  const [target, setTarget] = useState<ReminderTargetView | null>(null);
  const [form, setForm] = useState<Form>(blankForm);
  const [listCursors, setListCursors] = useState<string[]>([]);
  const [targetCursors, setTargetCursors] = useState<string[]>([]);
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const resource = useReminders(canRead ? access.principalId : null, access.patientId, type, reminderId,
    listCursors.at(-1), targetCursors.at(-1));
  const data = canRead ? resource.data : null;
  const selected = data?.selected ?? null;
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  const canWrite = !!data && !!access.csrfToken && relation?.status === 'active' && relation.actions.includes('manage_draft')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  const [identity, setIdentity] = useState<ReminderIntentInput | null>(null);
  const [intent, setIntent] = useState<IntentView | null>(null);
  const [request, setRequest] = useState<RequestView | null>(null);
  const [saved, setSaved] = useState<{ receipt: WriteReceipt; current: ReminderView } | null>(null);
  const [absent, setAbsent] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const mounted = useRef(true);
  const composing = useRef(false);
  const initialized = useRef<string | null>(null);
  const automatic = useRef<string | null>(null);
  const frozen = !!identity || !!intentId || !!clientId || !!saved;
  const versionCurrent = !selected || form.version === selected.version;
  const edit = canWrite && !busy && !frozen && versionCurrent && (!selected || selected.allowed_actions.includes('configure'));

  useEffect(() => {
    if (!selected || initialized.current === selected.reminder_id || frozen) return;
    let current = true;
    initialized.current = selected.reminder_id;
    void Promise.resolve().then(() => formFrom(selected)).then(value => {
      if (current && mounted.current) { setType(selected.target_type); setTarget(null); setForm(value); }
    }, failure => { if (!(failure instanceof Error)) throw failure; if (current && mounted.current) setError(failure); });
    return () => { current = false; };
  }, [selected, frozen]);
  useEffect(() => {
    mounted.current = true;
    const changed = () => { if (document.visibilityState !== 'visible') {
      pending.current?.abort(); setSaved(null); setRequest(null); automatic.current = null;
    } };
    document.addEventListener('visibilitychange', changed);
    return () => { mounted.current = false; pending.current?.abort(); document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => { if (!canRead) { pending.current?.abort(); setSaved(null); setRequest(null); automatic.current = null; } }, [canRead]);

  function run(operation: (controller: AbortController) => Promise<void>) {
    if (!canRead || busyRef.current) return;
    const controller = new AbortController(); pending.current = controller;
    busyRef.current = true; setBusy(true); setError(null);
    void operation(controller).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (mounted.current && !controller.signal.aborted) {
        if (failure instanceof ApiError && failure.status === 404 && failure.code === 'REMINDER_REQUEST_NOT_FOUND') {
          setAbsent(true); setIdentity(null); setIntent(null);
        } else setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) void access.refresh();
        if (failure instanceof ApiError && failure.status === 409) resource.reload();
      }
    }).finally(() => { if (pending.current === controller) {
      busyRef.current = false; pending.current = null; if (mounted.current) setBusy(false);
    } });
  }
  function update(value: Form) { if (!edit) throw new Error('当前提醒设置不具备编辑条件。'); setForm(value); setError(null); }

  function requestTarget(): { operation: ReminderIntentInput['operation_id']; path: string } {
    const operation = params.get('operation');
    const id = params.get('request_target');
    if (!id || !['create', 'change'].includes(operation ?? '')) throw new Error('原提醒请求缺少准确操作和目标身份。');
    validateApiValue('v2-protocol.schema.json#/$defs/Uuid', id);
    if (operation === 'create' && id !== access.patientId) throw new Error('原提醒请求不属于当前患儿。');
    if (operation === 'change' && id !== reminderId) throw new Error('原提醒请求不属于当前提醒设置。');
    return operation === 'create' ? { operation: 'material_reminder_create', path: `/patients/${id}/material-reminders` }
      : { operation: 'material_reminder_change', path: `/material-reminders/${id}/settings` };
  }
  async function adopt(receipt: WriteReceipt, payload: ReminderInput | ReminderChangeInput, controller: AbortController) {
    if (!access.patientId) throw new Error('尚未取得当前患儿身份。');
    const current = await readReminder(receipt.resource_id, access.patientId, controller.signal);
    if (BigInt(current.version) < BigInt(receipt.version)) throw new Error('重新读取的提醒版本早于原保存回执。');
    if (current.version === receipt.version && !reminderPayloadMatches(current, payload)) throw new Error('提醒回执与重新读取的设置不一致，保存结果需要核验。');
    if (mounted.current && !controller.signal.aborted) {
      setForm(formFrom(current)); setType(current.target_type); setSaved({ receipt, current }); resource.reload();
    }
  }
  function inspect() {
    run(async controller => {
      const expected = requestTarget();
      setSaved(null); setRequest(null); setAbsent(false);
      const value = intentId ? await readReminderRequest(intentId, controller.signal)
        : clientId ? await findReminderRequest(clientId, expected.operation, expected.path, controller.signal) : null;
      if (!value || value.operation_id !== expected.operation || value.target_key !== expected.path || !value.reminder_input
          || !value.generation || !value.expires_at || !value.payload_sha256) throw new Error('原提醒请求与当前保存操作不一致。');
      if (controller.signal.aborted || !mounted.current) return;
      const payload = value.reminder_input;
      if (payload.due_at !== null && payload.timezone !== undefined && payload.quiet_start !== undefined && payload.quiet_end !== undefined) {
        setForm({ version: 'expected_version' in payload ? payload.expected_version : null,
          enabled: payload.enabled ?? selected?.status === 'enabled', due: reminderLocal(payload.due_at, payload.timezone),
          timezone: payload.timezone, quietStart: payload.quiet_start ?? '', quietEnd: payload.quiet_end ?? '', postpone: '', choice: 'reject' });
        if ('target_type' in payload) setType(payload.target_type);
      }
      setRequest(value); setIdentity({ operation_id: expected.operation, target_key: value.target_key,
        request_id: value.request_id, schema_version: '2.0.0', payload });
      setIntent({ intent_id: value.intent_id, request_id: value.request_id, generation: value.generation,
        expires_at: value.expires_at, payload_sha256: value.payload_sha256,
        status: value.state === 'committed' ? 'committed' : value.state === 'failed' ? 'failed' : 'prepared' });
      if (value.receipt) await adopt(value.receipt, payload, controller);
    });
  }
  useEffect(() => {
    const lookup = intentId ? `intent:${intentId}` : clientId ? `request:${clientId}` : null;
    if (canRead && data && lookup && automatic.current !== lookup && !busyRef.current) { automatic.current = lookup; inspect(); }
  }, [canRead, data, intentId, clientId]);

  async function savePayload(payload: ReminderInput | ReminderChangeInput, controller: AbortController) {
    if (!access.patientId) throw new Error('当前患儿身份尚未通过核验。');
    const operation = reminderId ? 'material_reminder_change' as const : 'material_reminder_create' as const;
    const path = reminderId ? `/material-reminders/${reminderId}/settings` : `/patients/${access.patientId}/material-reminders`;
    const current = identity ?? { operation_id: operation, target_key: path, request_id: crypto.randomUUID(), schema_version: '2.0.0' as const, payload };
    setIdentity(current); automatic.current = `request:${current.request_id}`;
    setParams(value => { const next = new URLSearchParams(value); next.set('client_request', current.request_id);
      next.set('operation', operation === 'material_reminder_create' ? 'create' : 'change'); next.set('request_target', reminderId ?? access.patientId!); return next;
    }, { replace: true });
    const prepared = intent ?? await prepareReminderRequest(current, controller.signal);
    if (controller.signal.aborted || !mounted.current) return;
    setIntent(prepared); automatic.current = `intent:${prepared.intent_id}`;
    setParams(value => { const next = new URLSearchParams(value); next.set('request', prepared.intent_id); return next; }, { replace: true });
    if (prepared.status === 'failed' || prepared.status === 'prepared' && Date.parse(prepared.expires_at) <= Date.now())
      throw new Error('原提醒请求需要重新核验，当前没有再次提交。');
    await adopt(await submitReminderRequest(current, prepared, controller.signal), current.payload, controller);
  }
  function submit() {
    run(async controller => {
      if (!canWrite) throw new Error('当前提醒权限尚未通过核验。');
      if (identity) { if (request?.state !== 'pending' || request.may_retry) throw new Error('原请求需要查询或重新核验。'); await savePayload(identity.payload, controller); return; }
      if (!edit || !data) throw new Error('当前提醒版本或编辑条件尚未通过核验。');
      const quietStart = reminderClock(form.quietStart), quietEnd = reminderClock(form.quietEnd);
      if ((quietStart === null) !== (quietEnd === null) || quietStart !== null && quietStart === quietEnd) throw new Error('请同时填写安静时段开始和结束，并使用不同时间。');
      const due = reminderInstant(form.postpone || form.due, form.timezone, form.choice);
      const settings = { due_at: due, timezone: form.timezone, quiet_start: quietStart, quiet_end: quietEnd };
      if (selected) {
        const currentTarget = data.targets.items.find(item => item.target_id === selected.target_id);
        if (!currentTarget || currentTarget.target_state !== 'pending') throw new Error('请读取包含当前目标的准确目录页面，完成或取消的目标无法重新安排。');
        const changedEnabled = form.enabled !== (selected.status === 'enabled');
        const payload: ReminderChangeInput = { expected_version: selected.version, action: 'configure', target_version: currentTarget.target_version,
          ...settings, ...(changedEnabled || form.postpone ? { enabled: form.postpone ? true : form.enabled } : {}) };
        await savePayload(validateApiValue<ReminderChangeInput>('v2-protocol.schema.json#/$defs/ReminderChangeInput', payload), controller);
      } else {
        if (!target || target.target_state !== 'pending' || data.targets.capability_state !== 'available'
            || !data.targets.items.some(item => item.target_id === target.target_id && item.target_version === target.target_version))
          throw new Error('请选择当前获准的准确报告或草稿目标。');
        if (form.postpone) throw new Error('创建提醒时请使用提醒时间；保存后可以设置延后时间。');
        await savePayload(validateApiValue<ReminderInput>('v2-protocol.schema.json#/$defs/ReminderInput',
          { ...settings, target_id: target.target_id, target_type: target.target_type, target_version: target.target_version, enabled: form.enabled }), controller);
      }
    });
  }
  function change(action: 'pause' | 'disable') {
    run(async controller => {
      if (!canWrite || !selected || !selected.allowed_actions.includes(action) || frozen || !versionCurrent) throw new Error('当前提醒不具备此项操作条件。');
      await savePayload({ expected_version: selected.version, action, due_at: null }, controller);
    });
  }
  function retry() { run(async controller => {
    if (!canWrite || !request?.may_retry || !identity) throw new Error('原提醒请求不具备重新提交条件。');
    const prepared = await retryReminderRequest(request, controller.signal);
    if (mounted.current && !controller.signal.aborted) { setIntent(prepared); setRequest(null);
      await adopt(await submitReminderRequest(identity, prepared, controller.signal), identity.payload, controller); }
  }); }
  function cancelOriginal() { run(async controller => {
    if (!canWrite || !request || ['committed', 'unknown'].includes(request.state)) throw new Error('当前提醒原请求无法取消。');
    const value = await cancelReminderRequest(request);
    if (mounted.current && !controller.signal.aborted) setRequest(value);
  }); }
  function correctOriginal() {
    if (busy || !absent && request?.error_code !== 'REMINDER_INTENT_CANCELLED') throw new Error('原提醒请求结果尚未明确。');
    setParams(reminderId ? { reminder: reminderId } : {}, { replace: true });
    setIdentity(null); setIntent(null); setRequest(null); setSaved(null); setAbsent(false); setError(null); automatic.current = null;
  }
  function chooseReminder(value: string | null) {
    if (busy || identity && !saved) throw new Error('原提醒结果明确之前不能更换操作目标。');
    setIdentity(null); setIntent(null); setRequest(null); setSaved(null); setError(null); setForm(blankForm()); setTarget(null);
    initialized.current = null; automatic.current = null;
    setParams(value ? { reminder: value } : {}, { replace: true });
  }
  const canSave = canWrite && !busy && !saved && (identity ? request?.state === 'pending' && !request.may_retry
    : edit && !!form.due && !!form.timezone && (!!selected || !!target));
  const display = data && (saved?.current ?? selected);

  return <UI2FamilyFrame title="资料补充提醒" screenId="UI2-32" note="本人自主启用，可暂停、延后和关闭。" backTo="/account">
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <section className="card reminder-main"><Bell size={47} /><h2>按你的安排，<br />继续整理资料。</h2><p>只提醒已经产生的报告或本人设置的记录任务。</p>
      <label className="switch-row"><span>启用资料补充提醒</span><input type="checkbox" role="switch" aria-label="启用资料补充提醒" checked={data ? form.enabled : false} disabled={!edit}
        onChange={event => update({ ...form, enabled: event.target.checked })} /></label></section>
    <UI2ReadState loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    <form style={{ display: 'contents' }} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onSubmit={event => { event.preventDefault(); if (!composing.current) submit(); }} onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}>
      <section className="card form-card">
        <label className="field"><span>提醒对象</span><select aria-label="提醒对象" value={type} disabled={!edit || !!selected}
          onChange={event => { setType(event.target.value as ReminderTarget); setTarget(null); setTargetCursors([]); }}>
          {targetTypes.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="field"><span>提醒时间</span><input aria-label="提醒时间" type="datetime-local" step="1" autoComplete="off" disabled={!edit} value={data ? form.due : ''}
          onChange={event => update({ ...form, due: event.target.value })} /></label>
        <label className="field"><span>时区</span><input aria-label="时区" placeholder="选择本人所在时区" autoComplete="off" disabled={!edit} value={data ? form.timezone : ''}
          onChange={event => update({ ...form, timezone: event.target.value })} /></label>
        <details><summary>安静时段与延后设置</summary><div className="field-grid">
          <label className="field"><span>安静时段开始</span><input aria-label="安静时段开始" type="time" step="1" disabled={!edit} value={data ? form.quietStart : ''} onChange={event => update({ ...form, quietStart: event.target.value })} /></label>
          <label className="field"><span>安静时段结束</span><input aria-label="安静时段结束" type="time" step="1" disabled={!edit} value={data ? form.quietEnd : ''} onChange={event => update({ ...form, quietEnd: event.target.value })} /></label></div>
          <label className="field"><span>延后至</span><input aria-label="延后至" type="datetime-local" step="1" disabled={!edit || !selected?.allowed_actions.includes('snooze')} value={data ? form.postpone : ''}
            onChange={event => update({ ...form, postpone: event.target.value, enabled: event.target.value ? true : form.enabled })} /></label>
          {data && <label className="field"><span>夏令时重复时间</span><select disabled={!edit} value={form.choice} onChange={event => update({ ...form, choice: event.target.value as LocalTimeChoice })}>
            <option value="reject">重复时间需要明确选择</option><option value="earlier">使用较早一次</option><option value="later">使用较晚一次</option></select></label>}
        </details>
      </section>
      {data && <section className="card form-card"><h2>当前获准的目标</h2>
        {data.targets.capability_state === 'unavailable' ? <p role="status">当前类别尚不可用：{data.targets.capability_reason}</p>
          : data.targets.items.length === 0 ? <p>当前没有获准的目标资料。</p>
          : <label className="field"><span>准确目标与版本</span><select aria-label="准确目标与版本" disabled={!edit || !!selected} value={selected?.target_id ?? target?.target_id ?? ''}
              onChange={event => { const item = data.targets.items.find(value => value.target_id === event.target.value); if (!item) throw new Error('所选提醒目标没有准确保存版本。'); setTarget(item); }}>
              <option value="" disabled>请选择本人已有资料</option>{data.targets.items.map(item => <option key={item.target_id} value={item.target_id}>
                {item.form_type ? formTypes[item.form_type] : item.target_type === 'record_task' ? '本人记录任务' : '报告批次'} · {item.target_id} · 版本 {item.target_version} · {targetStates[item.target_state]}</option>)}</select></label>}
        {(targetCursors.length > 0 || data.targets.next_cursor) && <div className="two-buttons">
          <button className="button secondary" type="button" disabled={busy || frozen || !targetCursors.length} onClick={() => { setTarget(null); setTargetCursors(value => value.slice(0, -1)); }}>上一页目标</button>
          <button className="button secondary" type="button" disabled={busy || frozen || !data.targets.next_cursor} onClick={() => { setTarget(null); setTargetCursors(value => [...value, data.targets.next_cursor!]); }}>下一页目标</button></div>}
        {selected?.target_type === 'draft' && selected.target_state !== 'unavailable' && <ContextLink className="text-action" to={`/drafts?draft=${selected.target_id}`}>查看目标草稿</ContextLink>}
        {selected?.target_type === 'batch' && <p>准确报告目标：{selected.target_id}</p>}
      </section>}
      {data && canRead && access.patientId && type === 'record_task' && <PersonalRecordTaskTools patientId={access.patientId}
        enabled={canWrite && !busy && !frozen} linkedTaskId={selected?.target_type === 'record_task' ? selected.target_id : null}
        onChanged={resource.reload} />}
      <div className="note blue"><ShieldCheck size={17} /><p>提醒未处理不会改变临床评估，不生成检查安排。</p></div>
      <button className="button" type="submit" disabled={!canSave}>{busy ? '正在核验提醒保存结果' : '保存提醒设置'}</button>
    </form>
    <div className="two-buttons"><button className="button secondary" disabled={!canWrite || busy || frozen || !versionCurrent || !selected?.allowed_actions.includes('pause')} onClick={() => change('pause')}>暂停提醒</button>
      <button className="button secondary" disabled={!canWrite || busy || frozen || !versionCurrent || !selected?.allowed_actions.includes('disable')} onClick={() => change('disable')}>关闭提醒</button></div>
    {display && <section className="card form-card" role="status"><h2>当前保存结果</h2><div className="pair"><span>提醒状态</span><strong>{statuses[display.status]}</strong></div>
      <div className="pair"><span>目标状态</span><strong>{targetStates[display.target_state]}</strong></div><div className="pair"><span>当前设置版本</span><strong>{display.version}</strong></div>
      <p>下次计划时间：{timeText(display.next_due_at, display.timezone)}</p><p>通知能力：{display.channel_state === 'available' ? display.channel : display.channel_reason}</p>
      <p>最近通知状态：{display.last_notice_status}</p></section>}
    {selected && !versionCurrent && !saved && <section className="status-panel stale" role="status"><h2>提醒设置已有更新</h2><p>当前输入对应版本 {form.version}，当前保存版本为 {selected.version}。</p></section>}
    {error && <section className="status-panel error" role="alert"><h2>提醒结果需要核验</h2><p>{error.message}</p></section>}
    {canRead && frozen && !saved && <section className="status-panel pending" role="status"><h2>原提醒请求结果待核验</h2><p>原请求保存结果明确之前，不能创建新的提醒请求。</p>
      <button className="text-action" disabled={busy} onClick={inspect}>查询原提醒请求</button>{request && <p>请求状态：{request.state}{request.error_code ? `；${request.error_code}` : ''}</p>}
      {request?.may_retry && canWrite && <button className="button secondary" disabled={busy} onClick={retry}>重新提交原提醒请求</button>}
      {request && !['committed', 'unknown'].includes(request.state) && request.error_code !== 'REMINDER_INTENT_CANCELLED'
        && <button className="text-action" disabled={busy || !canWrite} onClick={cancelOriginal}>取消未提交的提醒请求</button>}
      {absent && <p>原提醒请求没有准备或提交。</p>}
      {(absent || request?.error_code === 'REMINDER_INTENT_CANCELLED') && <button className="text-action" disabled={busy} onClick={correctOriginal}>更正提醒输入</button>}</section>}
    {data && saved && <section className="status-panel success" role="status"><h2>提醒设置已经保存</h2><p>本次保存版本：{saved.receipt.version}；保存时间：{saved.receipt.saved_at}</p>
      <p>{saved.current.version === saved.receipt.version ? '保存内容与重新读取的准确版本一致。' : `原保存事实已核验，当前设置对应后续版本 ${saved.current.version}。`}</p>
      <button className="text-action" disabled={busy} onClick={() => chooseReminder(saved.receipt.resource_id)}>继续查看当前提醒</button></section>}
    {data && <section className="card form-card"><h2>本人已经保存的提醒</h2>{data.reminders.items.length ? data.reminders.items.map(item => <button key={item.reminder_id} className="list-row" disabled={busy || !!identity && !saved}
      onClick={() => chooseReminder(item.reminder_id)}><span><strong>{targetTypes.find(([value]) => value === item.target_type)?.[1]}</strong><small>{statuses[item.status]} · 版本 {item.version} · {item.reminder_id}</small></span></button>) : <p>当前目录页面没有已保存的提醒。</p>}
      <div className="two-buttons"><button className="button secondary" disabled={busy || frozen || !listCursors.length} onClick={() => setListCursors(value => value.slice(0, -1))}>上一页提醒</button>
        <button className="button secondary" disabled={busy || frozen || !data.reminders.next_cursor} onClick={() => setListCursors(value => [...value, data.reminders.next_cursor!])}>下一页提醒</button></div>
      <button className="text-action" disabled={busy || !!identity && !saved} onClick={() => chooseReminder(null)}>设置另一个资料提醒</button></section>}
    {data && access.patientId && <MaterialReminderNotices key={reminderId ?? 'directory'} patientId={access.patientId}
      reminderId={reminderId} enabled={canWrite && !busy} />}
  </UI2FamilyFrame>;
}
