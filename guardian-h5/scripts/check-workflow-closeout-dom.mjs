// Synthetic nonvisual execution of real record recovery hooks and both voice modes.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent, fixtureTime } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { randomUUID } from 'node:crypto';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'https://localhost/' });
for (const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','HTMLButtonElement','Event']) Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
Object.defineProperty(window,'isSecureContext',{value:true});
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {Simulate}=await import('react-dom/test-utils');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {AccessProvider}=await import('../src/state/access.tsx');
const {useRecordSubmission}=await import('../src/state/useRecordSubmission.ts');
const {RecordRecoveryPanel}=await import('../src/components/RecordRecoveryPanel.tsx');
const {VoiceEntry}=await import('../src/components/VoiceEntry.tsx');
const id='00000000-0000-4000-8000-000000000001',source='00000000-0000-4000-8000-000000000002';
let root,router,recovered=0,hookError=null;
const flush=async()=>act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
const click=async node=>{assert.ok(node,'button present');await act(async()=>Simulate.click(node));await flush();};
const change=async(node,value)=>{await act(async()=>Simulate.change(node,{target:{value}}));await flush();};
const button=text=>[...document.querySelectorAll('button')].find(node=>node.textContent.includes(text));
const base=(path)=>path==='/api/auth/bootstrap'?syntheticBootstrap():path==='/api/session'?syntheticSession(id):path==='/api/relations'?{relations:[{relation_id:id,patient_id:id,display_id:'SYNTHETIC',status:'active',version:'1',valid_from:'2020-01-01T00:00:00Z',valid_until:null,actions:['read_raw','write_record','view_request']}]}:null;
async function mount(element,path='/form'){root=createRoot(document.getElementById('root'));router=createMemoryRouter([{path:'/form',element:h(AccessProvider,null,element)}],{initialEntries:[path]});await act(async()=>root.render(h(RouterProvider,{router})));await flush();}
async function unmount(){await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();}
function RecoveryForm(){const submission=useRecordSubmission({beginSubmission:()=>({}),cancelSubmission:()=>{},markSubmitted:()=>{}},'observation');return h('main',null,h(RecordRecoveryPanel,{submission,onRecovered:()=>{recovered++;}}),h('input',{'aria-label':'new input',disabled:submission.pending}),h('button',{disabled:submission.pending,onClick:()=>submission.submit({kind:'observation',capture_method:'manual',fields:[{field_code:'temp',value_type:'decimal',value_state:'known',raw_numeric:'0'}]}).catch(error=>{hookError=error;})},'save new'))}
for(const commitBeforeLoss of [false,true]){
 const intentId=randomUUID();let intent=null,receipt=null,posts=0,first=true;recovered=0;hookError=null;
 globalThis.fetch=async(path,options={})=>{
  const standard=base(path);if(standard)return Response.json(standard);
  if(path.includes('/requests?'))return Response.json({patient_id:id,requests:[]});
  if(path==='/api/write-intents'){const body=JSON.parse(options.body);if(intent)assert.deepEqual(body.payload,intent.record_input);else intent={patient_id:id,intent_id:intentId,request_id:body.request_id,operation_id:'record_create',target_key:body.target_key,status:'prepared',generation:'1',prepared_at:'2026-10-11T00:00:00Z',record_input:body.payload};return Response.json(syntheticIntent(intentId,intent.request_id));}
  if(path===`/api/requests/${intentId}`)return Response.json({...intent,status:receipt?'committed':'prepared',receipt,expires_at:fixtureTime,may_retry:false,resource_id:receipt?.record_id??null});
  if(path===`/api/patients/${id}/records`){posts++;assert.equal(options.headers['Idempotency-Key'],intent.request_id);assert.deepEqual(JSON.parse(options.body),intent.record_input);if(first){first=false;if(commitBeforeLoss)receipt={patient_id:id,record_id:randomUUID(),record_version_id:randomUUID(),version:1};throw new TypeError('Synthetic lost response');}receipt??={patient_id:id,record_id:randomUUID(),record_version_id:randomUUID(),version:1};return Response.json(receipt);}
  throw new Error(`Unexpected synthetic path ${path}`);
 };
 await mount(h(RecoveryForm));await click(button('save new'));assert.ok(hookError);const url='/form'+router.state.location.search;assert.match(url,/record_intent=/);assert.equal(document.querySelector('input').disabled,true);await unmount();
 await mount(h(RecoveryForm),url);assert.equal(document.querySelector('input').disabled,true);await click(button(commitBeforeLoss?'查看已确认保存':'核对并重试'));assert.equal(recovered,1);assert.equal(posts,commitBeforeLoss?1:2);assert.ok(receipt);await unmount();
}
let microphoneRequests=0,transcriptions=0,lastBusy=false,applied=[];
Object.defineProperty(navigator,'mediaDevices',{value:{getUserMedia:async()=>{microphoneRequests++;return {getTracks:()=>[{stop(){}}]};}},configurable:true});
class Recorder{static isTypeSupported(){return true;}state='inactive';constructor(stream,options){this.mimeType=options.mimeType;}start(){this.state='recording';}stop(){this.state='inactive';this.ondataavailable?.({data:new Blob(['SYNTHETIC AUDIO'],{type:this.mimeType})});this.onstop?.();}}
globalThis.MediaRecorder=Recorder;
globalThis.fetch=async(path,options={})=>{const standard=base(path);if(standard)return Response.json(standard);if(path.endsWith('/voice-input-options'))return Response.json({patient_id:id,state:'available',reason_code:null,field_config_id:source,provider_config_id:source,source_options:[{source_revision_id:source,family_authorization_id:source,external_authorization_id:source}],original_options:[],input_modes:['LOCAL_AUDIO_CONFIRMED_TEXT']});if(path.endsWith('/voice-transcription')){transcriptions++;assert.equal(options.headers['X-Use-Authorization'],source);return Response.json({text:'SYNTHETIC TRANSCRIPT',transcription_evidence_id:source});}throw new Error(path);};
for(const structured of [false,true]){
 await mount(h(VoiceEntry,{structured,onBusyChange:value=>{lastBusy=value;},onApply:(text,metadata)=>applied.push({text,metadata})}));
 assert.equal(button('开始语音输入').disabled,true);assert.equal(button('选择已有录音').disabled,true);await change(document.querySelector('select'),source);assert.equal(button('开始语音输入').disabled,false);await click(button('开始语音输入'));await click(button('结束并核对'));assert.equal(document.querySelector('textarea').value,'SYNTHETIC TRANSCRIPT');assert.equal(button('正在转写'),undefined);await click(button(structured?'提取候选并核对':'确认加入症状说明'));assert.equal(lastBusy,false);assert.equal(applied.at(-1).text,'SYNTHETIC TRANSCRIPT');if(structured)assert.equal(applied.at(-1).metadata.transcriptionAuthorizationId,source);await unmount();
}
assert.equal(microphoneRequests,2);assert.equal(transcriptions,2);
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_NONVISUAL_DOM',checks:['reload before commit recovers exact frozen payload and request key','reload after lost committed response reads receipt without duplicate POST','recovery blocks new writes until original is resolved','structured and unstructured audio require approved source','both audio modes leave transcribing and release busy state after confirmation']},null,2));
