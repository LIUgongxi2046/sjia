import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { ChevronRight as ArrowRight, ChevronRight, CircleHelp } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { useAccess } from '../../state/access';
import { useRelationResource } from '../../api/useRelationResource';
import { readApplication, prepareApplication, submitApplication, readApplicationRequest, retryApplication, sameApplicationDeclaration } from '../../api/application';
import type { ApplicationDeclaration, ApplicationInput, ApplicationIntent, ApplicationRequest, ApplicationReceipt } from '../../api/application';
import type { ApplicationView } from '../../api/endpoints';
import { ApplicationDeclarationDetails, declaredRelationshipLabels } from './ApplicationDeclarationDetails';

const permissionLabels: Record<ApplicationInput['requested_access'], string> = {
  read: '查看资料', read_write: '查看与登记', review_decides: '按核验结果确定',
};
const statusLabels: Record<ApplicationView['status'], string> = { pending: '待核验', approved: '已通过', rejected: '未通过', withdrawn: '已撤回' };

export function UI2AssociationApplyPage() {
  const access = useAccess();
  const navigate = useNavigate();
  const canWrite = !!access.principalId && !!access.csrfToken && !access.loading && !access.error;
  const recent = useRelationResource<{ requests: ApplicationRequest[] }>(canWrite ? '/api/requests' : null,
    access.principalId, 'ApplicationRequestList');
  const [evidence, setEvidence] = useState('');
  const [declaredName, setDeclaredName] = useState('');
  const [declaredRelationship, setDeclaredRelationship] = useState<ApplicationDeclaration['declared_relationship'] | ''>('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [permission, setPermission] = useState<ApplicationInput['requested_access']>('read');
  const [identity, setIdentity] = useState<{ requestId: string; payload: ApplicationInput } | null>(null);
  const [intent, setIntent] = useState<ApplicationIntent | null>(null);
  const [request, setRequest] = useState<ApplicationRequest | null>(null);
  const [selectedIntentId, setSelectedIntentId] = useState<string | null>(null);
  const [saved, setSaved] = useState<{ receipt: ApplicationReceipt; application: ApplicationView } | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const composing = useRef(false);
  const owner = useRef(access.principalId);
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (owner.current !== access.principalId) {
      pending.current?.abort(); pending.current = null; busyRef.current = false;
      composing.current = false;
      owner.current = access.principalId;
      setBusy(false); setEvidence(''); setDeclaredName(''); setDeclaredRelationship(''); setAcknowledged(false);
      setPermission('read'); setIdentity(null); setIntent(null);
      setRequest(null); setSelectedIntentId(null); setSaved(null); setError(null);
    }
  }, [access.principalId]);
  useEffect(() => { if (!canWrite) { pending.current?.abort(); setSaved(null); setRequest(null); setIntent(null); } }, [canWrite]);

  function run(operation: (controller: AbortController) => Promise<void>) {
    if (!canWrite || busyRef.current) return;
    const controller = new AbortController(); pending.current = controller;
    busyRef.current = true; setBusy(true); setError(null);
    void operation(controller).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setError(failure); recent.reload(); }
    }).finally(() => {
      if (pending.current === controller) { busyRef.current = false; pending.current = null; setBusy(false); }
    });
  }

  async function adoptReceipt(receipt: ApplicationReceipt, payload: ApplicationInput | null, controller: AbortController) {
    const application = await readApplication(receipt.resource_id, controller.signal);
    if (BigInt(application.version) < BigInt(receipt.version)) throw new Error('重新读取的申请版本早于保存回执。');
    if (payload && (application.evidence_ref !== payload.evidence_ref || application.note !== payload.note
      || application.requested_access !== payload.requested_access
      || !sameApplicationDeclaration(payload.applicant_declaration, application.applicant_declaration))) throw new Error('重新读取的申请声明、说明或权限与提交内容不一致。');
    if (!controller.signal.aborted) { setSaved({ receipt, application }); recent.reload(); }
  }

  function submit() {
    // Recovery reuses the original acknowledged snapshot, never a newly inferred declaration.
    if (saved || selectedIntentId !== null && identity === null || (!identity && (!declaredName.trim() || !declaredRelationship || !acknowledged || !evidence.trim()))
      || (request !== null && (request.state !== 'pending' || request.may_retry || request.operation_id !== 'application_create' || request.application_input === null))) return;
    run(async controller => {
      if (!access.csrfToken) throw new Error('当前身份缺少请求校验信息。');
      const current = identity ?? { requestId: crypto.randomUUID(), payload: { evidence_ref: evidence.trim(), note: null, requested_access: permission,
        applicant_declaration: { schema_version: '1.0.0', declared_patient_name: declaredName.trim(),
          declared_relationship: declaredRelationship as ApplicationDeclaration['declared_relationship'], guardian_scope_acknowledged: true } } satisfies ApplicationInput };
      setIdentity(current);
      const prepared = intent ?? await prepareApplication(current.requestId, current.payload, access.csrfToken, controller.signal);
      if (controller.signal.aborted) return;
      setIntent(prepared);
      if (prepared.status === 'failed' || (prepared.status === 'prepared' && Date.parse(prepared.expires_at) <= Date.now())) throw new Error('保存意图需要重新核验，当前没有重新提交申请。');
      const receipt = await submitApplication(prepared, current.payload, access.csrfToken, controller.signal);
      await adoptReceipt(receipt, current.payload, controller);
    });
  }

  function inspect(intentId: string) {
    run(async controller => {
      setSelectedIntentId(intentId); setSaved(null); setRequest(null); setIntent(null); setIdentity(null); setEvidence('');
      setDeclaredName(''); setDeclaredRelationship(''); setAcknowledged(false);
      const current = await readApplicationRequest(intentId, controller.signal);
      if (controller.signal.aborted) return;
      setRequest(current); setSaved(null);
      if (current.operation_id === 'application_create') {
        if (current.target_key !== '/api/relation-applications') throw new Error('申请请求的目标与当前申请入口不一致。');
        if (current.application_input) {
          const { validateRelationRead } = await import('../../api/relationReadSchema');
          const payload = validateRelationRead<ApplicationInput>('ApplicationInput', current.application_input);
          setEvidence(payload.evidence_ref); setPermission(payload.requested_access);
          setDeclaredName(payload.applicant_declaration?.declared_patient_name ?? '');
          setDeclaredRelationship(payload.applicant_declaration?.declared_relationship ?? '');
          setAcknowledged(payload.applicant_declaration?.guardian_scope_acknowledged === true);
          setIdentity({ requestId: current.request_id, payload });
          setIntent({ intent_id: current.intent_id, request_id: current.request_id, generation: current.generation,
            status: current.state === 'committed' ? 'committed' : current.state === 'failed' ? 'failed' : 'prepared',
            expires_at: current.expires_at, payload_sha256: current.payload_sha256 });
          if (current.receipt) await adoptReceipt(current.receipt, payload, controller);
        } else if (current.receipt) await adoptReceipt(current.receipt, null, controller);
      } else if (current.receipt) await adoptReceipt(current.receipt, null, controller);
    });
  }

  function retry() {
    run(async controller => {
      if (!request || !access.csrfToken || request.operation_id !== 'application_create' || !request.application_input || !request.may_retry)
        throw new Error('当前请求尚不具备重新提交条件。');
      const { validateRelationRead } = await import('../../api/relationReadSchema');
      const payload = validateRelationRead<ApplicationInput>('ApplicationInput', request.application_input);
      const prepared = await retryApplication(request, access.csrfToken, controller.signal);
      if (controller.signal.aborted) return;
      setIntent(prepared); setRequest(null);
      await adoptReceipt(await submitApplication(prepared, payload, access.csrfToken, controller.signal), payload, controller);
    });
  }

  const frozen = busy || identity !== null || saved !== null || selectedIntentId !== null;
  return <UI2FamilyFrame title="申请关联" screenId="UI2-19" note="建立获准的访问关系" backTo="/associations">
    <UI2ReadState requirePatient={false} />
    <div className="note blue"><CircleHelp size={17} /><p>填写申请内容，核验后开放患儿资料访问。</p></div>
    <form style={{ display: 'contents' }} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
      onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}
      onSubmit={event => { event.preventDefault(); if (!composing.current) submit(); }}>
      <section className="card form-card">
        <label className="field" htmlFor="application-declared-name"><span>患儿称呼</span><input id="application-declared-name" autoComplete="off"
          placeholder="填写您所称呼的名字" value={canWrite ? declaredName : ''} maxLength={100} required={!identity} disabled={!canWrite || frozen}
          onChange={event => { setDeclaredName(event.target.value); setAcknowledged(false); }} /></label>
        <label className="field" htmlFor="application-declared-relationship"><span>与患儿关系</span><select id="application-declared-relationship"
          value={canWrite ? declaredRelationship : ''} required={!identity} disabled={!canWrite || frozen}
          onChange={event => { setDeclaredRelationship(event.target.value as typeof declaredRelationship); setAcknowledged(false); }}>
          <option value="">请选择</option>{Object.entries(declaredRelationshipLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <label className="field" htmlFor="application-evidence"><span>申请说明</span><textarea id="application-evidence" placeholder="说明申请目的和所需资料范围"
          value={canWrite ? evidence : ''} maxLength={10000} required disabled={!canWrite || frozen} onChange={event => { setEvidence(event.target.value); setAcknowledged(false); }} /></label>
      </section>
      <label className="switch-row application-acknowledgement"><input id="application-guardian-acknowledgement" type="checkbox" checked={canWrite && acknowledged}
        required={!identity} disabled={!canWrite || frozen} onChange={event => setAcknowledged(event.target.checked)} /><span>确认申请资料属于本人监护范围</span></label>
      <button className="button" type="submit" disabled={!canWrite || busy || !evidence.trim() || saved !== null
        || (!identity && (!declaredName.trim() || !declaredRelationship || !acknowledged))
        || (selectedIntentId !== null && identity === null)
        || (request !== null && (request.state !== 'pending' || request.may_retry || request.operation_id !== 'application_create' || request.application_input === null))}>
        {busy ? '正在核验申请保存结果' : identity ? '继续提交原申请请求' : '提交关联申请'}</button>
      <details className="card form-card"><summary>申请权限与核验说明 · {permissionLabels[permission]}</summary>
        <label className="field" htmlFor="application-permission"><span id="application-permission-label">申请权限</span><select id="application-permission" aria-labelledby="application-permission-label" value={permission} disabled={!canWrite || frozen}
          onChange={event => { setPermission(event.target.value as ApplicationInput['requested_access']); setAcknowledged(false); }}>{Object.entries(permissionLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
        <p>患儿称呼、关系及监护范围均为申请人未经核验的声明。勾选仅用于说明申请范围，不代表身份或监护资格已核验，也不构成临床同意或访问授权。</p>
        <div className="pair"><span>身份与证明方式</span><strong>按项目配置提供</strong></div>
      </details>
    </form>
    {error && <section className="status-panel error" role="alert"><h2>申请结果需要核验</h2><p>{error.message}</p><p>保存结果尚未确认，请先查询申请状态。</p></section>}
    {canWrite && (intent || selectedIntentId) && !saved && <button className="text-action" disabled={busy} onClick={() => inspect(intent?.intent_id ?? selectedIntentId!)}>核验原申请结果<ArrowRight size={16} /></button>}
    {canWrite && request && !saved && <section className="card form-card"><h3>本人保存请求</h3>
      <div className="pair"><span>当前结果</span><strong>{({ pending: '尚未确认保存', committed: '已经保存', failed: '未完成保存' } as const)[request.state]}</strong></div>
      <div className="pair"><span>请求版本</span><strong>{request.generation}</strong></div>
      {request.application_input === null && <p>该请求尚未保留可恢复正文，不能据此重新提交。</p>}
      {request.may_retry && request.operation_id === 'application_create' && <button className="button" disabled={busy} onClick={retry}>重新提交原申请请求</button>}
      {request.error_code && <p role="status">{request.error_code}</p>}
    </section>}
    {canWrite && saved && <section className="status-panel success" role="status"><h2>申请已经保存</h2>
      <div className="pair"><span>当前申请状态</span><strong>{statusLabels[saved.application.status]}</strong></div>
      <div className="pair"><span>本次保存版本</span><strong>{saved.receipt.version}</strong></div>
      <div className="pair"><span>保存时间</span><strong>{eventTimeLabel(saved.receipt.saved_at)}</strong></div>
      <p>申请保存后，身份、证明与访问范围仍需正式核验。</p>
      <ApplicationDeclarationDetails declaration={saved.application.applicant_declaration} />
      <button className="text-action" onClick={() => navigate('/associations')}>查看本人申请与关联<ArrowRight size={16} /></button>
    </section>}
    {canWrite && <details className="card form-card"><summary>本人申请保存请求</summary>
      <UI2ReadState requirePatient={false} loading={recent.loading} error={recent.error} onRetry={recent.reload} />
      {recent.data?.requests.length === 0 && <p>尚无可追溯的申请保存请求</p>}
      {recent.data?.requests.map(value => <button className="list-row" key={value.intent_id} disabled={busy} onClick={() => inspect(value.intent_id)}>
        <span className="row-text"><strong>{value.operation_id === 'application_create' ? '关联申请' : '申请撤回'}</strong>
          <small>{eventTimeLabel(value.prepared_at)} · {({ pending: '保存待核验', committed: '已保存', failed: '未完成' } as const)[value.state]}</small></span><ChevronRight size={17} />
      </button>)}
    </details>}
    <details className="card form-card"><summary>补充已知病情</summary><p>病情登记需要有效患儿关联，申请依据和申请权限用于身份核验。自报 sJIA 状态及其来源说明在获准病情登记流程中保存。</p><p>自报状态包括尚未填写、已确诊、疑似和不了解；来源说明与病情登记共同保留。</p></details>
    <details className="card form-card"><summary>申请后如何处理</summary><p>身份和证明核验通过后，提供本人获准的患儿访问范围。申请状态与资料访问权限分别确认。</p></details>
  </UI2FamilyFrame>;
}
