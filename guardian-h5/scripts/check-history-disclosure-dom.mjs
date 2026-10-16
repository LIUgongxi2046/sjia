// Actual history page, disclosure, endpoint transport and schema; synthetic reads only.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({load(url,context,nextLoad){
 if(url.endsWith('/state/access.tsx'))return {format:'module',source:'export function useAccess(){return globalThis.__historyAccess}',shortCircuit:true};
 if(url.endsWith('/api/useRecordResource.ts'))return {format:'module',source:'export function useRecordResource(){return globalThis.__historyResource}',shortCircuit:true};
 if(/\.(png|jpg|webp|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};
 return nextLoad(url,context);
}});
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true});
for(const name of ['window','document','navigator','HTMLElement','HTMLButtonElement','Element','Node','ShadowRoot'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {UI2HistoryPage}=await import('../src/features/records/UI2HistoryPage.tsx');
const actor=randomUUID(),patient=randomUUID(),record=randomUUID(),v1=randomUUID(),v2=randomUUID();
const base={patient_id:patient,record_id:record,record_version_id:v1,version:1,kind:'observation',origin_type:'self_report',capture_method:'manual',quality_status:'valid',source_organization:null,correction_reason:'SYNTHETIC REASON',corrected_at:'2026-10-07T10:02:00Z',recorded_at:'2026-10-07T10:01:00Z',report:null,fields:[],attachments:[{file_id:randomUUID(),ordinal:1}],fact_refs:[]};
const field={field_id:randomUUID(),field_code:'temp',concept_code:null,original_name:'SYNTHETIC ALLOWED FIELD',value_type:'decimal',value_state:'known',raw_numeric:'38.2',raw_text:null,categorical_value:null,qualifier:'eq',raw_unit:'°C',medical_time_id:null,medical_time:null,sample_time_origin:'observation',method:null,reference_interval:null,report_flag:null,verification_states:[],confirmation_id:null,source_anchor:null};
const history={patient_id:patient,record_id:record,current_version:'2',versions:[v1,v2].map((id,i)=>({record_version_id:id,version:String(i+1),origin_type:'self_report',quality_status:'valid',recorded_at:base.recorded_at}))};
let root,router,calls,queue,checks=0;
const page=()=>document.querySelector('[data-screen-id]')?.shadowRoot??document;
const flush=async()=>act(async()=>{await new Promise(r=>setTimeout(r,15));});
const buttons=()=>[...page().querySelectorAll('button[aria-expanded]')];
const button=n=>buttons().find(b=>b.textContent.includes(`版本 ${n} 详情`));
const click=async n=>{await act(async()=>button(n).click());await flush();};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
async function render(){await act(async()=>root.render(h(RouterProvider,{router})));await flush();}
async function setup(){
 calls=[];queue=[];Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});
 globalThis.__historyAccess={principalId:actor,patientId:patient,scopeVersion:1,loading:false,error:null,relations:[],refresh:async()=>{},hasCurrentAccess:(a,p,s)=>a===globalThis.__historyAccess.principalId&&p===globalThis.__historyAccess.patientId&&s===globalThis.__historyAccess.scopeVersion&&!globalThis.__historyAccess.loading&&!globalThis.__historyAccess.error};
 globalThis.__historyResource={data:structuredClone(history),loading:false,error:null,reload(){}};
 globalThis.fetch=async(path,options)=>{calls.push({path,options});assert.ok([`/api/record-versions/${v1}`,`/api/record-versions/${v2}`].includes(path),'No originals, audit, attachments or other reads');assert.equal(options.method,'GET');return queue.length?await queue.shift():Response.json({...base,record_version_id:path.endsWith(v1)?v1:v2,version:path.endsWith(v1)?1:2});};
 root=createRoot(document.getElementById('root'));router=createMemoryRouter([{path:'/records/:recordId/history',element:h(UI2HistoryPage)}],{initialEntries:[`/records/${record}/history`]});await render();
}
async function teardown(){await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();}
function pass(s){checks++;console.log(`PASS ${s}`);}
try{
 await setup();assert.equal(calls.length,0);assert.equal(buttons().length,2);assert.equal(page().querySelectorAll('.history-version-details').length,0);pass('initial page makes zero detail requests');
 await click(1);assert.equal(calls.length,1);assert.match(page().textContent,/SYNTHETIC REASON/);assert.match(page().textContent,/操作者信息尚未由数据契约提供/);assert.equal(page().querySelectorAll(`a[href="/records/versions/${v1}"]`).length,1);assert.ok(!page().textContent.includes('首次登记'));pass('explicit expansion reads once, no invented author, preserves full-version link');
 await click(1);assert.equal(calls[0].options.signal.aborted,true);assert.equal(page().querySelectorAll('.history-version-details').length,0);await click(1);assert.equal(calls.length,2);pass('collapse aborts and discards; reopening reads fresh');await teardown();
 await setup();const first=deferred();queue.push(first.promise);await click(1);await click(2);assert.equal(calls[0].options.signal.aborted,true);assert.equal(page().querySelectorAll('.history-version-details').length,1);await act(async()=>first.resolve(Response.json({...base,correction_reason:'STALE FIRST'})));await flush();assert.ok(!page().textContent.includes('STALE FIRST'));assert.equal(button(1).getAttribute('aria-expanded'),'false');pass('rapid version switch aborts and ignores late prior response');await teardown();
 await setup();queue.push(Response.json({...base,correction_reason:null,corrected_at:null,fields:[field],fact_refs:[{field_id:field.field_id,fact_id:randomUUID(),version_id:randomUUID(),version:'1'}]}));await click(1);assert.match(page().textContent,/未提供更正原因/);assert.match(page().textContent,/未提供更正时间/);assert.match(page().textContent,/SYNTHETIC ALLOWED FIELD/);assert.match(page().textContent,/不表示全部字段/);assert.ok(!page().textContent.includes('38.2'));pass('partial authorized fields show status only; null reason/time stay unknown');await teardown();
 for(const patch of [{patient_id:randomUUID()},{record_id:randomUUID()},{record_version_id:randomUUID()},{version:99}]){await setup();queue.push(Response.json({...base,...patch}));await click(1);assert.ok(page().querySelector('[role="alert"]'));assert.ok(!page().textContent.includes('SYNTHETIC REASON'));await teardown();}pass('wrong patient, record, version ID and version ordinal all rejected');
 await setup();queue.push(Response.json({code:'DENIED',message:'denied',retryable:false,recovery:'none'},{status:403}));await click(1);assert.match(page().textContent,/当前权限无法读取此版本/);assert.equal(calls.length,1);assert.ok(!page().textContent.includes('SYNTHETIC REASON'));pass('403 is local, clears data and causes no automatic retry or auth loop');await teardown();
 for(const [label,change,restore] of [
 ['principal',()=>__historyAccess={...__historyAccess,principalId:randomUUID()},()=>__historyAccess={...__historyAccess,principalId:actor}],
 ['patient',()=>__historyAccess={...__historyAccess,patientId:randomUUID()},()=>__historyAccess={...__historyAccess,patientId:patient}],
 ['scope',()=>__historyAccess={...__historyAccess,scopeVersion:2},()=>__historyAccess={...__historyAccess,scopeVersion:1}],
 ['access loading',()=>__historyAccess={...__historyAccess,loading:true},()=>__historyAccess={...__historyAccess,loading:false}],
 ['access error',()=>__historyAccess={...__historyAccess,error:'denied'},()=>__historyAccess={...__historyAccess,error:null}],
 ['history loading',()=>__historyResource={...__historyResource,loading:true},()=>__historyResource={...__historyResource,loading:false}],
 ['history error',()=>__historyResource={...__historyResource,error:new Error('failed')},()=>__historyResource={...__historyResource,error:null}],
 ]){await setup();const gate=deferred();queue.push(gate.promise);await click(1);change();await act(async()=>router.navigate(`/records/${record}/history?state=${encodeURIComponent(label)}`));await flush();assert.equal(calls[0].options.signal.aborted,true);await act(async()=>gate.resolve(Response.json({...base,correction_reason:'STALE SENSITIVE'})));await flush();assert.ok(!page().textContent.includes('STALE SENSITIVE'));restore();await act(async()=>router.navigate(`/records/${record}/history`));await flush();assert.equal(page().querySelectorAll('.history-version-details').length,0);assert.equal(calls.length,1);pass(`${label} change clears request/data and does not restore stale expansion`);await teardown();}
 await setup();await click(1);assert.match(page().textContent,/SYNTHETIC REASON/);await act(async()=>router.navigate(`/records/${randomUUID()}/history`));await flush();assert.ok(!page().textContent.includes('SYNTHETIC REASON'));assert.equal(calls[0].options.signal.aborted,true);pass('record navigation removes already-rendered details and rejects stale list');await teardown();
 for(const key of ['principalId','patientId','scopeVersion','loading','error']){await setup();await click(1);__historyAccess={...__historyAccess,[key]:key==='scopeVersion'?2:key==='loading'?true:key==='error'?'denied':randomUUID()};await act(async()=>router.navigate(`/records/${record}/history?loaded=${key}`));await flush();assert.ok(!page().textContent.includes('SYNTHETIC REASON'));assert.equal(calls[0].options.signal.aborted,true);await teardown();}pass('loaded data clears on principal/patient/scope/loading/error transitions');
 await setup();await click(1);__historyResource={...__historyResource,data:{...history,versions:history.versions.map(v=>v.record_version_id===v1?{...v,version:'9'}:v)}};await act(async()=>router.navigate(`/records/${record}/history?ordinal=9`));await flush();assert.equal(page().querySelectorAll('.history-version-details').length,0);assert.equal(calls.length,1);__historyResource={...__historyResource,data:history};await act(async()=>router.navigate(`/records/${record}/history?ordinal=1`));await flush();assert.equal(page().querySelectorAll('.history-version-details').length,0);assert.equal(calls.length,1);pass('changed version ordinal closes disclosure without automatic read or resurrection');await teardown();
 await setup();const hidden=deferred();queue.push(hidden.promise);await click(1);await act(async()=>{Object.defineProperty(document,'visibilityState',{value:'hidden',configurable:true});document.dispatchEvent(new window.Event('visibilitychange'));});assert.equal(calls[0].options.signal.aborted,true);await act(async()=>hidden.resolve(Response.json({...base,correction_reason:'STALE HIDDEN'})));await flush();await act(async()=>{Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});document.dispatchEvent(new window.Event('visibilitychange'));});await flush();assert.equal(page().querySelectorAll('.history-version-details').length,0);assert.ok(!page().textContent.includes('STALE HIDDEN'));assert.equal(calls.length,1);pass('hidden clears and aborts, visibility restore never reopens/prefetches');await teardown();
 await setup();const late=deferred();queue.push(late.promise);await click(1);await teardown();assert.equal(calls[0].options.signal.aborted,true);await act(async()=>late.resolve(Response.json(base)));await flush();assert.equal(document.getElementById('root').textContent,'');pass('unmount aborts and ignores late response');
 await setup();__historyResource={...__historyResource,data:{...history,versions:[history.versions[0],history.versions[0]]}};await act(async()=>router.navigate(`/records/${record}/history?duplicate=1`));await flush();assert.equal(buttons().length,0);assert.match(page().textContent,/包含重复版本/);assert.equal(calls.length,0);pass('duplicate version IDs fail closed without duplicate disclosures');await teardown();
 console.log(`PASS ${checks} history disclosure DOM scenarios; no browser, services, database or external data`);
}finally{if(root?._internalRoot)await act(async()=>root.unmount());router?.dispose();dom.window.close();}
