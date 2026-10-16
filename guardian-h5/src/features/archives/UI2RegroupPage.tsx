import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import type { DocumentEpisodeOption, DocumentView } from '../../../../frontend-design/v2-types';
import type { RegroupIdentity, RegroupInput, RegroupOutput, RegroupReceipt, RegroupRequest } from '../../../../frontend-design/archive-regroup-types';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { MedicalTimeInput } from '../../components/MedicalTimeInput';
import { editableMedicalTime, emptyTime, isTimeInputValid, timePayload, type TimeInput } from '../../components/medicalTimeValue';
import { useAccess } from '../../state/access';
import { readDocumentInputs } from '../../api/archiveDocuments';
import { prepareRegroup, readRegroupOptions, readRegroupRequest, regroupTarget, retryRegroup, submitRegroup } from '../../api/archiveRegrouping';
import { ApiError } from '../../api/client';
import { validateApiValue } from '../../api/schema';

const types: [RegroupOutput['document_type'], string][] = [['laboratory', '检验报告'], ['inpatient', '住院病历'], ['discharge', '出院记录'], ['outpatient', '门诊记录'], ['therapy', '治疗资料'], ['imaging_report', '影像报告文字'], ['imaging_asset', '影像附件'], ['unknown', '待分类']];
type EditingOutput = Omit<RegroupOutput, 'metadata_confirmed'> & { confirmed: boolean; time: TimeInput };
function fromDocument(value: DocumentView): EditingOutput {
  const time = editableMedicalTime(value.documented_time);
  return { document_id: value.document_id, batch_id: value.batch_id, page_ids: [...value.page_ids], document_type: value.document_type,
    organization: value.organization, documented_time: value.documented_time ? timePayload(time) : null, episode_ids: [...value.episode_ids],
    confirmed_text: value.confirmed_text, proposed_metadata: value.proposed_metadata, confirmed: false, time };
}
export function UI2RegroupPage() {
  const access = useAccess(), [params] = useSearchParams();
  const batch = params.get('batch');
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  return <RegroupContent key={JSON.stringify([access.principalId, access.patientId, batch, relation?.version, relation?.status, access.error])} batchId={batch} />;
}
function RegroupContent({ batchId }: { batchId: string | null }) {
  const access = useAccess(), [params, setParams] = useSearchParams();
  const [documents, setDocuments] = useState<DocumentView[]>([]), [episodes, setEpisodes] = useState<DocumentEpisodeOption[]>([]);
  const [selected, setSelected] = useState<string[]>([]), [outputs, setOutputs] = useState<EditingOutput[]>([]), [reason, setReason] = useState('');
  const [loading, setLoading] = useState(false), [error, setError] = useState<Error | null>(null), [busy, setBusy] = useState(false);
  const [request, setRequest] = useState<RegroupRequest | null>(null), [saved, setSaved] = useState<RegroupReceipt | null>(null);
  const [revision, setRevision] = useState(0), [hidden, setHidden] = useState(document.visibilityState === 'hidden');
  const controller = useRef<AbortController | null>(null), lock = useRef(false), mounted = useRef(true), composing = useRef(false);
  const clientRequestId = params.get('client_request'), intentId = params.get('request');
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error && !hidden
    && relation?.status === 'active' && relation.actions.includes('read_raw')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  const canWrite = canRead && !!access.csrfToken && relation?.status === 'active' && relation.actions.includes('write_record')
    && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
  const edit = canWrite && !busy && !request && !clientRequestId && !intentId && !saved;
  useEffect(() => {
    mounted.current = true;
    const clear = () => { controller.current?.abort(); lock.current = false; setBusy(false); setDocuments([]); setEpisodes([]); setSelected([]); setOutputs([]); setReason(''); setRequest(null); setSaved(null); setError(null); };
    const visibility = () => { setHidden(document.visibilityState === 'hidden'); if (document.visibilityState === 'hidden') clear(); else setRevision(value => value + 1); };
    window.addEventListener('pagehide', clear); document.addEventListener('visibilitychange', visibility);
    return () => { mounted.current = false; controller.current?.abort(); window.removeEventListener('pagehide', clear); document.removeEventListener('visibilitychange', visibility); };
  }, []);
  useEffect(() => {
    // Updating the recovery URL during submission must not abort its own write.
    if (lock.current) return;
    const pending = new AbortController(); controller.current?.abort(); controller.current = pending;
    if (!canRead || !batchId || !access.patientId) return () => pending.abort();
    setLoading(true); setError(null);
    const patientId = access.patientId;
    const work = intentId || clientRequestId
      ? readRegroupRequest(patientId, batchId, (intentId || clientRequestId)!, !intentId, pending.signal).then(value => { if (!pending.signal.aborted) { setRequest(value); setSaved(value.receipt); } })
      : Promise.all([readRegroupOptions(batchId, patientId, pending.signal), readDocumentInputs(batchId, patientId, pending.signal)]).then(([options, inputs]) => {
        if (!pending.signal.aborted) { setDocuments(options.documents); setEpisodes(inputs.episodes); }
      });
    void work.catch(failure => { if (!pending.signal.aborted) setError(failure instanceof Error ? failure : new Error('读取失败')); })
      .finally(() => { if (!pending.signal.aborted) setLoading(false); });
    return () => pending.abort();
  }, [canRead, batchId, access.patientId, intentId, clientRequestId, revision]);
  const sources = documents.filter(value => selected.includes(value.document_id));
  const pages = sources.flatMap(value => value.page_ids);
  const unassigned = pages.filter(page => !outputs.some(output => output.page_ids.includes(page)));
  const update = (index: number, patch: Partial<EditingOutput>) => setOutputs(values => values.map((value, i) => i === index ? { ...value, ...patch, confirmed: patch.confirmed ?? false } : value));
  function selectDocument(value: DocumentView, checked: boolean) {
    setSelected(ids => checked ? [...ids, value.document_id] : ids.filter(id => id !== value.document_id));
    setOutputs(values => checked ? [...values, fromDocument(value)] : values.filter(output => output.document_id !== value.document_id)
      .map(output => ({ ...output, page_ids: output.page_ids.filter(page => !value.page_ids.includes(page)), confirmed: false, confirmed_text: null, proposed_metadata: null, episode_ids: [] })));
  }
  function assign(page: string, target: number) {
    setOutputs(values => values.map((value, index) => ({ ...value, page_ids: index === target ? [...value.page_ids.filter(id => id !== page), page] : value.page_ids.filter(id => id !== page),
      confirmed: false, confirmed_text: null, proposed_metadata: null, episode_ids: [] })));
  }
  function payload(): RegroupInput {
    if (!batchId || !sources.length || sources.length > 20 || outputs.some(item => !item.confirmed || !item.page_ids.length || !isTimeInputValid(item.time))) throw new Error('请选择来源，并逐份确认目标文书的页序与元数据。');
    return validateApiValue<RegroupInput>('v2-protocol.schema.json#/$defs/RegroupInput', { batch_id: batchId,
      sources: sources.map(value => ({ document_id: value.document_id, expected_version: value.version })),
      outputs: outputs.map(({ confirmed, time, ...value }) => ({ ...value, metadata_confirmed: true,
        documented_time: timePayload(time) })), unassigned_page_ids: unassigned, reason: reason.trim() });
  }
  async function commit(recovered?: RegroupRequest) {
    if (!canWrite || lock.current || !access.patientId || !batchId) return;
    lock.current = true; setBusy(true); setError(null);
    const pending = new AbortController(); controller.current = pending;
    try {
      const identity: RegroupIdentity = { operation_id: 'archive_documents_regroup', target_key: regroupTarget(access.patientId),
        request_id: recovered?.request_id ?? crypto.randomUUID(), schema_version: '2.0.0', payload: recovered?.regroup_input ?? payload() };
      // Persist only opaque recovery identifiers in the URL before any write.
      if (!recovered) { const next = new URLSearchParams(params); next.set('client_request', identity.request_id); setParams(next, { replace: true }); }
      const intent = recovered ? recovered.may_retry ? await retryRegroup(recovered, pending.signal)
        : { intent_id: recovered.intent_id, request_id: recovered.request_id, generation: recovered.generation, status: 'prepared' as const,
          expires_at: recovered.expires_at, payload_sha256: recovered.payload_sha256 } : await prepareRegroup(identity, pending.signal);
      if (intent.status !== 'committed') await submitRegroup(identity, intent, pending.signal);
      const actual = await readRegroupRequest(access.patientId, batchId, intent.intent_id, false, pending.signal);
      if (!pending.signal.aborted && mounted.current) { setRequest(actual); setSaved(actual.receipt); }
    } catch (failure) { if (!pending.signal.aborted && mounted.current) setError(failure instanceof Error ? failure : new Error('重组保存结果待查询')); }
    finally { if (mounted.current) { lock.current = false; setBusy(false); } }
  }
  const ready = edit && sources.length > 0 && sources.length <= 20 && !!reason.trim() && outputs.every(value => value.confirmed && value.page_ids.length > 0 && isTimeInputValid(value.time));
  return <UI2FamilyFrame title="拆分、合并与移动文书页" screenId="UI2-28-regroup" note="同一批次内一次保存全部页归属，历史文书与原件持续保留。" backTo="/archives">
    <UI2ReadState loading={loading || access.loading} error={error || (access.error ? new Error(access.error) : null)} onRetry={() => setRevision(value => value + 1)} />
    {!batchId && <p role="status">请从某份文书或批次进入重组。</p>}
    {canRead && !request && !clientRequestId && !intentId && <form onSubmit={event => { event.preventDefault(); if (!composing.current) void commit(); }} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}>
      <fieldset disabled={!edit} className="card"><legend>选择需要一起重组的文书（最多 20 份）</legend>
        {!loading && !documents.length && <p>当前批次没有获准重组的文书。</p>}
        {documents.map(value => <label className="field" key={value.document_id}><input type="checkbox" checked={selected.includes(value.document_id)} onChange={event => selectDocument(value, event.currentTarget.checked)} />{value.organization || '机构未确定'} · {types.find(([key]) => key === value.document_type)?.[1]} · 版本 {value.version} · {value.page_ids.length} 页</label>)}
      </fieldset>
      {sources.length > 0 && <><section className="card"><h2>每个原页只保留一个当前归属</h2><p>页归属改变后清除待核对原文与就诊选择，需要按新边界重新核对。不分配的页保留原件和逐项事实。</p>
        {pages.map((page, index) => <label className="field" key={page}><span>原页 {index + 1}</span><ContextLink to={`/archives/source?page=${page}&batch=${batchId}`}>核对原页</ContextLink><select aria-label={`原页 ${index + 1} 的目标文书`} disabled={!edit} value={outputs.findIndex(value => value.page_ids.includes(page))} onChange={event => assign(page, Number(event.target.value))}><option value={-1}>暂不归入文书</option>{outputs.map((_, index) => <option key={index} value={index}>目标文书 {index + 1}</option>)}</select></label>)}
        <button type="button" disabled={!edit || outputs.length >= 20} onClick={() => setOutputs(values => [...values, { document_id: null, batch_id: batchId!, page_ids: [], document_type: 'unknown', organization: null, documented_time: null, episode_ids: [], confirmed_text: null, proposed_metadata: null, confirmed: false, time: { ...emptyTime } }])}>增加新文书（拆分）</button></section>
        {outputs.map((output, index) => <fieldset disabled={!edit} className="card" key={index}><legend>目标文书 {index + 1}</legend>
          <p>{output.document_id ? '保留原文书身份并新增版本' : '新增文书身份'}；共 {output.page_ids.length} 页。</p>
          <button type="button" onClick={() => setOutputs(values => values.filter((_, i) => i !== index).map(value => ({ ...value, confirmed: false })))}>移除这个目标（原文书转为历史）</button>
          <ol>{output.page_ids.map((page, order) => <li key={page}>原页 {pages.indexOf(page) + 1} <button type="button" disabled={order === 0} onClick={() => { const next = [...output.page_ids]; [next[order - 1], next[order]] = [next[order], next[order - 1]]; update(index, { page_ids: next }); }}>上移</button> <button type="button" disabled={order === output.page_ids.length - 1} onClick={() => { const next = [...output.page_ids]; [next[order + 1], next[order]] = [next[order], next[order + 1]]; update(index, { page_ids: next }); }}>下移</button></li>)}</ol>
          <label className="field"><span>文书类型</span><select value={output.document_type} onChange={event => update(index, { document_type: event.target.value as RegroupOutput['document_type'] })}>{types.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
          <label className="field"><span>医疗机构原文</span><input value={output.organization ?? ''} onChange={event => update(index, { organization: event.target.value || null })} /></label>
          <MedicalTimeInput extended label="文书形成时间" value={output.time} onChange={time => update(index, { time })} />
          {!isTimeInputValid(output.time) && <p role="alert">请补全文书时间，或明确选择无法确定。</p>}
          <label className="field"><span>本份文书已核对原文</span><textarea value={output.confirmed_text ?? ''} onChange={event => update(index, { confirmed_text: event.target.value || null })} /></label>
          {episodes.filter(episode => output.page_ids.includes(episode.source_page_id)).map(episode => <label className="field" key={episode.episode_id}><input type="checkbox" checked={output.episode_ids.includes(episode.episode_id)} onChange={event => update(index, { episode_ids: event.target.checked ? [...output.episode_ids, episode.episode_id] : output.episode_ids.filter(id => id !== episode.episode_id) })} />{episode.label} · {episode.episode_type}</label>)}
          {output.proposed_metadata && <p>保留尚待核对的原始元数据提议，重组后可继续在文书归组页确认。</p>}
          <label className="field"><input type="checkbox" checked={output.confirmed} onChange={event => update(index, { confirmed: event.target.checked })} />本人已核对这份目标文书的页序、机构、时间、就诊及原文</label>
        </fieldset>)}
        <section className="card"><p>将有 {sources.filter(value => !outputs.some(output => output.document_id === value.document_id)).length} 份原文书转为历史；{unassigned.length} 个原页暂不归组。</p><label className="field"><span>重组原因</span><textarea value={reason} maxLength={4000} disabled={!edit} onChange={event => setReason(event.target.value)} /></label><button type="submit" className="button primary" disabled={!ready}>一次保存全部文书归属</button></section></>}
    </form>}
    {canRead && (clientRequestId || intentId) && !saved && <section className="card"><h2>原请求恢复</h2><p>只查询或继续原请求，不重新生成提交身份。页面更新或超时后先核对保存结果。</p><button disabled={busy || loading || !canRead} onClick={() => setRevision(value => value + 1)}>查询原保存结果</button>{!request && error instanceof ApiError && error.status === 404 && error.code === 'REGROUP_REQUEST_NOT_FOUND' && <button disabled={busy} onClick={() => { const next = new URLSearchParams(params); next.delete('client_request'); next.delete('request'); setParams(next, { replace: true }); setOutputs([]); setSelected([]); setReason(''); }}>服务端已确认无原请求，重新读取文书</button>}{request && <><p>状态：{request.state}；来源文书 {request.regroup_input.sources.length} 份，目标文书 {request.regroup_input.outputs.length} 份。</p><button disabled={!canWrite || busy || request.state !== 'pending' && !request.may_retry} onClick={() => void commit(request)}>核对后继续原请求</button></>}</section>}
    {canRead && saved && <section role="status" className="card"><h2>全部文书归属已保存</h2><p>保存版本 {saved.version}。逐项事实已生成新版本，原始值、时间和出处保留。</p>{saved.result.outputs.map(value => <p key={value.document_id}><ContextLink to={`/archives/source?document=${value.document_id}&document_version=${value.version_id}`}>查看目标文书版本 {value.version}（{value.page_ids.length} 页）</ContextLink></p>)}{saved.result.retired_document_ids.length > 0 && <p>原文书已转为历史：{saved.result.retired_document_ids.length} 份，原始版本继续保留。</p>}{saved.result.sources.map(value => <p key={value.version_id}><ContextLink to={`/archives/source?document=${value.document_id}&document_version=${value.version_id}`}>查看原文书历史版本 {value.version}</ContextLink></p>)}<ContextLink to={`/archives/batches/${batchId}`}>回到原始批次</ContextLink></section>}
  </UI2FamilyFrame>;
}
