// Actual family CorrectionPage with synthetic records and response-loss recovery.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent, fixtureTime } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', {url:'https://localhost/'});
for(const key of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','HTMLButtonElement','Event'])Object.defineProperty(globalThis,key,{value:dom.window[key],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {Simulate}=await import('react-dom/test-utils');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {AccessProvider}=await import('../src/state/access.tsx');
const {CorrectionPage}=await import('../src/features/records/CorrectionPage.tsx');
const {createCorrectionSubmission}=await import('../src/api/recordSubmission.ts');
const patient=randomUUID(),actor=randomUUID(),recordId=randomUUID(),fieldId=randomUUID();
const shadow=()=>document.querySelector('[data-screen-id]')?.shadowRoot;
const button=text=>[...shadow().querySelectorAll('button')].find(x=>x.textContent.includes(text));
const reason=()=>[...shadow().querySelectorAll('label')].find(x=>x.textContent.includes('更正原因'))?.querySelector('textarea');
const flush=async()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,10));});
const click=async node=>{assert.ok(node,'Synthetic button exists');await act(async()=>Simulate.click(node));await flush();};
const change=async(node,value)=>{assert.ok(node,'Synthetic input exists');await act(async()=>Simulate.change(node,{target:{value,checked:value===true}}));await flush();};
let root,router;
const basePath=`/records/${recordId}/correct`;
async function mount(path=basePath){root=createRoot(document.getElementById('root'));router=createMemoryRouter([
 {path:'/records/:recordId/correct',element:h(AccessProvider,null,h(CorrectionPage))},
 {path:'/records/:recordId',element:h('p',null,'SYNTHETIC SAVED RECORD')}
],{initialEntries:[path]});await act(async()=>root.render(h(RouterProvider,{router})));await flush();await flush();}
async function unmount(){await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();}
const checks=[];
for(const kind of ['observation','report','therapy'])for(const committedBeforeLoss of [false,true]){
 const record={patient_id:patient,record_id:recordId,record_version_id:randomUUID(),version:1,kind,
  origin_type:kind==='report'?'report_transcription':'self_report',capture_method:'manual',quality_status:'pending',
  source_organization:null,correction_reason:null,corrected_at:null,recorded_at:'2026-01-01T00:00:00Z',report:null,attachments:[],allowed_actions:['correct_record'],
  fields:[{field_id:fieldId,field_code:'synthetic_note',concept_code:null,original_name:'SYNTHETIC NOTE',value_type:'text',value_state:'known',
   raw_text:'SYNTHETIC ORIGINAL',raw_numeric:null,categorical_value:null,qualifier:null,raw_unit:null,medical_time_id:null,sample_time_origin:'unknown',
   method:null,reference_interval:null,report_flag:null,verification_states:[],confirmation_id:null,source_anchor:null,medical_time:null}],
  fact_refs:[{field_id:fieldId,fact_id:randomUUID(),version_id:randomUUID(),version:'1'}]};
 let intent=null,receipt=null,posts=0,dropped=false;
 globalThis.fetch=async(path,options={})=>{
  const url=new URL(path,'https://localhost'),body=options.body?JSON.parse(options.body):null;
  if(url.pathname==='/api/auth/bootstrap')return Response.json(syntheticBootstrap());
  if(url.pathname==='/api/session')return Response.json(syntheticSession(actor, patient));
  if(url.pathname==='/api/relations')return Response.json({relations:[{relation_id:randomUUID(),patient_id:patient,display_id:'SYNTHETIC',status:'active',version:'1',valid_from:'2020-01-01T00:00:00Z',valid_until:null,actions:['read_raw','correct_record','view_request','manage_draft']}]});
  if(url.pathname===`/api/records/${recordId}`)return Response.json(record);
  if(url.pathname.endsWith('/field-config'))return Response.json(syntheticFieldConfig(patient, kind, [{field_code:'synthetic_note',value_type:'text',label:'SYNTHETIC NOTE',writable:true,allowed_values:[]}]));
  if(url.pathname===`/api/patients/${patient}/requests`){assert.equal(url.searchParams.get('operation_id'),'record_correct');assert.equal(url.searchParams.get('record_id'),recordId);return Response.json({patient_id:patient,requests:[]});}
  if(url.pathname==='/api/write-intents'){
   assert.equal(body.operation_id,'record_correct');assert.equal(body.payload.expected_version,1);
   if(intent){assert.equal(body.request_id,intent.request_id);assert.deepEqual(body.payload,intent.record_input);}
   else intent={patient_id:patient,intent_id:randomUUID(),request_id:body.request_id,operation_id:body.operation_id,target_key:body.target_key,
     status:'prepared',generation:'1',prepared_at:'2026-10-11T00:00:00Z',record_input:body.payload};
   return Response.json(syntheticIntent(intent.intent_id, intent.request_id));
  }
  if(url.pathname===`/api/records/${recordId}/corrections`){posts++;assert.equal(options.headers['Idempotency-Key'],intent.request_id);assert.deepEqual(body,intent.record_input);
   if(kind==='therapy')assert.equal(body.family_confirmation,true);
   if(!dropped){dropped=true;if(committedBeforeLoss){receipt={patient_id:patient,record_id:recordId,record_version_id:randomUUID(),version:2};record.version=3;record.record_version_id=randomUUID();}throw new TypeError('Synthetic lost correction response');}
   receipt??={patient_id:patient,record_id:recordId,record_version_id:randomUUID(),version:2};return Response.json(receipt);
  }
  if(intent&&url.pathname===`/api/requests/${intent.intent_id}`)return Response.json({...intent,status:receipt?'committed':'prepared',receipt,expires_at:fixtureTime,may_retry:false,resource_id:receipt?.record_version_id??null});
  throw new Error(`Unexpected synthetic correction request ${path}`);
 };
 await mount();for(let wait=0;wait<30&&reason()?.disabled;wait++)await flush();assert.ok(reason());assert.equal(reason().disabled,false,shadow().textContent);await change(reason(),'SYNTHETIC REASON');
 if(kind==='therapy')await change(shadow().querySelector('input[type=checkbox]'),true);
 assert.equal(button('保存更正').disabled,false);await click(button('保存更正'));
 assert.equal(posts,1);assert.equal(reason().disabled,true);assert.ok(button('核对并重试原更正'));
 const savedPath=basePath+router.state.location.search;assert.match(savedPath,/record_intent=/);await unmount();
 await mount(savedPath);assert.equal(reason().disabled,true);
 await click(button(committedBeforeLoss?'查看已确认保存':'核对并重试 '));
 assert.equal(router.state.location.pathname,`/records/${recordId}`);assert.equal(posts,committedBeforeLoss?1:2);
 assert.equal(receipt.version,2);await unmount();
 checks.push(`${kind}: reload after ${committedBeforeLoss?'committed response loss plus later version':'uncommitted response loss'} preserves original correction`);
}
// A malformed success receipt remains uncertain; it can never be cached as success.
{
 let invalid=true,calls=0;const malformedIntentId=randomUUID();
 globalThis.fetch=async (path,options)=>path==='/api/write-intents'?Response.json(syntheticIntent(malformedIntentId,JSON.parse(options.body).request_id)):
  (calls++,Response.json({patient_id:patient,record_id:recordId,record_version_id:randomUUID(),version:invalid?9:2}));
 const frozen=createCorrectionSubmission(patient,recordId,{expected_version:1,reason:'SYNTHETIC',fields:[]});
 await assert.rejects(frozen.submit());assert.equal(frozen.uncertain,true);invalid=false;
 assert.equal((await frozen.submit()).version,2);assert.equal(calls,2);
 checks.push('malformed receipt is not cached or accepted as a successful correction');
}
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_ACTUAL_CORRECTION_PAGE_DOM',checks},null,2));
