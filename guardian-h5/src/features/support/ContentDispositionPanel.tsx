import { useEffect, useRef, useState } from 'react';
import { ContentDispositionWorkspace } from './ContentDispositionWorkspace';
import { ApiError } from '../../api/client';
import { approveContentPlan, executeContentPlan, previewContentPlan, readContentPlan } from '../../api/contentDisposition';
import type { ContentPlan } from '../../api/contentDisposition';
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const modes = { retain: '继续保留', revoke_access: '撤销访问并保留内容', erase_selected_content: '擦除选定内容' };
const states = { preview: '清单待批准', approved: '准确清单已批准', retained: '已记录保留', access_revoked: '访问已撤销', erasing: '正在处理，需查询原结果', logical_erasure_complete: '当前数据库内容及受控文件处置完成' };
export function ContentDispositionPanel({ active, onPermission }: { active: boolean; onPermission: () => void }) {
  const [patient, setPatient] = useState(''), [policy, setPolicy] = useState(''), [hold, setHold] = useState('');
  const [holdVersion, setHoldVersion] = useState(''), [targets, setTargets] = useState(''), [source, setSource] = useState('');
  const [familyRequest, setFamilyRequest] = useState<string | null>(null);
  const [plan, setPlan] = useState<ContentPlan | null>(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [reviewed, setReviewed] = useState(false), [uncertain, setUncertain] = useState(false);
  const operation = useRef<AbortController | null>(null), generation = useRef(0), running = useRef(false);
  useEffect(() => { generation.current += 1; operation.current?.abort(); setPlan(null); setReviewed(false); setUncertain(false); setBusy(false); return () => { generation.current += 1; operation.current?.abort(); }; }, [active, patient, policy, hold, holdVersion, targets, familyRequest]);
  useEffect(() => {
    const clear = () => { generation.current += 1; operation.current?.abort(); setPatient(''); setPolicy(''); setHold(''); setHoldVersion(''); setTargets(''); setSource(''); setFamilyRequest(null); setPlan(null); setReviewed(false); setUncertain(false); setError(''); setBusy(false); };
    if (!active) clear(); window.addEventListener('pagehide', clear); return () => window.removeEventListener('pagehide', clear);
  }, [active]);
  async function run(work: (signal: AbortSignal) => Promise<ContentPlan>, mutation = false) {
    if (!active || running.current) return;
    const revision = generation.current, controller = new AbortController(); operation.current = controller;
    running.current = true; setBusy(true); setError(''); if (mutation) setUncertain(true);
    try { const next = await work(controller.signal); if (controller.signal.aborted || revision !== generation.current) return; setPlan(next); setReviewed(false); setUncertain(false); }
    catch (failure) { if (controller.signal.aborted || revision !== generation.current) return; setError(failure instanceof Error ? failure.message : '处置请求未完成。'); if (failure instanceof ApiError && [401, 403].includes(failure.status)) { setPlan(null); setPatient(''); setPolicy(''); setHold(''); setHoldVersion(''); setTargets(''); setSource(''); setFamilyRequest(null); setUncertain(false); onPermission(); } }
    finally { running.current = false; if (revision === generation.current) setBusy(false); }
  }
  function preview() {
    try { const parsed: unknown = JSON.parse(targets); if (!Array.isArray(parsed)) throw new Error('目标须为逐项 JSON 数组。');
      void run(signal => previewContentPlan(patient, policy, { schema_version: 'content-disposition-request-1', targets: parsed, hold_id: hold, hold_version: Number(holdVersion) }, signal, familyRequest));
    } catch (failure) { setError(failure instanceof Error ? failure.message : '目标格式无效。'); }
  }
  if (!active) return null;
  return <section className="card form-card" style={{ overflowWrap: 'anywhere' }}><h2>按明确政策处置内容</h2>
    <p>没有默认处置政策或保留期限。预览不会擦除内容；批准清单与执行分别核验。数据库逻辑处置不证明 WAL、备份或模型服务副本已清除。</p>
    <ContentDispositionWorkspace active={active} plan={plan} onPermission={onPermission}
      onRecover={id => void run(signal => readContentPlan(id, signal))}
      onScopeChange={p => { generation.current += 1; operation.current?.abort(); setPatient(p); setPolicy(''); setHold(''); setHoldVersion(''); setTargets(''); setSource(''); setFamilyRequest(null); setPlan(null); setReviewed(false); setUncertain(false); setBusy(false); }}
      onSelect={(p,pol,h,v,t,application) => { setFamilyRequest(application ?? null); setPatient(p); setPolicy(pol); setHold(h); setHoldVersion(v); setTargets(t); }} />
    {familyRequest && <p>关联本人申请 {familyRequest}。目标和政策须与原申请完全相同，复制载荷只在单独批准的清单中展开。</p>}
    <details><summary>准确申请范围（也可手工核对录入）</summary>
    <label className="field"><span>患儿 UUID</span><input value={patient} disabled={busy} onChange={event => setPatient(event.target.value.trim())} /></label>
    <label className="field"><span>已批准的内容政策 UUID</span><input value={policy} disabled={busy} onChange={event => setPolicy(event.target.value.trim())} /></label>
    <label className="field"><span>保留例外审查 UUID</span><input value={hold} disabled={busy} onChange={event => setHold(event.target.value.trim())} /></label>
    <label className="field"><span>保留例外审查版本</span><input value={holdVersion} disabled={busy} inputMode="numeric" onChange={event => setHoldVersion(event.target.value)} /></label>
    <label className="field"><span>准确目标数组（数据库 adapter、object_id、version，或文件 file_id、object_version）</span><textarea value={targets} disabled={busy} onChange={event => setTargets(event.target.value)} /></label>
    </details>
    <button className="button secondary" disabled={busy || !uuid.test(patient) || !uuid.test(policy) || !uuid.test(hold) || !/^[1-9][0-9]*$/.test(holdVersion) || !targets.trim()} onClick={preview}>只生成处置清单</button>
    {error && <p role="alert">{error}</p>}
    {plan && <><p>申请 {plan.plan_id}</p><p>本次准确处置患儿：{plan.patient_id}</p>{plan.manifest.family_request_id && <p>本人申请：{plan.manifest.family_request_id}</p>}<p>状态：{states[plan.status]}。模式：{modes[plan.manifest.mode]}。</p>
      {!plan.runtime_execution_enabled && <p role="status">当前运行环境未启用内容处置执行。</p>}
      <p>批准范围指纹：{plan.manifest_sha256}</p><p>保留关系及元数据规范：{plan.manifest.preservation_sha256}</p>
      <p>清单包含 {plan.manifest.items.length} 个准确对象。复制载荷可能让清单大于最初选定范围。</p>
      <details><summary>逐项核对目标与版本</summary>{plan.manifest.items.map((item, index) => <p key={index}>{item.adapter ?? '文件'} · {item.object_id ?? item.file_id} · {item.version ?? item.object_version}</p>)}</details>
      <p>未获全副本清除证明：{plan.manifest.obligations.join('、')}。</p>
      {plan.status === 'preview' && <><label className="field"><span>与准确清单摘要匹配的 SourceRevision UUID</span><input value={source} onChange={event => setSource(event.target.value.trim())} /></label>
        <label><input type="checkbox" checked={reviewed} onChange={event => setReviewed(event.target.checked)} />已核对全部对象、版本、保留元数据和未完成副本义务</label>
        <button className="button" disabled={busy || uncertain || !reviewed || !uuid.test(source)} onClick={() => void run(signal => approveContentPlan(plan, source, signal), true)}>批准这份准确清单</button></>}
      {['approved', 'erasing'].includes(plan.status) && <button className="button" disabled={busy || uncertain || !plan.runtime_execution_enabled} onClick={() => void run(signal => executeContentPlan(plan, signal), true)}>{plan.status === 'erasing' ? '继续处理原申请的' : '执行已批准的'}{modes[plan.manifest.mode]}</button>}
      {uncertain && <p role="status">提交结果尚未确认，请查询原申请后继续。</p>}
      {plan.receipt && <p>载荷已擦除：{plan.receipt.logical_payload_erased ? '是' : '否'}；全副本已清除：否。{plan.receipt.retained_until && `保留复查范围至 ${plan.receipt.retained_until}。`}</p>}
      <button className="text-action" disabled={busy} onClick={() => void run(signal => readContentPlan(plan.plan_id, signal))}>查询原申请与回执</button>
    </>}
  </section>;
}
