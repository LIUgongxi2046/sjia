// Synthetic-only DOM and contract proof; never calls a connected API.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLButtonElement','Event']) Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createElement:h,act}=await import('react'); const {createRoot}=await import('react-dom/client');
const {Simulate}=await import('react-dom/test-utils');
const {ContentDispositionPanel}=await import('../src/features/support/ContentDispositionPanel.tsx');
const {validateContentPlan}=await import('../src/api/contentDisposition.ts');
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`, hash='a'.repeat(64), now='2026-10-11T00:00:00Z';
const rootTarget={kind:'database',adapter:'records.recordversion',object_id:id(3),version:'1'};
const value={schema_version:'content-disposition-plan-view-1',plan_id:id(4),patient_id:id(1),status:'preview',manifest_sha256:hash,
 runtime_execution_enabled:false,approved_at:null,completed_at:null,receipt:null,
 manifest:{schema_version:'content-disposition-manifest-1',patient_id:id(1),policy_id:id(2),policy_version:1,policy_sha256:hash,
 registry_sha256:hash,preservation_sha256:hash,mode:'erase_selected_content',environment:'synthetic-only',hold_id:id(5),hold_version:1,
 roots:[rootTarget],items:[{...rootTarget,row_sha256:hash}],obligations:['physical_database_storage','backup_copies'],created_at:now}};
validateContentPlan(value);
assert.throws(()=>validateContentPlan({...value,patient_id:id(7)}));
assert.throws(()=>validateContentPlan({...value,receipt:{schema_version:'content-disposition-receipt-1',logical_payload_erased:true,all_copies_erased:true}}));
assert.throws(()=>validateContentPlan({...value,manifest:{...value.manifest,arbitrary_sql:'DELETE'}}));
let calls=[],next=value,delayed=null,permissions=0;
globalThis.fetch=async(path,options={})=>{calls.push({path,options}); if(next==='delay')return new Promise(resolve=>{delayed=resolve;}); if(next==='denied')return Response.json({code:'CASE_ACTION_DENIED',message:'Synthetic denied'},{status:403});return Response.json(next);};
const root=createRoot(document.getElementById('root')); const onPermission=()=>{permissions++;};
const flush=()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
const render=active=>act(async()=>root.render(h(ContentDispositionPanel,{active,onPermission})));
const inputFor=text=>[...document.querySelectorAll('label')].find(e=>e.querySelector('span')?.textContent===text)?.querySelector('input');
const labels=['患儿 UUID','已批准的内容政策 UUID','保留例外审查 UUID','保留例外审查版本','与准确清单摘要匹配的 SourceRevision UUID'];
const change=(index,text)=>act(async()=>Simulate.change(inputFor(labels[index]),{target:{value:text}}));
const targets=text=>act(async()=>Simulate.change(document.querySelector('textarea'),{target:{value:text}}));
const click=text=>act(async()=>{const button=[...document.querySelectorAll('button')].find(e=>e.textContent.includes(text));assert.ok(button,`button ${text}`);button.click();});
const fill=async()=>{await change(0,id(1));await change(1,id(2));await change(2,id(5));await change(3,'1');await targets(JSON.stringify([rootTarget]));};
await render(true);assert.equal(calls.length,0);await fill();assert.equal(calls.length,0);
await click('只生成');await flush();assert.equal(calls.length,1);assert.ok(document.body.textContent.includes('当前运行环境未启用'));
assert.ok(document.body.textContent.includes('没有默认处置政策或保留期限'));assert.ok(document.body.textContent.includes('WAL'));
const approvalButton=[...document.querySelectorAll('button')].find(e=>e.textContent.includes('批准这份'));assert.ok(approvalButton.disabled);
await change(4,id(6));await act(async()=>Simulate.change(document.querySelector('input[type=checkbox]'),{target:{checked:true}}));
next={...value,status:'approved',approved_at:now};await click('批准这份');await flush();
assert.ok(calls.at(-1).path.endsWith('/approve'));assert.equal(JSON.parse(calls.at(-1).options.body).expected_manifest_sha256,hash);
assert.ok([...document.querySelectorAll('button')].find(e=>e.textContent.includes('执行已批准')).disabled);
next={...next,runtime_execution_enabled:true};await click('查询原申请');await flush();
next={...next,status:'logical_erasure_complete',completed_at:now,receipt:{schema_version:'content-disposition-receipt-1',manifest_sha256:hash,journal_sequence:1,logical_payload_erased:true,all_copies_erased:false,unverified_copy_obligations:['physical_database_storage','backup_copies'],proof_scope:'current_database_payload_and_controlled_local_path_absence'}};
await click('执行已批准');await flush();assert.ok(calls.at(-1).path.endsWith('/execute'));assert.ok(document.body.textContent.includes('全副本已清除：否'));
await change(0,id(7));assert.ok(!document.body.textContent.includes(hash));
await fill();next='delay';await click('只生成');const pending=calls.at(-1);await targets('[]');assert.ok(pending.options.signal.aborted);
await act(async()=>delayed(Response.json(value)));await flush();assert.ok(!document.body.textContent.includes(hash));
await fill();next='denied';await click('只生成');await flush();assert.equal(permissions,1);assert.equal(document.querySelector('textarea').value,'');
await fill();next=value;await click('只生成');await flush();await act(async()=>window.dispatchEvent(new Event('pagehide')));
assert.ok([...document.querySelectorAll('input')].every(e=>e.value===''||e.type==='checkbox'));assert.equal(document.querySelector('textarea').value,'');
next={...value,status:'erasing',runtime_execution_enabled:true,approved_at:now};
await act(async()=>Simulate.change(inputFor('恢复已有申请 UUID（刷新或提交结果不确定时使用）'),{target:{value:id(4)}}));
await click('按标识恢复');await flush();assert.ok(calls.at(-1).path.endsWith('/plans/'+id(4)));assert.ok(document.body.textContent.includes('正在处理，需查询原结果'));
assert.ok([...document.querySelectorAll('button')].some(e=>e.textContent.includes('继续处理原申请')));
await fill();await render(false);await render(true);assert.equal(document.querySelector('input').value,'');
await act(async()=>root.unmount());
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_NONVISUAL_DOM',checks:['no implicit query or policy','strict typed manifest and receipts','explicit preview then approval then gated execution','exact manifest bound across actions','all-copy claim cannot become true','scope changes and late responses cleared','permission and pagehide/inactive clear private form']},null,2));
