import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { LockKeyhole } from 'lucide-react';
import type { IntentView, RequestView, WriteReceipt } from '../../../../frontend-design/types';
import type { SupportSessionView } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { cancelPublication, preparePublication, publicationOperations, publicationRequestIdentity, readPublicationApprovalConfig, readPublicationConfig,
  readPublicationContact, readPublicationDirectory, readPublicationRelease, readPublicationRequest, readPublicationRequests,
  retryPublication, submitPublication } from '../../api/publication';
import type { ConfigDetail, ContactDetail, PublicationAction, PublicationCollection, PublicationDirectory, PublicationIdentity,
  PublicationOperation, PublicationPayload, ReleaseDetail } from '../../api/publication';
import { UI2Frame, UI2Sheet } from '../../components/UI2Frame';
import { PublicationEditor } from './PublicationEditor';
import { SupportReadStatus } from './SupportApp';
import type { SupportReadState } from './SupportApp';

const labels = { verification: '资料与核对资格', rules: '规则及窗口版本', validation: '独立病例验证', content: '家庭文字批准', contact: '联系信息有效性', notification: '接收渠道与许可' };
const collectionNames = { release: '发布版本', config: '配置版本', contact: '联系版本', source: '来源版本' };
const requestStates = { pending: '已保存提交意图', processing: '正在处理', committed: '已经保存', failed: '提交失败', unknown: '结果尚未确认', not_found: '尚未找到请求' };

