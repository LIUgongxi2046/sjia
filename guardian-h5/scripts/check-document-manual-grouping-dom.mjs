// Actual grouping page with deterministic API seams, no server/provider or browser.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {randomUUID} from 'node:crypto';
import {JSDOM} from 'jsdom';
registerHooks({load(url,context,next){
 if(url.endsWith('/state/access.tsx'))return {format:'module',source:'export function useAccess(){return globalThis.__groupAccess;}',shortCircuit:true};
 if(url.endsWith('/api/archiveBatches.ts'))return {format:'module',source:'export async function readBatch(){return globalThis.__groupFixture.batch;}',shortCircuit:true};
 if(url.endsWith('/api/archiveFacts.ts'))return {format:'module',source:'export async function readPageOriginal(){return {page:globalThis.__groupFixture.batch.pages[0]};} export async function readCandidates(){globalThis.__groupFixture.candidateReads++;return null;}',shortCircuit:true};
 if(url.endsWith('/api/archiveDocuments.ts'))return {format:'module',source:`export async function readDocumentInputs(){return {episodes:[]};} export async function readDocumentRequest(){return globalThis.__groupFixture.request;} export const findDocumentRequest=readDocumentRequest; export async function prepareDocumentRequest(value){globalThis.__groupFixture.prepared=value;throw new Error('Synthetic stop before submission');} export async function retryDocumentRequest(){globalThis.__groupFixture.retries++;return {intent_id:'synthetic-intent'};} export async function submitDocumentRequest(){throw new Error('Unexpected submit');} export async function readArchiveDocument(){throw new Error('Unexpected document read');} export function documentPayloadMatches(){return false;}`,shortCircuit:true};
 if(/\.(png|webp|jpg|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};
 return next(url,context);
}});
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
for(const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLTextAreaElement','ShadowRoot'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Unexpected network');};
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');const {Simulate}=await import('react-dom/test-utils');const {createMemoryRouter,RouterProvider}=await import('react-router');const {UI2DocumentCreatePage}=await import('../src/features/archives/UI2DocumentCreatePage.tsx');
const patient=randomUUID(),batchId=randomUUID(),pageId=randomUUID();
globalThis.__groupAccess={principalId:randomUUID(),patientId:patient,accountStatus:'active',loading:false,error:null,scopeVersion:1,hasCurrentAccess:()=>true,refresh:async()=>{},relations:[{patient_id:patient,status:'active',actions:['read_raw','write_record'],display_id:'SYNTHETIC'}]};
const basePage={page_id:pageId,batch_id:batchId,status:'uploaded',file_status:'available',task:null,attempts:[],candidate_set_id:null,error_code:null};
let root,router;
const screen=()=>document.querySelector('[data-screen-id]').shadowRoot;
const save=()=>[...screen().querySelectorAll('button')].find(node=>node.textContent==='确认归组');
const flush=async()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,5));});
async function mount(status,page={},recovery=false,noPage=false,intake_mode='ocr'){
 const payload={batch_id:batchId,page_ids:[pageId],document_type:'unknown',organization:null,documented_time:null,episode_ids:[],confirmed_text:null,proposed_metadata:{episodes:[],grouping_note:null}};
 globalThis.__groupFixture={batch:{batch_id:batchId,patient_id:patient,intake_mode,status,pages:[{...basePage,...page}]},candidateReads:0,retries:0,prepared:null,request:{intent_id:'synthetic-intent',request_id:randomUUID(),operation_id:'archive_document_create',target_key:`/patients/${patient}/archive-documents`,document_input:payload,state:'failed',may_retry:true,receipt:null}};
 root=createRoot(document.getElementById('root'));router=createMemoryRouter([{path:'*',element:h(UI2DocumentCreatePage)}],{initialEntries:[`/archives/grouping?batch=${batchId}${noPage?'':`&page=${pageId}`}${recovery?'&request=synthetic-intent':''}`]});
 await act(async()=>root.render(h(RouterProvider,{router})));await flush();await flush();
}
async function unmount(){await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();}
for(const state of ['receiving','cancelled']){
 await mount(state);assert.equal(save().disabled,true);assert.ok(screen().textContent.includes(state==='receiving'?'尚未进入可归组状态':'该批次已取消'));
 assert.ok(screen().querySelector(`a[href*="/archives/source?page=${pageId}"]`));assert.equal(globalThis.__groupFixture.prepared,null);await unmount();
}
await mount('partial');assert.equal(save().disabled,false);await unmount();
await mount('receiving',{},false,false,'manual');assert.equal(save().disabled,false);assert.ok(screen().textContent.includes('未使用自动识别'));assert.equal(globalThis.__groupFixture.candidateReads,0);assert.ok(!screen().textContent.includes('已有专业就诊引用'));await unmount();
for(const page of [{status:'queued'},{status:'confirmed'},{status:'archived'},{file_status:'quarantined'},{task:{status:'failed'}},{candidate_set_id:randomUUID()},{attempts:[{status:'failed',finished_at:'2026-01-01T00:00:00Z'}]}]){
 await mount('receiving',page,false,false,'manual');assert.equal(save().disabled,true);assert.equal(screen().querySelector('input[type=checkbox]').disabled,true);assert.equal(globalThis.__groupFixture.candidateReads,0);await unmount();
}
await mount('cancelled',{},false,false,'manual');assert.equal(save().disabled,true);await unmount();
const failed={status:'failed',task:{status:'failed'},candidate_set_id:randomUUID(),error_code:'SYNTHETIC_OCR_FAILURE'};
await mount('failed',failed);assert.equal(save().disabled,false);assert.equal(globalThis.__groupFixture.candidateReads,0);assert.ok(screen().textContent.includes('不会改变识别失败状态'));assert.ok(screen().textContent.includes('SYNTHETIC_OCR_FAILURE'));
const originalState=JSON.stringify(globalThis.__groupFixture.batch);
const manual=[...screen().querySelectorAll('label')].find(node=>node.textContent.startsWith('本人确认原文')).querySelector('textarea');
await act(async()=>Simulate.change(manual,{target:{value:'SYNTHETIC human transcribed text'}}));await act(async()=>Simulate.click(save()));await flush();
assert.equal(globalThis.__groupFixture.prepared.payload.confirmed_text,'SYNTHETIC human transcribed text');assert.equal(JSON.stringify(globalThis.__groupFixture.batch),originalState);assert.equal(globalThis.__groupFixture.candidateReads,0);await unmount();
await mount('failed',failed,false,true);const choice=screen().querySelector('input[type=checkbox]');assert.equal(choice.disabled,false);assert.equal(save().disabled,true);await act(async()=>Simulate.change(choice,{target:{checked:true}}));assert.equal(save().disabled,false);await unmount();
for(const page of [{...failed,task:null},{...failed,task:{status:'running'}},{...failed,task:{status:'uncertain'}},{...failed,attempts:[{status:'running',finished_at:null}]},{...failed,attempts:[{status:'failed',finished_at:null}]},{...failed,file_status:'disposed'},...['queued','processing','uncertain','cancel_requested','cancelled'].map(status=>({status}))]){
 await mount('partial',page);assert.equal(save().disabled,true,JSON.stringify(page));assert.equal(screen().querySelector('input[type=checkbox]').disabled,true);await unmount();
}
await mount('receiving',{},true);assert.ok(screen().textContent.includes('原请求结果待核验'));const recover=[...screen().querySelectorAll('button')].find(node=>node.textContent==='查询原归组请求');assert.equal(recover.disabled,false);const retry=[...screen().querySelectorAll('button')].find(node=>node.textContent==='重新准备原归组请求');assert.equal(retry.disabled,false);await act(async()=>Simulate.click(retry));await flush();assert.equal(globalThis.__groupFixture.retries,1);await unmount();
assert.equal(requests,0);dom.window.close();console.log('PASS actual grouping page: receiving/cancelled new requests blocked, originals reachable; terminal failed intact source manual text only; active/uncertain/missing task/disposed sources blocked; no failed candidate read/status rewrite; original request recovery retained');
