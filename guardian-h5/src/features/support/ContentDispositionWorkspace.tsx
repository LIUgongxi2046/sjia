import { useEffect, useRef, useState } from 'react';
import { ApiError } from '../../api/client';
import { listContentPatients, readContentOptions, listContentHistory, readContentLifecycle, reviewContentPlan, listContentFamilyRequests } from '../../api/contentDisposition';
import type { ContentPatients, ContentOptions, ContentHistory, ContentPlan, ContentLifecycle, ContentFamilyApplication } from '../../api/contentDisposition';
const modes={retain:'继续保留',revoke_access:'撤销访问',erase_selected_content:'擦除选定内容'};
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function ContentDispositionWorkspace({active,plan,onSelect,onRecover,onScopeChange,onPermission}:{active:boolean;plan:ContentPlan|null;onSelect:(patient:string,policy:string,hold:string,version:string,targets:string,application?:string)=>void;onRecover:(id:string)=>void;onScopeChange:(patient:string)=>void;onPermission:()=>void}) {
 const [patients,setPatients]=useState<ContentPatients|null>(null),[patient,setPatient]=useState(''),[options,setOptions]=useState<ContentOptions|null>(null);
 const [applications,setApplications]=useState<ContentFamilyApplication[]|null>(null);
 const [policy,setPolicy]=useState(''),[selected,setSelected]=useState<number[]>([]),[history,setHistory]=useState<ContentHistory|null>(null);
 const [recover,setRecover]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false),[life,setLife]=useState<ContentLifecycle|null>(null);
 const [reviewSource,setReviewSource]=useState(''),[reviewText,setReviewText]=useState('');
 const operation=useRef<AbortController|null>(null),generation=useRef(0),running=useRef(false);
 function clear() {generation.current++;operation.current?.abort();setPatients(null);setPatient('');setOptions(null);setPolicy('');setSelected([]);setHistory(null);setApplications(null);setRecover('');setError('');setBusy(false);setLife(null);setReviewSource('');setReviewText('');}
 useEffect(()=>{if(!active)clear();window.addEventListener('pagehide',clear);return()=>{window.removeEventListener('pagehide',clear);generation.current++;operation.current?.abort();};},[active]);
 useEffect(()=>{setLife(null);setReviewSource('');setReviewText('');if(plan){setPatient(plan.patient_id);setOptions(null);setSelected([]);setPolicy('');setHistory(null);setApplications(null);generation.current++;operation.current?.abort();setBusy(false);}},[plan?.plan_id]);
 async function run<T,>(work:(signal:AbortSignal)=>Promise<T>,apply:(value:T)=>void) {
  if(!active||running.current)return;const controller=new AbortController(),revision=generation.current;operation.current=controller;running.current=true;setBusy(true);setError('');
  try{const value=await work(controller.signal);if(!controller.signal.aborted&&revision===generation.current)apply(value);}
  catch(e){if(!controller.signal.aborted&&revision===generation.current){setError(e instanceof Error?e.message:'查询未完成。');if(e instanceof ApiError&&[401,403].includes(e.status)){clear();onPermission();}}}
  finally{running.current=false;if(revision===generation.current)setBusy(false);}
 }
 function scope(value:string){onScopeChange(value);generation.current++;operation.current?.abort();setBusy(false);setPatient(value);setOptions(null);setPolicy('');setSelected([]);setHistory(null);setApplications(null);}
 if(!active)return null;
 return <div className="form-card"><h3>选择身份与资料，或恢复已有申请</h3>
  <button className="button secondary" disabled={busy} onClick={()=>void run(listContentPatients,setPatients)}>查询有权管理的患儿</button>
  {patients&&<label className="field"><span>选择患儿身份</span><select value={patient} disabled={busy} onChange={e=>scope(e.target.value)}><option value="">请选择，不自动选中</option>{patients.patients.map(p=><option key={p.patient_id} value={p.patient_id}>{p.display_id} · {p.patient_id}</option>)}</select></label>}
  <button className="text-action" disabled={busy||!patient} onClick={()=>{onScopeChange(patient);setSelected([]);setPolicy('');setOptions(null);void run(s=>readContentOptions(patient,s),setOptions);}}>查询已批准政策与可选资料</button>
  <button className="text-action" disabled={busy||!patient} onClick={()=>void run(s=>listContentHistory(patient,s),setHistory)}>查询历史申请与到期复查</button>
  <button className="text-action" disabled={busy||!patient} onClick={()=>void run(s=>listContentFamilyRequests(patient,s),v=>setApplications(v.requests))}>查询本人提交的临床内容申请</button>
  {applications&&<div><h4>本人申请与准确范围</h4>{!applications.length&&<p>尚无本人申请。</p>}{applications.map(a=><article key={a.request_id}><p>申请 {a.request_id} · {a.status} · {a.targets.length} 个记录版本</p>{a.targets.map(t=><p key={t.record_version_id}>{t.record_version_id} · 版本 {t.version}</p>)}{a.linked_plan_id?<button className="text-action" disabled={busy} onClick={()=>onRecover(a.linked_plan_id!)}>打开关联计划</button>:<button className="button secondary" disabled={busy||!options?.hold?.eligible||!options.policies.some(p=>p.policy_id===a.policy_id)} onClick={()=>{const hold=options!.hold!;onSelect(patient,a.policy_id,hold.hold_id,String(hold.version),JSON.stringify(a.targets.map(t=>({kind:'database',adapter:'records.recordversion',object_id:t.record_version_id,version:t.version})),null,2),a.request_id);}}>载入原申请范围并单独预览</button>}</article>)}</div>}
  {options&&<><p>当前对象：{options.display_id}。仅展示身份、版本和资料类别，不展示临床正文。</p>
   {!options.policies.length&&<p role="status">没有当前有效且已批准的内容政策；不能提交执行。</p>}
   <label className="field"><span>明确选择已批准政策</span><select value={policy} disabled={busy} onChange={e=>setPolicy(e.target.value)}><option value="">请选择，不使用默认政策</option>{options.policies.map(p=><option key={p.policy_id} value={p.policy_id}>{modes[p.mode]} · v{p.version} · {p.access_purpose} · {p.policy_id}</option>)}</select></label>
   <p>保留例外审查：{options.hold ? `${options.hold.state} · v${options.hold.version} · 有效至 ${options.hold.valid_until}`:'没有有效审查'}。{options.hold&&!options.hold.eligible&&'当前审查不允许执行。'}</p>
   <details><summary>选择准确资料与历史版本（{selected.length} 项）</summary>{options.targets.map((item,index)=><label className="field" key={`${item.target.adapter}:${item.target.object_id??item.target.file_id}`}><span><input type="checkbox" checked={selected.includes(index)} disabled={busy} onChange={e=>setSelected(old=>e.target.checked?[...old,index]:old.filter(i=>i!==index))}/>{item.label} · {item.target.adapter??'local_object'} · {item.target.object_id??item.target.file_id} · 版本 {item.target.version??item.target.object_version}{item.record_id&&` · 记录 ${item.record_id}`}</span></label>)}</details>
   <button className="button secondary" disabled={busy||!policy||!selected.length||!options.hold?.eligible} onClick={()=>{const h=options.hold!;onSelect(patient,policy,h.hold_id,String(h.version),JSON.stringify(selected.map(i=>options.targets[i].target),null,2));}}>将已选范围载入申请表</button>
  </>}
  {history&&<div><h4>历史申请与复查队列</h4>{!history.plans.length&&<p>尚无申请。</p>}{history.plans.map(p=><article key={p.plan_id}><p>{modes[p.mode]} · {p.status} · {p.item_count} 项 · {p.created_at}</p><p>{p.lifecycle.review_due?'已到复查时间':'下次复查'}：{p.lifecycle.next_recheck_at}。未完成副本义务 {p.lifecycle.unresolved_copy_obligations.length} 项。</p><button className="text-action" disabled={busy} onClick={()=>onRecover(p.plan_id)}>打开申请 {p.plan_id}</button></article>)}</div>}
  <label className="field"><span>恢复已有申请 UUID（刷新或提交结果不确定时使用）</span><input value={recover} disabled={busy} onChange={e=>setRecover(e.target.value.trim())}/></label>
  <button className="button secondary" disabled={busy||!uuid.test(recover)} onClick={()=>onRecover(recover)}>按标识恢复原申请</button>
  {plan&&<><button className="text-action" disabled={busy} onClick={()=>void run(s=>readContentLifecycle(plan.plan_id,s),setLife)}>查询保留依据、到期与副本义务</button>
   {life&&<><p>依据 SourceRevision：{life.retention_basis_source_revision_id}</p><p>{life.review_evidence_status==='invalid_or_revoked'?'续审证据已失效，必须重新审查':life.review_due?'已到复查时间':'下次复查'}：{life.next_recheck_at}。</p><p>回执至少保留至 {life.receipt_keep_until}；元数据至少保留至 {life.metadata_keep_until}。{(life.receipt_expiry_review_due||life.metadata_expiry_review_due)&&'保留期已到，需核查备份与重试窗口证明；不会自动清除防复活记录。'}</p>
    {life.unresolved_copy_obligations.map(o=><p key={o.kind}>{o.kind}：尚无可验证的副本清除协议；复查期限 {o.review_due_at}。</p>)}
    <details><summary>登记已批准的续审证明（不会再次执行擦除）</summary><p>证明须绑定本申请、政策摘要、审查人、下一复查时间和明确原因；没有自动延长期限。</p><label className="field"><span>续审 SourceRevision UUID</span><input value={reviewSource} onChange={e=>setReviewSource(e.target.value.trim())}/></label><label className="field"><span>content-disposition-review-1 已批准证明 JSON</span><textarea value={reviewText} onChange={e=>setReviewText(e.target.value)}/></label><button className="button secondary" disabled={busy||!uuid.test(reviewSource)||!reviewText.trim()} onClick={()=>{try{const body:unknown=JSON.parse(reviewText);void run(s=>reviewContentPlan(plan,body,reviewSource,s),setLife);}catch{setError('续审证明 JSON 无效。');}}}>核验并保存续审事实</button></details>
   </>}
  </>}
  {error&&<p role="alert">{error}</p>}
 </div>;
}
