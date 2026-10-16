import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '../../api/client';
import { assertFamilyContentSelection, familyContentTargetKey, isFamilyContentId, listFamilyContentRequests, readFamilyContentOptions,
  readFamilyContentRequest, submitFamilyContentRequest } from '../../api/familyContentDisposition';
import type { FamilyContentOptions, FamilyContentRequest, FamilyContentRequestInput } from '../../api/familyContentDisposition';
import { useAccess } from '../../state/access';
import type { AccessContextValue } from '../../state/access';

const keys = { client: 'content_request_client', server: 'content_request_id', actor: 'content_request_actor', patient: 'content_request_patient' };
const statuses: Record<FamilyContentRequest['status'], string> = {
  requested: '已提交，等待审查', preview: '正在核对处置范围', approved: '已批准，尚不表示处置完成', retained: '按政策继续保留',
  access_revoked: '已撤销相应访问', erasing: '正在处理选定内容', logical_erasure_complete: '已登记逻辑内容擦除完成',
};
const modes = { retain: '继续保留', revoke_access: '撤销访问', erase_selected_content: '擦除选定内容' };
const clearMarker = (params: URLSearchParams) => { const next = new URLSearchParams(params); Object.values(keys).forEach(key => next.delete(key)); return next; };

/** A separate request-only panel. Its lifetime is the current, verified family scope. */
export function FamilyContentDispositionTools() {
  const access = useAccess(), [params, setParams] = useSearchParams();
  const [deniedScope, setDeniedScope] = useState<string | null>(null);
  const scope = JSON.stringify([access.principalId, access.patientId, access.scopeVersion, 'family_observation']);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const relation = access.relations.find(value => value.patient_id === access.patientId && value.status === 'active');
  const permitted = !!relation?.actions.includes('request_disposition');
  const current = !!access.principalId && !!access.patientId && !access.loading && !access.error && permitted
    && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  useEffect(() => {
    const hide = () => setVisible(false);
    const visibility = () => setVisible(document.visibilityState === 'visible');
    window.addEventListener('pagehide', hide); window.addEventListener('pageshow', visibility);
    document.addEventListener('visibilitychange', visibility);
    return () => { window.removeEventListener('pagehide', hide); window.removeEventListener('pageshow', visibility); document.removeEventListener('visibilitychange', visibility); };
  }, []);
  const markerActor = params.get(keys.actor), markerPatient = params.get(keys.patient);
  const markerMismatch = !!(markerActor && access.principalId && markerActor !== access.principalId
    || markerPatient && access.patientId && markerPatient !== access.patientId);
  useEffect(() => {
    // Only UUID recovery markers survive hiding/reverification. A different
    // patient/account must never send the previous identity into its API scope.
    if (markerMismatch || !access.loading && !access.principalId && Object.values(keys).some(key => params.has(key)))
      setParams(clearMarker, { replace: true });
  }, [markerMismatch, access.loading, access.principalId, params, setParams]);
  if (deniedScope === scope) return <section role="alert"><p>内容处置访问核验未通过，已清除本地资料与选择。</p>
    <button type="button" className="text-action" disabled={access.loading} onClick={() => { setDeniedScope(null); void access.refresh(); }}>重新核验内容处置权限</button></section>;
  if (!visible || !current || markerMismatch) return <button type="button" className="button secondary" disabled>申请处理临床记录内容</button>;
  return <FamilyContentDispositionPanel key={scope} access={access} onDenied={() => setDeniedScope(scope)} />;
}

