import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight } from '../../components/ThreeDIcon';
import equal from 'fast-deep-equal';
import { Image, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2Sheet } from '../../components/UI2Frame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useEndpointAccess } from '../../state/useEndpointAccess';
import { readBatch } from '../../api/archiveBatches';
import { factRequest, prepareFact, readCandidates, readFact, readPageFacts, readPageOriginal, retryFact, submitFact } from '../../api/archiveFacts';
import { FactFieldsEditor, editableTime, type EditableFact } from './FactFieldsEditor';
import { CommonFactTime } from './CommonFactTime';
import { isTimeInputValid } from '../../components/medicalTimeValue';
import type { IntentView, MedicalTime, MedicalTimeInput } from '../../../../frontend-design/types';
import type { FactCandidateSet, FactDecision, FactIntentInput, FactList, FactView, PageOriginalView, PageView, RequestView } from '../../../../frontend-design/v2-types';

interface Decision extends EditableFact { decision: FactDecision['decision'] | 'pending'; reason: string }
function inputTime(time: MedicalTime | null): MedicalTimeInput | null {
  if (!time) return null;
  const { id: _id, patient_id: _patient, ...value } = time;
  return { ...value, instant: null, resolution_status: value.resolution_status === 'resolved' ? 'local_only' : value.resolution_status };
}
function editableFact(fact: FactView): EditableFact {
  const source = fact.field.field, context = fact.context;
  const medical = inputTime(context.medical_time), documented = inputTime(context.documented_time);
  return { field: { client_field_id: source.field_id, field_code: source.field_code, concept_code: source.concept_code,
    dictionary_entry_id: source.dictionary_entry_id, original_name: source.original_name, value_type: source.value_type,
    value_state: source.value_state, raw_text: source.raw_text, raw_numeric: source.raw_numeric,
    categorical_value: source.categorical_value, qualifier: source.qualifier, raw_unit: source.raw_unit, method: source.method,
    reference_interval: source.reference_interval, report_flag: source.report_flag, medical_time: medical,
    sample_time_origin: source.sample_time_origin, anchor_id: fact.source_anchor_id, candidate_field_id: null },
    context: { ...context, medical_time: medical, documented_time: documented }, time: editableTime(medical), documented: editableTime(documented) };
}
export function UI2ConfirmPage() {
  const access = useAccess(), [params] = useSearchParams();
  return <ConfirmContent key={JSON.stringify([access.principalId, access.patientId, params.get('page'), params.get('batch')])} />;
}
function ConfirmContent() {
  const boundary = useEndpointAccess();
  const access = useAccess(), navigate = useNavigate(), [params, setParams] = useSearchParams();
  const pageId = params.get('page'), batchId = params.get('batch'), factId = params.get('fact');
  const [page, setPage] = useState<PageView | null>(null), [source, setSource] = useState<PageOriginalView | null>(null);
  const [candidates, setCandidates] = useState<FactCandidateSet | null>(null), [facts, setFacts] = useState<FactList | null>(null);
  const [choices, setChoices] = useState<PageView[]>([]), [form, setForm] = useState<Record<string, Decision>>({});
  const [tab, setTab] = useState('需要确认'), [loading, setLoading] = useState(false), [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false), [reload, setReload] = useState(0), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [identity, setIdentity] = useState<FactIntentInput | null>(null), [intent, setIntent] = useState<IntentView | null>(null), [request, setRequest] = useState<RequestView | null>(null);
  const [correction, setCorrection] = useState<{ fact: FactView; value: EditableFact; reason: string } | null>(null);
  const [emptyConfirmed, setEmptyConfirmed] = useState(false);
  const pending = useRef<AbortController | null>(null), mounted = useRef(true), running = useRef(false), restored = useRef('');
  const ready = !!access.principalId && !!access.patientId && !access.loading && !access.error && visible;
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const intentId = params.get('request'), clientId = params.get('client_request');
  function wipe() { setPage(null); setSource(null); setCandidates(null); setFacts(null); setChoices([]); setForm({}); setEmptyConfirmed(false); setCorrection(null); setIdentity(null); setIntent(null); setRequest(null); }
  function stopPending() { pending.current?.abort(); pending.current = null; running.current = false; setBusy(false); }
  useEffect(() => {
    mounted.current = true;
    const changed = () => { const shown = document.visibilityState === 'visible'; setVisible(shown); if (!shown) { stopPending(); restored.current = ''; wipe(); } else { void access.refresh(); setReload(value => value + 1); } };
    const freeze = () => { stopPending(); restored.current = ''; wipe(); setVisible(false); };
    const resume = () => { void access.refresh(); setVisible(document.visibilityState === 'visible'); setReload(value => value + 1); };
    document.addEventListener('visibilitychange', changed); document.addEventListener('freeze', freeze); document.addEventListener('resume', resume);
    return () => { mounted.current = false; pending.current?.abort(); document.removeEventListener('visibilitychange', changed); document.removeEventListener('freeze', freeze); document.removeEventListener('resume', resume); };
  }, []);
  async function load(signal: AbortSignal) {
    if (!ready || !access.patientId) return;
    boundary.guard(signal);
    if (!pageId) {
      if (batchId) { const value = await readBatch(batchId, access.patientId, signal); if (boundary.current(signal)) setChoices(value.pages); }
      return;
    }
    const original = await readPageOriginal(pageId, access.patientId, signal);
    boundary.guard(signal);
    const current = original.page;
    if (batchId && current.batch_id !== batchId) throw new Error('原页与当前批次不一致。');
    const values = await Promise.all([current.candidate_set_id ? readCandidates(current, signal) : Promise.resolve(null), readPageFacts(current, access.patientId, signal)]);
    boundary.guard(signal);
    const actual = factId ? await readFact(factId, access.patientId, signal) : null;
    if (actual && actual.page_id !== pageId) throw new Error('更正事实与当前原页不一致。');
    if (!boundary.current(signal)) return;
    setSource(original); setPage(current); setCandidates(values[0]); setFacts(values[1]);
    if (actual) setCorrection({ fact: actual, value: editableFact(actual), reason: '' });
    setForm(previous => Object.keys(previous).length && candidates?.candidate_set_id === values[0]?.candidate_set_id && values[0]?.status !== 'confirmed' ? previous : Object.fromEntries((values[0]?.items ?? []).map(item => {
      const field = item.confirmation?.field ?? item.field, context = item.confirmation?.context ?? item.context;
      return [item.candidate_id, { field: structuredClone(field), context: structuredClone(context),
        time: editableTime(field.medical_time), documented: editableTime(context.documented_time),
        decision: item.confirmation?.decision ?? 'pending', reason: item.confirmation?.reason ?? '' }];
    })));
  }
  function failure(value: unknown) {
    if (!(value instanceof Error)) throw value;
    setError(value);
    if (boundary.deny(value)) wipe();
  }
  useEffect(() => {
    if (!ready || busy) { if (!ready) { stopPending(); wipe(); } return; }
    if (boundary.blocked.current) return;
    const controller = new AbortController(); setLoading(true); setError(null);
    void load(controller.signal).then(() => { if (boundary.current(controller.signal)) setLoading(false); }, value => { if (boundary.current(controller.signal)) { wipe(); setLoading(false); failure(value); } });
    return () => controller.abort();
  }, [ready, pageId, batchId, factId, reload]);
  function run(work: (signal: AbortSignal) => Promise<void>) {
    if (!ready || running.current || !boundary.current()) return;
    running.current = true; setBusy(true); setError(null); const controller = new AbortController(); pending.current = controller;
    void Promise.resolve().then(() => { boundary.guard(controller.signal); return work(controller.signal); }).then(() => { if (mounted.current && boundary.current(controller.signal) && pending.current === controller) { running.current = false; setBusy(false); } }, value => {
      if (mounted.current && boundary.current(controller.signal) && pending.current === controller) { running.current = false; setBusy(false); failure(value); }
    });
  }
  function marker(value: FactIntentInput, prepared?: IntentView) {
    setParams(current => { const next = new URLSearchParams(current); next.set('client_request', value.request_id); next.set('operation', value.operation_id); next.set('target', value.target_key); if (prepared) next.set('request', prepared.intent_id); return next; }, { replace: true });
  }
  async function finish(original: FactIntentInput, prepared: IntentView, signal: AbortSignal) {
    boundary.guard(signal);
    const saved = await submitFact(original, prepared, signal); boundary.guard(signal);
    const recovered = await factRequest(prepared.intent_id, undefined, signal); boundary.guard(signal);
    if (recovered.request.state !== 'committed' || !equal(saved, recovered.request.receipt)) throw new Error('事实回执与重新读取的原请求不一致。');
    await load(signal);
    if (!boundary.current(signal)) return;
    restored.current = prepared.intent_id; setIdentity(null); setIntent(null); setRequest(null); setCorrection(null); setTab('全部项目');
    setParams(current => { const next = new URLSearchParams(current); for (const key of ['request', 'client_request', 'operation', 'target', 'fact']) next.delete(key); return next; }, { replace: true });
    setReload(value => value + 1);
  }
  function submit(original: FactIntentInput) { run(async signal => { setIdentity(original); marker(original); const prepared = await prepareFact(original, signal); if (!boundary.current(signal)) return; setIntent(prepared); marker(original, prepared); await finish(original, prepared, signal); }); }
  function inspect() { run(async signal => {
    const operation = params.get('operation') as FactIntentInput['operation_id'] | null, target = params.get('target');
    if (!intentId && (!clientId || !operation || !target)) throw new Error('原事实请求缺少准确恢复身份。');
    const saved = await factRequest(intentId ?? clientId!, intentId ? undefined : { operation: operation!, target: target! }, signal);
    const expectedTarget = saved.identity.operation_id === 'archive_candidates_confirm' ? `/archive-pages/${pageId}/confirm` : `/archive-facts/${factId}/corrections`;
    if (saved.identity.target_key !== expectedTarget) throw new Error('原事实请求与当前页面目标不一致。');
    if (!boundary.current(signal)) return;
    setIdentity(saved.identity); setRequest(saved.request); marker(saved.identity, { intent_id: saved.request.intent_id } as IntentView);
    if ('items' in saved.identity.payload) {
      const original = await readPageOriginal(pageId!, access.patientId!, signal);
      boundary.guard(signal);
      const actual = await readCandidates(original.page, signal);
      if (!boundary.current(signal)) return;
      setForm(Object.fromEntries(saved.identity.payload.items.map(item => [item.candidate_id, {
        ...item, reason: item.reason ?? '',
        field: item.field ?? actual.items.find(value => value.candidate_id === item.candidate_id)!.field,
        context: item.context ?? actual.items.find(value => value.candidate_id === item.candidate_id)!.context,
        time: editableTime(item.field?.medical_time ?? null), documented: editableTime(item.context?.documented_time ?? null),
      }])));
      setEmptyConfirmed(!saved.identity.payload.items.length);
    } else {
      const body = saved.identity.payload;
      const fact = await readFact(factId!, access.patientId!, signal);
      if (boundary.current(signal)) setCorrection({ fact, reason: body.reason,
        value: { field: body.field, context: body.context, time: editableTime(body.field.medical_time), documented: editableTime(body.context.documented_time) } });
    }
    if (saved.request.state === 'committed') {
      await load(signal); if (!boundary.current(signal)) return;
      restored.current = saved.request.intent_id; setIdentity(null); setRequest(null); setIntent(null); setCorrection(null); setTab('全部项目');
      setParams(current => { const next = new URLSearchParams(current); for (const key of ['request', 'client_request', 'operation', 'target', 'fact']) next.delete(key); return next; }, { replace: true }); setReload(value => value + 1);
    } else if (saved.request.state === 'pending' && !saved.request.may_retry) { const prepared = await prepareFact(saved.identity, signal); if (boundary.current(signal)) setIntent(prepared); }
  }); }
  useEffect(() => {
    const key = intentId ?? clientId;
    if (!ready || busy || loading || pageId && page?.page_id !== pageId || identity || !key || restored.current === key) return;
    let active = true; queueMicrotask(() => { if (active && restored.current !== key) { restored.current = key; inspect(); } });
    return () => { active = false; };
  }, [ready, busy, loading, page, identity, intentId, clientId]);
  const blocked = !ready || boundary.blocked.current || busy || !!identity || !!intentId || !!clientId;
  const canEdit = !blocked && page?.status === 'awaiting_confirmation' && candidates?.status === 'awaiting_confirmation';
  const closeCorrection = useCallback(() => {
    if (!identity && !busy) setParams(current => { const next = new URLSearchParams(current); next.delete('fact'); return next; });
  }, [identity, busy, setParams]);
  function confirm() {
    if (!page || !candidates) return;
    const items = candidates.items.map(item => {
      const value = form[item.candidate_id]; if (!value || value.decision === 'pending') throw new Error('请逐项确认全部候选。');
      return { candidate_id: item.candidate_id, decision: value.decision, field: value.decision === 'excluded' ? null : value.field,
        context: value.decision === 'excluded' ? null : value.context, reason: value.reason || null };
    });
    submit({ operation_id: 'archive_candidates_confirm', target_key: `/archive-pages/${page.page_id}/confirm`, request_id: crypto.randomUUID(), schema_version: '2.0.0',
      payload: { candidate_set_id: candidates.candidate_set_id, expected_page_version: page.version, items } });
  }
  const complete = !!candidates && (candidates.items.length === 0 ? emptyConfirmed : candidates.items.every(item => {
    const value = form[item.candidate_id];
    return !!value && value.decision !== 'pending' && (value.decision === 'excluded' ||
      isTimeInputValid(value.time) && isTimeInputValid(value.documented)) &&
      (!['modified', 'excluded'].includes(value.decision) || !!value.reason.trim());
  }));
  const saveDisabledReason = busy ? '正在核验保存结果，请稍候。' : !ready ? '当前身份或患儿访问关系尚未核验，暂不能保存。'
    : identity || intentId || clientId ? '原请求结果尚待核验，请先查询或继续原请求。'
    : !page || !candidates ? '尚未取得可确认的原件页和候选，请先选择原件并读取候选。'
    : !canEdit ? '当前原件或候选不处于待确认状态，暂不能再次保存。'
    : !complete ? candidates.items.length === 0 ? '请先明确确认此页没有可采用的文字事实。' : '请逐项确认候选，并补齐必需的时间和修订说明。'
    : null;
  const overlay = correction && <UI2Sheet title="更正事实内容" onClose={closeCorrection}><aside className="note amber">更正产生新版本，专业核对需重新完成。</aside><FactFieldsEditor value={correction.value} sourceLabel={`原页 ${correction.fact.page_id ?? '未知'} · 事实版本 ${correction.fact.version}`} disabled={blocked} onChange={value => setCorrection({ ...correction, value })} /><label className="field"><span>更正原因</span><textarea disabled={blocked} value={correction.reason} onChange={event => setCorrection({ ...correction, reason: event.target.value })} /></label><button className="button" disabled={blocked || !correction.reason.trim() || !isTimeInputValid(correction.value.time) || !isTimeInputValid(correction.value.documented)} onClick={() => submit({ operation_id: 'archive_fact_correct', target_key: `/archive-facts/${correction.fact.fact_id}/corrections`, request_id: crypto.randomUUID(), schema_version: '2.0.0', payload: { expected_version: correction.fact.version, field: correction.value.field, context: correction.value.context, reason: correction.reason } })}>保存更正</button></UI2Sheet>;
  return <UI2FamilyFrame title="逐项确认" screenId="UI2-27" note="逐项医学时间、语境与出处不能被共同时间覆盖。" backTo={page ? `/archives/batches/${page.batch_id}` : '/archives/batches'} overlay={overlay}>
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <div className="step-track"><span>提供原件</span><span className="active">逐项确认</span><span>保存资料</span></div>
    <aside className="note amber">同份文书中的不同检查，可能具有不同采样时间。</aside>
    <section className="card"><ContextLink className="list-row" to={`/archives/source${page ? `?page=${page.page_id}&batch=${page.batch_id}` : ''}`}><span className="row-icon"><Image size={30} /></span><span className="row-text"><strong>对照原始文书</strong><small>{page ? '读取准确原件、版本及页码' : '先选择已经接收的原件'}</small></span><ChevronRight size={17} /></ContextLink></section>
    <UI2ReadState loading={access.loading || loading} error={error} onRetry={() => boundary.revalidate(() => { restored.current = ''; setError(null); setReload(value => value + 1); })} />
    {choices.length > 0 && <section className="card">{choices.map((value, index) => <button className="list-row" disabled={blocked || !value.candidate_set_id} key={value.page_id} onClick={() => setParams({ page: value.page_id, batch: value.batch_id })}><span className="row-text"><strong>原页 {index + 1}</strong><small>{value.status}</small></span><ChevronRight size={17} /></button>)}</section>}
    <div className="segments">{['需要确认', '全部项目'].map(label => <button key={label} className={tab === label ? 'active' : ''} onClick={() => setTab(label)}>{label}</button>)}</div>
    {!candidates && <section className="card form-card"><div className="section-heading"><h2>事实内容</h2><span className="badge amber">未提供候选</span></div><label className="field"><span>原始项目名称</span><input disabled placeholder="按原文填写" /></label><div className="field-grid"><label className="field"><span>原始数值</span><input disabled /></label><label className="field"><span>原始单位</span><input disabled /></label></div><label className="field"><span>比较符</span><select disabled>{['等于', '小于', '大于', '小于等于', '大于等于', '未知'].map(label => <option key={label}>{label}</option>)}</select></label><label className="field"><span>此项医学时间</span><input disabled type="datetime-local" /></label><label className="field"><span>时间精度</span><select disabled>{['准确时间', '日期', '月份', '未知'].map(label => <option key={label}>{label}</option>)}</select></label><details><summary>语境、就诊与原文时间</summary><label className="field"><span>事实语境</span><select disabled>{['既往病史', '此次病情', '出院结论', '计划治疗', '实际治疗', '无法确定'].map(label => <option key={label}>{label}</option>)}</select></label><label className="field"><span>原始时间文字</span><input disabled /></label><div className="pair"><span>来源文书与页码</span><strong>待选择</strong></div></details></section>}
    {candidates?.status === 'awaiting_confirmation' && <CommonFactTime form={form} disabled={!canEdit} onApply={setForm} />}
    {candidates?.items.map((item, index) => { const value = form[item.candidate_id]; if (!value || tab === '需要确认' && candidates.status === 'confirmed') return null; return <section className="card form-card" key={item.candidate_id} style={{ overflowWrap: 'anywhere' }}><div className="section-heading"><h2>事实内容 {index + 1}</h2><span className="badge amber">{candidates.status === 'confirmed' ? '本人确认完成' : '等待本人确认'}</span></div>
      <FactFieldsEditor value={value} disabled={!canEdit || value.decision === 'excluded'} sourceLabel={`原件 ${source?.ordinal ?? '未知'} · 页面版本 ${page?.version ?? '未知'} · ${item.document_version_id ?? '文书待归组'}`} onChange={next => setForm(current => ({ ...current, [item.candidate_id]: { ...value, ...next, decision: 'modified' } }))} />
      <details><summary>此项原文与确认决定</summary><p style={{ whiteSpace: 'pre-wrap' }}>{item.source_quote}</p><label className="field"><span>本人确认</span><select disabled={!canEdit} value={value.decision} onChange={event => {
        const decision = event.target.value as Decision['decision'];
        const next: Decision = decision === 'accepted' ? { ...value, field: structuredClone(item.field), context: structuredClone(item.context), time: editableTime(item.field.medical_time), documented: editableTime(item.context.documented_time), decision } : { ...value, decision };
        if (decision === 'unknown') next.field = { ...next.field, value_state: 'unknown', raw_numeric: null, categorical_value: null };
        setForm(current => ({ ...current, [item.candidate_id]: next }));
      }}><option value="pending">请选择确认决定</option><option value="accepted">按候选原文确认</option><option value="modified">本人修订后确认</option><option value="unknown">无法确定原值</option><option value="excluded">排除此项候选</option></select></label><label className="field"><span>修订或排除依据</span><textarea disabled={!canEdit} value={value.reason} onChange={event => setForm(current => ({ ...current, [item.candidate_id]: { ...value, reason: event.target.value } }))} /></label></details>
    </section>; })}
    {facts?.items.map(fact => <section className="card form-card" key={fact.fact_id} style={{ overflowWrap: 'anywhere' }}><h2>{fact.field.field.original_name ?? '已保存事实'}</h2><p>{fact.field.field.raw_numeric ?? fact.field.field.raw_text ?? '原值未知'} {fact.field.field.raw_unit ?? ''}</p><p>原始时间：{fact.context.raw_time_text ?? '未知'}；医学时间精度：{fact.context.medical_time?.precision ?? '未知'}；专业核对：{fact.field.verification_status}。</p><ContextLink className="text-action" to={`/records/versions/${fact.record_version_id}`}>读取保存版本与来源</ContextLink><button className="text-action" disabled={blocked || !fact.allowed_actions.includes('correct_record')} onClick={() => setParams(current => { const next = new URLSearchParams(current); next.set('fact', fact.fact_id); return next; })}>更正此项事实</button></section>)}
    <section className="card"><ContextLink className="list-row" to={`/archives/grouping${page ? `?page=${page.page_id}&batch=${page.batch_id}` : ''}`}><span className="row-text"><strong>文书与就诊归组</strong></span><ChevronRight size={17} /></ContextLink><ContextLink className="list-row" to={`/archives/duplicates${page ? `?batch=${page.batch_id}` : ''}`}><span className="row-text"><strong>处理重复资料</strong></span><ChevronRight size={17} /></ContextLink></section>
    <button className="button" disabled={!canEdit || !complete} aria-describedby={saveDisabledReason ? 'archive-confirm-disabled-reason' : undefined} onClick={confirm}>确认并保存资料</button>{saveDisabledReason && <p id="archive-confirm-disabled-reason" className="footnote" role="status">{saveDisabledReason}</p>}<p className="footnote">本人确认不授予专业核对资格。</p>
    {candidates?.extraction && <details><summary>文书原文及提取范围</summary><p>文书类型候选：{candidates.extraction.document_type}；来源医院候选：{candidates.extraction.organization ?? '未知'}；形成时间原文：{candidates.extraction.documented_time_text ?? '未知'}。</p><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{candidates.extraction.page_text}</p>{candidates.extraction.unreadable_regions.map((item, index) => <p key={index}>未读区域：{item.reason}</p>)}</details>}
    {candidates?.items.length === 0 && <section className="card"><label className="field"><span>确认此页没有可采用的文字事实，保留原件及完整提取结果</span><input type="checkbox" disabled={!canEdit} checked={emptyConfirmed} onChange={event => setEmptyConfirmed(event.target.checked)} /></label></section>}
    {identity && <section className="card form-card"><h2>查询原请求保存结果</h2><p>正文及原请求保持准确，恢复期间停止修改。</p><button className="text-action" disabled={busy} onClick={inspect}>查询原请求</button>{request?.may_retry && <button className="button secondary" disabled={busy} onClick={() => run(async signal => { const prepared = await retryFact(request, signal); if (boundary.current(signal)) { setIntent(prepared); marker(identity, prepared); } })}>重新准备原请求</button>}{intent && <button className="button" disabled={busy} onClick={() => run(signal => finish(identity, intent, signal))}>继续原请求提交</button>}</section>}
  </UI2FamilyFrame>;
}
