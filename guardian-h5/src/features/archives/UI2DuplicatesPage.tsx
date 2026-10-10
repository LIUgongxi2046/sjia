import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ChevronDown, ShieldCheck } from 'lucide-react';
import { UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { PrivateSourceGallery } from '../../components/PrivateSourceGallery';
import { useAccess } from '../../state/access';
import { useArchiveRelations } from '../../api/useArchiveRelations';
import { ApiError } from '../../api/client';
import { timeLabel } from '../../api/records';
import { validateApiValue } from '../../api/schema';
import { prepareRelationRequest, readArchiveRelation, readRelationRequest, relationPayloadMatches,
  retryRelationRequest, submitRelationRequest } from '../../api/archiveRelations';
import type { IntentView } from '../../../../frontend-design/types';
import type { RelationChangeInput, RelationInput, RelationType, RelationView, WriteReceipt } from '../../../../frontend-design/v2-types';
import type { RelationCandidate, RelationIdentity, RelationRequestView, RelationSource } from '../../../../frontend-design/archive-relations-types';

const types: [RelationType, string][] = [['same_bytes', '相同文件'], ['document_copy', '同份文书的不同照片'],
  ['same_sampling', '同次采样的病史引用'], ['independent_sampling', '独立的重复检查'], ['formal_correction', '正式更正版本']];
const statuses = { proposed: '待确认', confirmed: '已确认资料关系', disputed: '无法确定', released: '已解除关联' };

function inputFrom(row: RelationView): RelationChangeInput {
  const { relation_id, patient_id, version_id, version, verification_id, allowed_actions, ...fields } = row;
  return { ...fields, expected_version: version };
}

export function UI2DuplicatesPage() {
  const access = useAccess();
  return <DuplicatesContent key={JSON.stringify([access.principalId, access.patientId])} />;
}

function DuplicatesContent() {
  const access = useAccess();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const relationId = params.get('relation');
  const requestId = params.get('request');
  const clientRequestId = params.get('client_request');
  const association = access.relations.find(value => value.patient_id === access.patientId);
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const directory = useArchiveRelations(canRead ? access.patientId : null, canRead ? access.principalId : null);
  const canWrite = canRead && directory.visible && !!directory.data && !directory.loading && !directory.error
    && !!access.csrfToken && association?.status === 'active'
    && association.actions.includes('write_record') && Date.parse(association.valid_from) <= Date.now()
    && (!association.valid_until || Date.parse(association.valid_until) > Date.now());
  const [leftId, setLeftId] = useState('');
  const [rightId, setRightId] = useState('');
  const [type, setType] = useState<RelationType | ''>('');
  const [status, setStatus] = useState<RelationInput['status']>('confirmed');
  const [reason, setReason] = useState('');
  const [evidence, setEvidence] = useState('');
  const [selected, setSelected] = useState<RelationView | null>(null);
  const [identity, setIdentity] = useState<RelationIdentity | null>(null);
  const [intent, setIntent] = useState<IntentView | null>(null);
  const [request, setRequest] = useState<RelationRequestView | null>(null);
  const [saved, setSaved] = useState<{ receipt: WriteReceipt; current: RelationView } | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const active = useRef<AbortController | null>(null);
  const busyRef = useRef(false);
  const composing = useRef(false);
  const mounted = useRef(true);
  const recovered = useRef<string | null>(null);
  const loaded = useRef<string | null>(null);
  const sources = directory.data?.sources ?? [];
  const left = sources.find(value => value.id === leftId);
  const right = sources.find(value => value.id === rightId);
  const edit = canWrite && !busy && !identity && !saved && !requestId && !clientRequestId;
  const permitted = !selected || selected.allowed_actions.includes('archive_relation_update');
  const sourceFiles = [left, right].flatMap((value, index) => {
    const fileId = value?.kind === 'file' ? value.id : value?.source?.file_id;
    return fileId ? [{ file_id: fileId, ordinal: index + 1, checksum: value?.checksum ?? undefined }] : [];
  });

  function run(operation: (controller: AbortController) => Promise<void>) {
    if (!canRead || !directory.visible || busyRef.current) return;
    const controller = new AbortController(); active.current = controller; busyRef.current = true;
    setBusy(true); setError(null);
    void operation(controller).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted && mounted.current) {
        setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setSaved(null); setRequest(null); setSelected(null); directory.reload(); void access.refresh(); }
        if (failure instanceof ApiError && failure.status === 409) directory.reload();
      }
    }).finally(() => { if (active.current === controller) { active.current = null; busyRef.current = false; if (mounted.current) setBusy(false); } });
  }

  function fill(payload: RelationInput | RelationChangeInput) {
    setLeftId(payload.left_fact_version_id ?? payload.left_file_id ?? '');
    setRightId(payload.right_fact_version_id ?? payload.right_file_id ?? '');
    setType(payload.relation_type); setStatus(payload.status); setReason(payload.reason); setEvidence(payload.evidence_ref);
  }

  async function adopt(receipt: WriteReceipt, payload: RelationIdentity['payload'], controller: AbortController) {
    if (!access.patientId) throw new Error('当前患儿尚未取得访问资格。');
    const original = await readArchiveRelation(receipt.resource_id, access.patientId, controller.signal, receipt.version);
    if (!relationPayloadMatches(original, payload)) throw new Error('原关系回执与重新读取的保存内容不一致。');
    const current = await readArchiveRelation(receipt.resource_id, access.patientId, controller.signal);
    if (BigInt(current.version) < BigInt(receipt.version)) throw new Error('当前关系版本早于保存回执。');
    if (!controller.signal.aborted && mounted.current) { setSaved({ receipt, current }); setSelected(current); directory.reload(); }
  }

  function inspect(identifier: string, byRequest = false) {
    run(async controller => {
      if (!access.patientId) throw new Error('当前患儿尚未取得访问资格。');
      const operation = params.get('operation');
      if (byRequest && operation !== 'archive_relation_create' && operation !== 'archive_relation_update') throw new Error('原请求缺少准确关系操作标识。');
      const target = operation === 'archive_relation_update' ? `/archive-relations/${relationId}/relationships` : `/patients/${access.patientId}/archive-relations`;
      const value = await readRelationRequest(identifier, controller.signal, byRequest ? { operation_id: operation as RelationIdentity['operation_id'], target_key: target } : undefined);
      if (value.operation_id === 'archive_relation_verify' || !('relation_type' in value.relation_input)) throw new Error('该原请求不属于本人资料整理。');
      if (value.operation_id === 'archive_relation_create' ? value.target_key !== `/patients/${access.patientId}/archive-relations`
        : relationId && value.target_key !== `/archive-relations/${relationId}/relationships`) throw new Error('原请求目标与当前资料关系不一致。');
      if (controller.signal.aborted || !mounted.current) return;
      fill(value.relation_input); setRequest(value);
      setIdentity({ operation_id: value.operation_id, target_key: value.target_key, request_id: value.request_id, schema_version: '2.0.0', payload: value.relation_input });
      setIntent({ intent_id: value.intent_id, request_id: value.request_id, generation: value.generation, expires_at: value.expires_at,
        payload_sha256: value.payload_sha256, status: value.state === 'committed' ? 'committed' : value.state === 'failed' ? 'failed' : 'prepared' });
      if (value.receipt) await adopt(value.receipt, value.relation_input, controller);
    });
  }

  useEffect(() => {
    mounted.current = true;
    const changed = () => {
      if (document.visibilityState !== 'visible') { active.current?.abort(); setSaved(null); setSelected(null); setRequest(null);
        setIdentity(null); setIntent(null); setLeftId(''); setRightId(''); setReason(''); setEvidence(''); recovered.current = null; loaded.current = null; }
    };
    document.addEventListener('visibilitychange', changed);
    return () => { mounted.current = false; active.current?.abort(); active.current = null; busyRef.current = false;
      recovered.current = null; loaded.current = null; document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => {
    if (!canRead) { active.current?.abort(); setSaved(null); setSelected(null); setRequest(null); setIdentity(null); setIntent(null); setLeftId(''); setRightId(''); setReason(''); setEvidence(''); recovered.current = null; loaded.current = null; }
  }, [canRead]);
  useEffect(() => {
    const lookup = requestId ? `intent:${requestId}` : clientRequestId ? `request:${clientRequestId}` : null;
    if (canRead && directory.visible && lookup && recovered.current !== lookup && !busyRef.current) { recovered.current = lookup; inspect(requestId ?? clientRequestId!, !requestId); }
    else if (canRead && directory.visible && relationId && !lookup && loaded.current !== relationId && !busyRef.current) {
      loaded.current = relationId;
      run(async controller => { const value = await readArchiveRelation(relationId, access.patientId!, controller.signal);
        if (!controller.signal.aborted && mounted.current) { setSelected(value); fill(inputFrom(value)); } });
    }
  }, [canRead, directory.visible, relationId, requestId, clientRequestId, busy]);

  function chooseRelation(value: RelationView) {
    if (!edit) return;
    setSelected(value); fill(inputFrom(value)); loaded.current = value.relation_id;
    setParams(next => { const copy = new URLSearchParams(next); copy.set('relation', value.relation_id); return copy; }, { replace: true });
  }
  function chooseCandidate(value: RelationCandidate) {
    if (!edit) return;
    setSelected(null); setLeftId(value.left_id); setRightId(value.right_id); setType(value.suggested_types[0]);
    setStatus('proposed'); setReason(''); setEvidence(''); loaded.current = null;
    setParams(next => { const copy = new URLSearchParams(next); copy.delete('relation'); return copy; }, { replace: true });
  }

  function submit(release = false) {
    run(async controller => {
      if (!canWrite || !permitted || !access.patientId) throw new Error('当前资料关系尚未取得保存权限。');
      let current = identity;
      if (!current) {
        if (!left || !right || left.kind !== right.kind || left.id === right.id || !type || !reason.trim() || !evidence.trim()) throw new Error('请选择两个准确来源、关系类别、关系依据和更正原因。');
        const fields: RelationInput = { left_fact_version_id: left.kind === 'fact' ? left.id : null,
          right_fact_version_id: right.kind === 'fact' ? right.id : null, left_file_id: left.kind === 'file' ? left.id : null,
          right_file_id: right.kind === 'file' ? right.id : null, relation_type: type, status: release ? 'released' : status, evidence_ref: evidence, reason };
        const payload = selected ? { ...fields, expected_version: selected.version } : fields;
        validateApiValue(`archive-relations.schema.json#/$defs/${selected ? 'RelationChangeInput' : 'RelationInput'}`, payload);
        current = { operation_id: selected ? 'archive_relation_update' : 'archive_relation_create',
          target_key: selected ? `/archive-relations/${selected.relation_id}/relationships` : `/patients/${access.patientId}/archive-relations`,
          request_id: crypto.randomUUID(), schema_version: '2.0.0', payload };
      }
      setIdentity(current); recovered.current = `request:${current.request_id}`;
      setParams(next => { const copy = new URLSearchParams(next); copy.set('client_request', current.request_id); copy.set('operation', current.operation_id); return copy; }, { replace: true });
      const prepared = intent ?? await prepareRelationRequest(current, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      setIntent(prepared); recovered.current = `intent:${prepared.intent_id}`;
      setParams(next => { const copy = new URLSearchParams(next); copy.set('request', prepared.intent_id); return copy; }, { replace: true });
      if (prepared.status === 'failed' || prepared.status === 'prepared' && Date.parse(prepared.expires_at) <= Date.now()) throw new Error('原关系请求需要重新核验。');
      await adopt(await submitRelationRequest(current, prepared, controller.signal), current.payload, controller);
    });
  }
  function retry() {
    run(async controller => { if (!identity || !request?.may_retry) throw new Error('原关系请求尚不具备重新提交条件。');
      const prepared = await retryRelationRequest(request, controller.signal); setIntent(prepared); setRequest(null);
      await adopt(await submitRelationRequest(identity, prepared, controller.signal), identity.payload, controller); });
  }
  function continueCurrent() {
    if (!saved || busyRef.current) return;
    const row = saved.current; setSaved(null); setIdentity(null); setIntent(null); setRequest(null); setError(null); setSelected(row); fill(inputFrom(row));
    recovered.current = null; loaded.current = row.relation_id;
    setParams(next => { const copy = new URLSearchParams(next); for (const key of ['request', 'client_request', 'operation']) copy.delete(key); copy.set('relation', row.relation_id); return copy; }, { replace: true });
  }
  function sourceDetails(value: RelationSource | undefined, title: string) {
    return <section><h3>{title}</h3>{value ? <><p>{value.label}</p><div className="pair"><span>来源标识</span><strong>{value.id}</strong></div>
      <div className="pair"><span>报告标识</span><strong>{value.report_identifier ?? '尚未提供'}</strong></div>
      <div className="pair"><span>医学时间</span><strong>{value.medical_time ? timeLabel({ ...value.medical_time,
        local_year: value.medical_time.local_year ?? null, local_month: value.medical_time.local_month ?? null,
        end_year: value.medical_time.end_year ?? null, end_month: value.medical_time.end_month ?? null,
        relative_text: value.medical_time.relative_text ?? null }) : '未知'}</strong></div>
      {value.record_version_id && <ContextLink className="text-action" to={`/archives/source?version=${encodeURIComponent(value.record_version_id)}`}>查看事实原始出处</ContextLink>}</> : <p>尚未选择当前获准来源</p>}</section>;
  }

  return <UI2FamilyFrame title="重复资料核对" screenId="UI2-29" note="同一文件、同次采样和独立检查分别判断。" backTo="/archives/confirm">
    <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{association?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={directory.loading} error={directory.error} onRetry={directory.reload} />
    <section className="card form-card"><div className="section-heading"><h2>确认资料关系</h2><span className="badge neutral">{selected ? statuses[selected.status] : left && right ? '待确认关系' : '待选择原件'}</span></div>
      <div className="pair"><span>原始文书</span><strong>{left && right ? `${left.label}；${right.label}` : '尚未提供'}</strong></div>
      <div className="pair"><span>页面与顺序</span><strong>{selected ? `关系版本 ${selected.version}，来源版本分别保留` : '尚未提供'}</strong></div>
      {sourceFiles.length ? <PrivateSourceGallery files={sourceFiles} triggerClassName="button secondary" triggerContent="查看原文依据" /> : <button className="button secondary" disabled>查看原文依据</button>}
      <details><summary>选择当前获准来源与关系候选</summary>
        {(['left', 'right'] as const).map((side, index) => <label className="field" key={side}><span>{index === 0 ? '原始资料' : '对应资料'}</span><select aria-label={index === 0 ? '原始资料' : '对应资料'} disabled={!edit || !!selected}
          value={index === 0 ? leftId : rightId} onChange={event => { (index === 0 ? setLeftId : setRightId)(event.target.value); setType(''); setStatus('confirmed'); }}><option value="">请选择</option>
          {sources.map(source => <option key={source.id} value={source.id}>{source.label}</option>)}</select></label>)}
        {sourceDetails(left, '原始资料依据')}{sourceDetails(right, '对应资料依据')}
        <h3>待确认候选</h3>{directory.data?.candidates.length ? directory.data.candidates.map(value => <button type="button" className="text-action" disabled={!edit} key={JSON.stringify([value.source_kind, value.left_id, value.right_id])} onClick={() => chooseCandidate(value)}>
          {sources.find(source => source.id === value.left_id)?.label} 与 {sources.find(source => source.id === value.right_id)?.label} · 待确认</button>) : <p>当前页没有关系候选；数值相同不能单独确认同次采样。</p>}
        <h3>已保存关系</h3>{directory.data?.relations.length ? directory.data.relations.map(value => <button type="button" className="text-action" disabled={!edit} key={value.relation_id} onClick={() => chooseRelation(value)}>
          {types.find(([code]) => code === value.relation_type)?.[1]} · {statuses[value.status]} · 版本 {value.version}</button>) : <p>当前页尚无已保存关系</p>}
        {directory.data?.next_cursor && <button type="button" className="text-action" disabled={directory.loading || busy} onClick={directory.more}>继续读取来源与关系</button>}
      </details>
    </section>
    <form style={{ display: 'contents' }} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }} onKeyDown={event => { if (event.key === 'Enter' && (event.nativeEvent.isComposing || composing.current)) event.preventDefault(); }}
      onSubmit={event => { event.preventDefault(); if (!composing.current) submit(); }}>
      <section className="card form-card"><label className="field"><span>与已有资料的关系</span><select aria-label="与已有资料的关系" disabled={!edit || !permitted} value={status === 'disputed' ? 'uncertain' : type}
        onChange={event => { if (event.target.value === 'uncertain') { setStatus('disputed'); } else { setType(event.target.value as RelationType); setStatus('confirmed'); } }}><option value="" disabled>请选择</option>
        {types.map(([value, label]) => <option key={value} value={value} disabled={!!left && (left.kind === 'file' ? !['same_bytes', 'document_copy'].includes(value) : ['same_bytes', 'document_copy'].includes(value))}>{label}</option>)}<option value="uncertain">无法确定</option></select></label>
        <label className="field"><span>关系依据</span><textarea aria-label="关系依据" disabled={!edit || !permitted} placeholder="根据报告标识、采样时间与来源核对" value={evidence} onChange={event => setEvidence(event.target.value)} /></label>
        <details><summary>关系状态、原因与专业核对</summary><label className="field"><span>本人整理状态</span><select aria-label="本人整理状态" disabled={!edit || !permitted} value={status} onChange={event => setStatus(event.target.value as RelationInput['status'])}>
          <option value="proposed">待确认</option><option value="confirmed">本人确认</option><option value="disputed">无法确定</option>{selected?.status === 'released' && <option value="released">已解除关联</option>}</select></label>
          <label className="field"><span>确认或解除原因</span><textarea aria-label="确认或解除原因" disabled={!edit || !permitted} value={reason} onChange={event => setReason(event.target.value)} /></label>
          <div className="pair"><span>专业核对证明</span><strong>{selected?.verification_id ?? '尚未取得专业核对证明'}</strong></div>
          <p>本人确认与专业核对分别记录。无法确定的关系保留已有类别线索和未决状态。</p>
        </details>
      </section>
      <div className="note amber"><ShieldCheck size={17} /><p>项目或数值相同，不能单独证明属于同次采样。</p></div>
      <button className="button" type="submit" disabled={!canWrite || !permitted || busy || !!saved || !!identity && request?.state !== 'pending' || !identity && (!left || !right || !type || !evidence.trim() || !reason.trim())}>{busy ? '正在核验关系保存结果' : '确认关系'}</button>
      <button className="button secondary" type="button" disabled={!edit || !permitted || !selected || selected.status === 'released' || !evidence.trim() || !reason.trim()} onClick={() => submit(true)}>解除当前关联</button>
    </form>
    {error && <section className="status-panel error" role="alert"><h2>关系结果需要核验</h2><p>{error.message}</p></section>}
    {(intent || requestId || clientRequestId) && canRead && !saved && <section className="status-panel pending" role="status"><h2>原请求结果待核验</h2><p>准确原请求及正文继续保留，明确保存结果后可以继续整理。</p>
      <button className="text-action" disabled={busy} onClick={() => { const id = intent?.intent_id ?? requestId; inspect(id ?? clientRequestId!, !id); }}>查询原关系请求</button>
      {request?.may_retry && <button className="button secondary" disabled={busy} onClick={retry}>重新提交原关系请求</button>}</section>}
    {saved && canRead && <section className="status-panel success" role="status"><h2>资料关系已经保存</h2><div className="pair"><span>保存版本</span><strong>{saved.receipt.version}</strong></div>
      <div className="pair"><span>当前关系</span><strong>{statuses[saved.current.status]}</strong></div><p>来源、依据和历史版本均可重新读取。专业采样资格继续按专业核对证明检查。</p>
      <button className="button secondary" disabled={busy} onClick={continueCurrent}>继续整理当前关系</button></section>}
  </UI2FamilyFrame>;
}