function FamilyContentDispositionPanel({ access, onDenied }: { access: AccessContextValue; onDenied: () => void }) {
  const [params, setParams] = useSearchParams();
  const clientId = params.get(keys.client), serverId = params.get(keys.server);
  const hasMarker = Object.values(keys).some(key => params.has(key));
  const markerValid = !hasMarker || isFamilyContentId(clientId) && (!serverId || isFamilyContentId(serverId))
    && params.get(keys.actor) === access.principalId && params.get(keys.patient) === access.patientId;
  const [opened, setOpened] = useState(hasMarker), [reload, setReload] = useState(0);
  const [options, setOptions] = useState<FamilyContentOptions | null>(null), [history, setHistory] = useState<FamilyContentRequest[]>([]);
  const [value, setValue] = useState<FamilyContentRequest | null>(null), [policyId, setPolicyId] = useState(''), [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const expected = useRef<FamilyContentRequestInput | null>(null);
  const queued = useRef<FamilyContentRequestInput | null>(null), operation = useRef<AbortController | null>(null);
  const running = useRef(false), mounted = useRef(false);
  const identity = useRef({ principal: access.principalId, patient: access.patientId, scope: access.scopeVersion });
  const accessRef = useRef(access); accessRef.current = access;
  const current = () => mounted.current && document.visibilityState === 'visible' && !accessRef.current.loading && !accessRef.current.error
    && accessRef.current.hasCurrentAccess(identity.current.principal, identity.current.patient, identity.current.scope)
    && accessRef.current.relations.some(relation => relation.patient_id === identity.current.patient && relation.status === 'active' && relation.actions.includes('request_disposition'));
  const patientId = access.patientId!;
  function saveMarker(client: string, server?: string) {
    setParams(before => { const next = clearMarker(before); next.set(keys.client, client); next.set(keys.actor, access.principalId!);
      next.set(keys.patient, patientId); if (server) next.set(keys.server, server); return next; }, { replace: true });
  }
  useEffect(() => {
    mounted.current = true;
    const abort = () => { operation.current?.abort(); queued.current = null; expected.current = null; };
    const hidden = () => { if (document.visibilityState !== 'visible') abort(); };
    window.addEventListener('pagehide', abort); document.addEventListener('visibilitychange', hidden);
    return () => { mounted.current = false; abort(); window.removeEventListener('pagehide', abort); document.removeEventListener('visibilitychange', hidden); };
  }, []);
  useEffect(() => {
    if (!opened || !current()) return;
    setSelected([]); setPolicyId(''); setOptions(null); setHistory([]); setValue(null); setError('');
    if (!markerValid) { setError('原申请恢复标识无效或与当前账号、患儿不一致；未读取或重新提交。'); return; }
    const controller = new AbortController(); operation.current = controller; running.current = true; setBusy(true);
    const guard = { signal: controller.signal, isCurrent: current };
    // A POST starts only after its non-clinical recovery marker is in the route.
    // Removing the queued payload before transport makes effects/reloads GET-only.
    const input = queued.current?.request_id === clientId ? queued.current : null;
    queued.current = null;
    const recover = async () => {
      if (clientId && serverId) {
        try { return await readFamilyContentRequest(patientId, serverId, clientId, guard); }
        catch (failure) { if (!(failure instanceof ApiError) || failure.status !== 404) throw failure; }
      }
      const list = await listFamilyContentRequests(patientId, guard);
      if (!controller.signal.aborted && current()) setHistory(list.requests);
      const original = clientId ? list.requests.find(request => request.client_request_id === clientId) ?? null : null;
      if (original && serverId && original.request_id !== serverId) throw new Error('原申请的服务端标识不一致，未采用其他申请。');
      return original;
    };
    void (async () => {
      let saved: FamilyContentRequest | null = null;
      if (input) {
        try { saved = await submitFamilyContentRequest(patientId, input, guard); }
        catch (failure) {
          if (controller.signal.aborted || !current()) return;
          if (failure instanceof ApiError && [401, 403].includes(failure.status)) throw failure;
          // Never infer failure from a lost response, malformed receipt, or an
          // empty list. Preserve this identity and immediately query it by GET.
          setError('本次提交结果尚未确认，正在核对原请求。');
          saved = await recover();
        }
      } else if (clientId) saved = await recover();
      else {
        const [choices, list] = await Promise.all([readFamilyContentOptions(patientId, guard), listFamilyContentRequests(patientId, guard)]);
        if (controller.signal.aborted || !current()) return;
        setOptions(choices); setHistory(list.requests);
      }
      if (controller.signal.aborted || !current()) return;
      if (saved && expected.current?.request_id === clientId) assertFamilyContentSelection(saved, expected.current);
      if (saved) { setValue(saved); setError(''); if (saved.request_id !== serverId) saveMarker(saved.client_request_id, saved.request_id); }
    })().catch(failure => {
      if (controller.signal.aborted || !current()) return;
      setOptions(null); setHistory([]); setValue(null); setSelected([]); setPolicyId('');
      setError(failure instanceof Error ? failure.message : '暂时无法核对内容处置申请。');
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) { onDenied(); void accessRef.current.refresh(); }
    }).finally(() => {
      if (operation.current === controller) { running.current = false; if (!controller.signal.aborted && current()) setBusy(false); }
    });
    return () => controller.abort();
  }, [opened, reload, clientId, serverId, markerValid]);

  function submit() {
    if (!current() || running.current || queued.current || hasMarker || !options || !policyId || !selected.length || selected.length > 100) return;
    if (!options.policies.some(policy => policy.policy_id === policyId)) { setError('所选政策已改变，请重新读取。'); return; }
    const targets = options.targets.filter(target => selected.includes(familyContentTargetKey(target)));
    if (targets.length !== selected.length) { setError('所选记录版本已改变，请重新读取。'); return; }
    const input: FamilyContentRequestInput = { schema_version: 'content-disposition-family-request-1', request_id: crypto.randomUUID(), policy_id: policyId,
      targets: targets.map(({ record_version_id, version }) => ({ record_version_id, version })) };
    expected.current = input; queued.current = input; running.current = true; setBusy(true); setSelected([]); setPolicyId('');
    saveMarker(input.request_id);
  }
  const uncertain = hasMarker && !value;
  return <><button type="button" className="button secondary" disabled={busy} onClick={() => setOpened(true)}>申请处理临床记录内容</button>
    {opened && <section className="card form-card" aria-label="临床记录内容处置申请" style={{ overflowWrap: 'anywhere' }}>
      <h2>临床记录内容处置申请</h2>
      <p role="note">请明确选择已批准政策和准确记录版本。本入口只提交申请，不批准政策，也不执行擦除。文件和用途副本的申请在上方独立办理。</p>
      {error && <p role="alert">{error}</p>}
      {busy && <p role="status">正在核对当前权限内的资料与原申请</p>}
      {uncertain && <p role="status">申请结果待确认。尚未查到原请求不代表提交失败；请稍后继续查询原申请，不能重复建立新请求。</p>}
      {options && !hasMarker && <>
        <label className="field"><span>内容处置政策</span><select value={policyId} disabled={busy} onChange={event => { setPolicyId(event.target.value); setSelected([]); }}>
          <option value="">请选择已批准政策，不自动选择</option>{options.policies.map(policy => <option key={policy.policy_id} value={policy.policy_id}>
            {modes[policy.mode]} · 版本 {policy.version} · {policy.policy_id}</option>)}</select></label>
        {!options.policies.length && <p>当前没有可申请的已批准政策。</p>}
        {policyId && <fieldset disabled={busy}><legend>选择准确记录版本（最多 100 项）</legend>{options.targets.map(target => {
          const key = familyContentTargetKey(target);
          return <label className="check-line" key={key}><input type="checkbox" checked={selected.includes(key)}
            onChange={event => setSelected(before => event.target.checked ? [...new Set([...before, key])] : before.filter(item => item !== key))} />
            记录 {target.record_id} · 版本 {target.version} · 版本标识 {target.record_version_id} · 登记时间 {target.recorded_at}</label>;
        })}</fieldset>}
        {!options.targets.length && <p>当前没有可申请处理的家庭可读记录版本。</p>}
        <button type="button" className="button" disabled={busy || !policyId || !selected.length || selected.length > 100} onClick={submit}>提交临床内容处置申请</button>
      </>}
      {value && <article aria-label="已核对的内容处置申请"><p role="status">当前状态：{statuses[value.status]}。</p>
        <p>申请标识 {value.request_id}；提交时间 {value.created_at}；政策 {value.policy_id}。</p>
        {value.targets.map(target => <p key={familyContentTargetKey(target)}>记录版本 {target.record_version_id} · 版本 {target.version}</p>)}
        <p>{value.logical_payload_erased ? '已登记选定逻辑内容擦除完成。' : '尚未登记逻辑内容擦除完成。'}所有副本均已擦除：否（all_copies_erased=false）。备份、审计及其他副本仍须分别核对。</p>
        <button type="button" className="text-action" disabled={busy} onClick={() => { if (current()) { setValue(null); expected.current = null; setParams(clearMarker, { replace: true }); } }}>填写另一份内容处置申请</button>
      </article>}
      {!hasMarker && history.length > 0 && <details><summary>本人已提交的内容处置申请（{history.length}）</summary>{history.map(request =>
        <article key={request.request_id}><p>{request.created_at} · {statuses[request.status]} · {request.targets.length} 个准确版本</p>
          <button type="button" className="text-action" disabled={busy} onClick={() => { if (current()) saveMarker(request.client_request_id, request.request_id); }}>查看申请 {request.request_id}</button></article>)}</details>}
      <button type="button" className="text-action" disabled={busy || !markerValid} onClick={() => { if (current()) setReload(before => before + 1); }}>{hasMarker ? '查询原内容处置申请' : '刷新政策、记录版本与申请'}</button>
    </section>}</>;
}