export function PublicationPage({ session, accessState, accessError, onRefresh }: {
  session: SupportSessionView | null; accessState: SupportReadState; accessError: string | null; onRefresh: () => void;
}) {
  const navigate = useNavigate(); const [parameters, setParameters] = useSearchParams();
  const [directory, setDirectory] = useState<PublicationDirectory | null>(null);
  const [directories, setDirectories] = useState<Partial<Record<'config' | 'contact' | 'source', PublicationDirectory>>>({});
  const [release, setRelease] = useState<ReleaseDetail | null>(null);
  const [config, setConfig] = useState<ConfigDetail | null>(null);
  const [contact, setContact] = useState<ContactDetail | null>(null);
  const [text, setText] = useState('');
  const [sheet, setSheet] = useState<'directory' | 'editor' | 'requests' | null>(null);
  const [operation, setOperation] = useState<PublicationOperation | null>(null);
  const [request, setRequest] = useState<RequestView | null>(null);
  const [requests, setRequests] = useState<{ items: RequestView[]; next_cursor: string | null; snapshot_version: string } | null>(null);
  const [phase, setPhase] = useState<'idle' | 'loading' | 'pending' | 'unknown' | 'saved'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [absent, setAbsent] = useState(false);
  const [cancelReason, setCancelReason] = useState('');
  const controller = useRef<AbortController>(); const busy = useRef(false);
  const original = useRef<{ identity: PublicationIdentity; intent: IntentView | null } | null>(null);
  const available = session !== null && accessState === 'default';
  const actions = available ? session.capabilities.filter(item => item.state === 'available').map(item => item.capability_id) : [];
  const may = (action: PublicationAction) => actions.includes(action);
  const clear = useCallback(() => {
    controller.current?.abort(); setDirectory(null); setDirectories({}); setRelease(null); setConfig(null); setContact(null);
    setText(''); setSheet(null); setOperation(null); setRequest(null); setRequests(null); setError(null); setAbsent(false); setCancelReason(''); original.current = null;
  }, []);
  useEffect(() => {
    if (!available) clear();
    const hidden = () => { if (document.visibilityState !== 'visible') clear(); };
    document.addEventListener('visibilitychange', hidden);
    return () => { controller.current?.abort(); document.removeEventListener('visibilitychange', hidden); };
  }, [available, session, clear]);
  function failure(fault: unknown) {
    if (fault instanceof ApiError && [401, 403].includes(fault.status)) { clear(); onRefresh(); }
    setError(fault instanceof Error ? fault.message : '当前发布操作失败。');
  }
  async function run(action: (signal: AbortSignal) => Promise<void>, pending = false) {
    if (busy.current || !available) return;
    busy.current = true; controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal; setPhase(pending ? 'pending' : 'loading'); setError(null);
    try { await action(signal); if (!signal.aborted) setPhase(pending ? 'saved' : 'idle'); }
    catch (fault) { if (!signal.aborted) { setPhase(pending ? 'unknown' : 'idle'); failure(fault); } }
    finally { busy.current = false; }
  }
  function queryIdentity(identity: PublicationIdentity, intent?: IntentView) {
    const query = new URLSearchParams(parameters);
    query.set('request_id', identity.request_id); query.set('operation_id', identity.operation_id); query.set('target_key', identity.target_key);
    if (intent) query.set('intent_id', intent.intent_id); else query.delete('intent_id');
    setParameters(query, { replace: true });
  }
  async function saved(identity: PublicationIdentity, receipt: WriteReceipt, signal: AbortSignal) {
    const action = publicationOperations[identity.operation_id][0];
    if (receipt.resource_type === 'release') {
      const result = await readPublicationRelease(receipt.resource_id, signal);
      if (result.resource.version !== receipt.version) throw new Error('保存版本与重新读取的发布版本不一致。');
      if (signal.aborted) return; setRelease(result); setText(result.approved_text ?? '');
    } else if (receipt.resource_type === 'contact_version') {
      const result = await readPublicationContact(receipt.resource_id, action, signal);
      if (result.version !== receipt.version) throw new Error('保存版本与重新读取的联系版本不一致。');
      if (signal.aborted) return; setContact(result);
    } else {
      const id = receipt.resource_type === 'config' ? receipt.resource_id : identity.target_key.split('/')[4];
      const result = identity.operation_id === 'config_approval_revoke'
        ? await readPublicationApprovalConfig(id, signal) : await readPublicationConfig(id, action, signal);
      if (receipt.resource_type === 'config' && result.resource.version !== receipt.version)
        throw new Error('保存版本与重新读取的配置版本不一致。');
      if (receipt.resource_type === 'config_approval' && !result.approvals.some(row => row.id === receipt.resource_id))
        throw new Error('保存批准与准确配置记录不一致。');
      if (signal.aborted) return; setConfig(result);
    }
    if (!signal.aborted) { setSheet(null); setOperation(null); setDirectory(null); setDirectories({}); }
  }
  function submit(op: PublicationOperation, target: string, payload: PublicationPayload) {
    if (blockedWrite) return;
    const identity: PublicationIdentity = { operation_id: op, target_key: target, request_id: crypto.randomUUID(), schema_version: '1.0.0', payload };
    original.current = { identity, intent: null }; setRequest(null); setAbsent(false); queryIdentity(identity);
    void send(identity, null);
  }
  function send(identity: PublicationIdentity, intent: IntentView | null) {
    return run(async signal => {
      const prepared = intent ?? await preparePublication(identity, signal);
      if (signal.aborted) return;
      original.current = { identity, intent: prepared }; queryIdentity(identity, prepared);
      const receipt = await submitPublication(identity, prepared, signal);
      await saved(identity, receipt, signal);
      const result = await readPublicationRequest(prepared.intent_id, undefined, signal);
      if (!signal.aborted) setRequest(result);
    }, true);
  }
  function restore(intentId?: string) {
    return run(async signal => {
      const id = intentId ?? parameters.get('intent_id');
      const op = parameters.get('operation_id'), target = parameters.get('target_key'), requestId = parameters.get('request_id');
      if (!id && (!op || !(op in publicationOperations) || !target || !requestId)) throw new Error('原发布请求身份尚未完整提供。');
      let result: RequestView;
      try { result = await readPublicationRequest(id ?? requestId!, id ? undefined : { operation_id: op as PublicationOperation, target_key: target! }, signal); }
      catch (fault) {
        if (!signal.aborted && fault instanceof ApiError && fault.status === 404 && fault.code === 'INTENT_NOT_FOUND') {
          setRequest(null); setAbsent(true); setError('当前岗位范围没有对应提交意图，可以更正输入。'); return;
        }
        throw fault;
      }
      const identity = publicationRequestIdentity(result);
      if (publicationOperations[identity.operation_id][0] === 'manage_config') {
        navigate(`/support/sources?${new URLSearchParams({ intent_id: result.intent_id })}`); return;
      }
      if (signal.aborted) return;
      original.current = { identity, intent: null }; queryIdentity(identity, { intent_id: result.intent_id } as IntentView); setRequest(result); setAbsent(false);
      if (result.receipt) await saved(identity, result.receipt, signal);
    });
  }
  useEffect(() => {
    if (!available || busy.current) return;
    if (parameters.has('intent_id') || parameters.has('request_id')) void restore();
    else if (may('publish_config')) void run(async signal => {
      const value = await readPublicationDirectory('release', 'publish_config', undefined, signal);
      if (value.active_release_id) {
        const detail = await readPublicationRelease(value.active_release_id, signal);
        if (!signal.aborted) { setRelease(detail); setText(detail.approved_text ?? ''); }
      }
      if (!signal.aborted) setDirectory(value);
    });
  }, [available, session?.principal_id]);
  function list(collection: PublicationCollection, action: PublicationAction, cursor?: string) {
    void run(async signal => {
      const value = await readPublicationDirectory(collection, action, cursor, signal);
      if (cursor && (!directory || value.snapshot_version !== directory.snapshot_version)) throw new Error('目录内容已变化，请重新读取。');
      if (!signal.aborted) { setDirectory(cursor ? { ...value, items: [...directory!.items, ...value.items] } : value); setSheet('directory'); }
    });
  }
  function choose(id: string) {
    if (!directory) return;
    void run(async signal => {
      if (directory.collection === 'release') { const value = await readPublicationRelease(id, signal); if (!signal.aborted) { setRelease(value); setText(value.approved_text ?? ''); setConfig(null); setContact(null); } }
      if (directory.collection === 'config') { const value = await readPublicationConfig(id, directoryAction('config'), signal); if (!signal.aborted) { setConfig(value); setContact(null); } }
      if (directory.collection === 'contact') { const value = await readPublicationContact(id, 'manage_contacts', signal); if (!signal.aborted) { setContact(value); setConfig(null); } }
      if (!signal.aborted) setSheet(null);
    });
  }
  function directoryAction(collection: PublicationCollection): PublicationAction {
    if (collection === 'release') return 'publish_config'; if (collection === 'contact') return 'manage_contacts';
    const action = (['manage_configs', 'approve_config', 'register_validation', 'publish_config', 'manage_contacts'] as const).find(may);
    if (!action) throw new Error('当前岗位尚未取得目录权限。'); return action;
  }
  function edit(op: PublicationOperation) {
    if (blockedWrite) return;
    void run(async signal => {
      const action = publicationOperations[op][0];
      const values: Partial<Record<'config' | 'contact' | 'source', PublicationDirectory>> = {};
      values.config = await readPublicationDirectory('config', action, undefined, signal);
      if (op === 'release_create') values.contact = await readPublicationDirectory('contact', action, undefined, signal);
      if (['config_create', 'contact_create', 'config_approve', 'config_validate'].includes(op)) values.source = await readPublicationDirectory('source', action, undefined, signal);
      if (!op.endsWith('_create')) {
        if (op.startsWith('config_')) { if (!config) throw new Error('需要选择准确配置版本。'); const value = await readPublicationConfig(config.resource.id, action, signal); if (!signal.aborted) setConfig(value); }
        else if (op.startsWith('contact_')) { if (!contact) throw new Error('需要选择准确联系版本。'); const value = await readPublicationContact(contact.id, action, signal); if (!signal.aborted) setContact(value); }
        else { if (!release) throw new Error('需要选择准确发布版本。'); const value = await readPublicationRelease(release.resource.release_id, signal); if (!signal.aborted) setRelease(value); }
      }
      if (!signal.aborted) { setDirectories(values); setOperation(op); setSheet('editor'); }
    });
  }
  function moreEditor(collection: 'config' | 'contact' | 'source') {
    const current = directories[collection]; if (!operation || !current?.next_cursor) return;
    void run(async signal => {
      const value = await readPublicationDirectory(collection, publicationOperations[operation][0], current.next_cursor!, signal);
      if (value.snapshot_version !== current.snapshot_version) throw new Error('操作目录已变化，请重新读取。');
      if (!signal.aborted) setDirectories(previous => ({ ...previous, [collection]: { ...value, items: [...current.items, ...value.items] } }));
    });
  }
  function saveTextVersion() {
    if (!release || blockedWrite) return;
    void run(async signal => {
      const component = release.resource.release.components.find(item => item.kind === 'content');
      if (!component) throw new Error('当前发布没有准确文字配置。');
      const value = await readPublicationConfig(component.config_id, 'manage_configs', signal);
      if (value.resource.config.schema_version !== 'content-1') throw new Error('当前家庭文字格式不可采用。');
      const configs = await readPublicationDirectory('config', 'manage_configs', undefined, signal);
      const sources = await readPublicationDirectory('source', 'manage_configs', undefined, signal);
      if (!signal.aborted) { setConfig(value); setDirectories({ config: configs, source: sources }); setOperation('config_create'); setSheet('editor'); }
    });
  }
  function history(cursor?: string) {
    void run(async signal => {
      const result = await readPublicationRequests(cursor, signal);
      if (cursor && result.snapshot_version !== requests?.snapshot_version) throw new Error('请求目录已变化，请重新读取。');
      if (!signal.aborted) { setRequests(cursor ? { ...result, items: [...requests!.items, ...result.items] } : result); setSheet('requests'); }
    });
  }
  function continueRequest(retry: boolean) {
    if (!request) return;
    const identity = publicationRequestIdentity(request);
    if (retry) void run(async signal => {
      const intent = await retryPublication(request, signal); if (signal.aborted) return;
      original.current = { identity, intent }; queryIdentity(identity, intent);
      const receipt = await submitPublication(identity, intent, signal); await saved(identity, receipt, signal);
      const result = await readPublicationRequest(intent.intent_id, undefined, signal); if (!signal.aborted) setRequest(result);
    }, true);
    else if (request.state === 'pending' && !request.may_retry && request.expires_at && Date.parse(request.expires_at) > Date.now())
      void send(identity, { intent_id: request.intent_id, request_id: request.request_id, generation: request.generation!, status: 'prepared', expires_at: request.expires_at, payload_sha256: request.payload_sha256! });
  }
  const close = useCallback(() => setSheet(null), []);
  const blockedWrite = phase === 'unknown' || phase === 'pending' || request !== null && ['pending', 'processing', 'unknown'].includes(request.state)
    || (parameters.has('intent_id') || parameters.has('request_id')) && request === null && !absent;
  const canEnable = available && may('publish_config') && release?.may_enable && text === release.approved_text && !blockedWrite;
  const operations = (Object.keys(publicationOperations) as PublicationOperation[]).filter(op => may(publicationOperations[op][0])
    && publicationOperations[op][0] !== 'manage_config'
    && (op.endsWith('_create') || op.startsWith('config_') && config || op.startsWith('contact_') && contact || op.startsWith('release_') && release));
  function eligible(op: PublicationOperation) {
    if (op.endsWith('_create')) return true;
    if (op === 'config_approval_revoke') return config!.approvals.some(row => row.revoked_at === null);
    if (op.startsWith('config_')) return ({ config_submit: ['draft'], config_approve: ['pending'], config_validate: ['approved', 'enabled'],
      config_enable: ['approved'], config_disable: ['enabled'] } as Partial<Record<PublicationOperation, string[]>>)[op]?.includes(config!.resource.status) === true;
    if (op === 'release_approve') return release!.resource.status === 'draft';
    if (op === 'release_enable') return release!.may_enable && text === release!.approved_text;
    if (op === 'release_disable') return release!.resource.status === 'enabled' && release!.active_release_id === release!.resource.release_id;
    if (op === 'contact_activate') return ['draft', 'approved'].includes(contact!.status) && Date.parse(contact!.contact.valid_until) > Date.now();
    return op === 'contact_disable' && contact!.status !== 'disabled';
  }
  return <UI2Frame title="内容与启用" screenId="UI2-36" note="批准、验证与实际启用分别记录。" onBack={() => navigate('/support/assessment')}
    overlay={available && sheet && <UI2Sheet title={sheet === 'directory' ? '选择准确版本' : sheet === 'requests' ? '本人提交记录' : '维护当前版本'} onClose={close}>
      {error && <p role="alert">{error}</p>}
      {sheet === 'directory' && directory && <>{directory.capability_state === 'unavailable' && <p>{directory.capability_reason}</p>}
        {directory.items.map(item => <button className="row" key={item.id} disabled={busy.current} onClick={() => choose(item.id)}><span><strong>{item.title}</strong><small>版本 {item.version ?? '来源'} · {item.status} · {item.id}</small></span></button>)}
        {directory.capability_state === 'available' && !directory.items.length && <p>当前范围尚无保存版本。</p>}
        {directory.next_cursor && <button className="text-action" disabled={busy.current} onClick={() => list(directory.collection, directoryAction(directory.collection), directory.next_cursor!)}>读取更多版本</button>}</>}
      {sheet === 'editor' && operation && <>{Object.entries(directories).map(([collection, value]) => value?.next_cursor && <button key={collection} className="text-action" disabled={busy.current} onClick={() => moreEditor(collection as 'config' | 'contact' | 'source')}>读取更多{collectionNames[collection as PublicationCollection]}</button>)}
        <PublicationEditor key={`${operation}:${config?.resource.id}:${contact?.id}:${release?.resource.release_id}`} operation={operation} directories={directories}
          config={config} contact={contact} release={release} gateVersion={directories.config!.gate_version} text={text} busy={blockedWrite || phase === 'loading'} onSubmit={submit} /></>}
      {sheet === 'requests' && requests && <>{requests.items.map(item => <button className="row" key={item.intent_id} disabled={busy.current} onClick={() => void restore(item.intent_id)}><span>
        <strong>{publicationOperations[item.operation_id as PublicationOperation][3]}</strong><small>{requestStates[item.state]} · {item.prepared_at}</small></span></button>)}
        {!requests.items.length && <p>本人尚无发布、配置或联系提交记录。</p>}{requests.next_cursor && <button className="text-action" disabled={busy.current} onClick={() => history(requests.next_cursor!)}>读取更多提交</button>}</>}
    </UI2Sheet>}>
    <div className="support-banner"><LockKeyhole size={16} />受控支持 · 需独立岗位授权</div>
    <section className="card form-card"><div className="eyebrow">指定范围内操作</div><h2>内容与启用</h2><p className="muted">批准、验证与实际启用分别记录。</p>
      <div className="pair"><span>当前资料范围</span><strong>{available && release ? `${release.resource.release.environment} · 发布版本 ${release.resource.version}` : '尚未授权选择'}</strong></div></section>
    <SupportReadStatus state={accessState} error={accessError} onRefresh={onRefresh} />
    {available && actions.includes('manage_config') && <button className="button secondary" onClick={() => navigate('/support/sources')}>维护来源与保留政策</button>}
    <section className="card form-card">{Object.entries(labels).map(([key, label]) => <div className="pair" key={key}><span>{label}</span><strong>
      {available && release ? (() => { const item = release.readiness.find(row => row.key === key)!; return item.state === 'available'
        ? key === 'notification' ? '策略有效，接收许可需逐人核验' : key === 'verification' ? '配置有效，资料需逐项核验' : '当前版本有效' : item.reason; })() : '待确认'}
    </strong></div>)}</section>
    <section className="card form-card"><label className="field"><span>家庭展示文字</span><textarea placeholder="准确批准文字，绑定内容版本" value={available ? text : ''}
      disabled={!available || !may('manage_configs') || phase === 'pending' || phase === 'unknown'} onChange={event => setText(event.target.value)} /></label>
      <label className="field"><span>启用环境</span><select disabled value={available && release ? release.resource.release.environment : 'demo'}>
        <option value="demo">演示</option><option value="controlled_pilot">受控试点</option><option value="approved_family">获准家庭使用</option>
        {available && release && !['demo', 'controlled_pilot', 'approved_family'].includes(release.resource.release.environment) && <option value={release.resource.release.environment}>{release.resource.release.environment}</option>}
      </select></label></section>
    <button className="button" disabled={!canEnable} onClick={() => edit('release_enable')}>条件齐备后启用</button>
    {available && <section className="card form-card"><h3>维护与版本记录</h3>
      {may('publish_config') && <button className="text-action" disabled={busy.current} onClick={() => list('release', 'publish_config')}>选择发布版本</button>}
      {actions.some(action => ['manage_configs', 'approve_config', 'register_validation', 'publish_config'].includes(action)) && <button className="text-action" disabled={busy.current} onClick={() => list('config', directoryAction('config'))}>选择配置版本</button>}
      {may('manage_contacts') && <button className="text-action" disabled={busy.current} onClick={() => list('contact', 'manage_contacts')}>选择联系版本</button>}
      {config && <p className="muted">{config.resource.config.config_key} · 版本 {config.resource.version} · {config.resource.status}</p>}
      {contact && <p className="muted">{contact.contact.contact_name} · 版本 {contact.version} · {contact.status} · {contact.contact.valid_until}</p>}
      {may('manage_configs') && release && text !== release.approved_text && <><p className="muted">家庭文字需要保存为新配置版本，并由新发布版本准确引用。</p>
        <button className="text-action" disabled={busy.current || blockedWrite || !text.trim()} onClick={saveTextVersion}>保存家庭文字版本</button></>}
      {operations.map(op => <button className="text-action" key={op} disabled={busy.current || blockedWrite || !eligible(op)} onClick={() => edit(op)}>{publicationOperations[op][3]}</button>)}
      <button className="text-action" disabled={busy.current} onClick={() => history()}>查看本人提交记录</button>
    </section>}
    {available && (parameters.has('intent_id') || parameters.has('request_id')) && <section className="card form-card"><h3>原请求与保存结果</h3>
      <p role="status">{phase === 'pending' ? '正在确认提交结果' : request ? requestStates[request.state] : '提交结果需要查询原请求确认。'}</p>
      {request?.receipt && <p>已保存版本 {request.receipt.version} · {request.receipt.saved_at}</p>}
      {request?.error_code && <p>{request.error_code}</p>}
      <button className="text-action" disabled={busy.current} onClick={() => void restore()}>查询原请求</button>
      {absent && <button className="text-action" onClick={() => { const query = new URLSearchParams(parameters);
        for (const key of ['intent_id', 'request_id', 'operation_id', 'target_key']) query.delete(key);
        setParameters(query, { replace: true }); setAbsent(false); setRequest(null); original.current = null; setPhase('idle'); setError(null); }}>更正输入</button>}
      {request?.may_retry && <button className="button secondary" disabled={busy.current} onClick={() => continueRequest(true)}>使用原正文重试</button>}
      {request?.state === 'pending' && <><label className="field"><span>更正依据</span><input value={cancelReason} disabled={busy.current} onChange={event => setCancelReason(event.target.value)} /></label>
        <button className="button secondary" disabled={busy.current || !cancelReason.trim()} onClick={() => void run(async signal => {
          const value = await cancelPublication(request, cancelReason, signal);
          if (!signal.aborted) { setRequest(value); setCancelReason(''); }
        }, true)}>取消原提交意图</button></>}
      {request?.state === 'pending' && !request.may_retry && request.expires_at && Date.parse(request.expires_at) > Date.now() && <button className="button secondary" disabled={busy.current} onClick={() => continueRequest(false)}>继续原提交</button>}
    </section>}
    {error && !sheet && <p role="alert">{error}</p>}
  </UI2Frame>;
}
