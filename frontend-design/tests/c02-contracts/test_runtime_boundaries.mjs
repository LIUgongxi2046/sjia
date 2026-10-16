/** Actual adapters and validator, synthetic fetch only. No HTTP service, browser, DB or provider. */
import '../../../guardian-h5/scripts/registration-test-loader.mjs';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const { validateApiValue }=await import('../../../guardian-h5/src/api/schema.ts');
const { readAssessment, readAssessmentCreationOptions }=await import('../../../guardian-h5/src/api/assessments.ts');
const { readDynamicOptions }=await import('../../../guardian-h5/src/api/dynamic.ts');
const { projectAssessmentObservationTime, projectAssessmentCreationOptions, projectDynamicCreationOptions }=await import('../../../guardian-h5/src/api/assessmentWire.ts');
const schema='v2-protocol.schema.json#/$defs/', u='00000000-0000-4000-8000-000000000001', v='00000000-0000-4000-8000-000000000002', stamp='2026-10-01T12:00:00Z', digest='a'.repeat(64);
let checks=0; const pass=()=>checks++;
for(const c of JSON.parse(fs.readFileSync(new URL('../../../.planning-work/c02-contract-tests/synthetic-contract-cases.json',import.meta.url),'utf8'))) {
 if(c.valid) assert.doesNotThrow(()=>validateApiValue(schema+c.name,c.value),c.name+'.'+c.label);
 else assert.throws(()=>validateApiValue(schema+c.name,c.value),c.name+'.'+c.label);
 pass();
}
assert.throws(()=>validateApiValue(schema+'AssessmentCreationQuery',{known_at:'bad',mode:'current'}));pass();
const time={raw_text:null,precision:'unknown',local_year:null,local_month:null,end_year:null,end_month:null,relative_text:null,local_date:null,local_datetime:null,timezone_name:null,utc_offset_minutes:null,instant:null,resolution_status:'unknown'};
const config={config_id:u,config_key:'synthetic',version:'1',checksum:digest};
function options(dynamic, t) {
 const candidate={fact_version_id:u,field_id:u,record_version_id:u,fact_version:'1',original_name:null,raw_value:null,raw_unit:null,qualifier:null,context_type:'unknown',medical_time:structuredClone(t),available_at:null};
 const variable={variable_code:'synthetic',concept_code:'synthetic',candidates:[candidate,{...candidate,fact_version_id:v,medical_time:null}]};
 const plan={kind:dynamic?'dynamic':'classification',purpose:'synthetic',rule:config,window:config,verification:config,selection_variables:[dynamic?{...variable,input_role:'current'}:variable],selection_evidence:[],prior_selections:[]};
 if(dynamic) Object.assign(plan,{input_rule:config,use_authorization_ids:[u]});
 return {schema_version:dynamic?'dynamic-creation-options-2':'assessment-creation-options-2',patient_id:u,display_id:'SYNTHETIC ONLY',known_at:stamp,mode:'current',plans:[plan],observation_periods:[
  {observation_period_id:u,period_id:u,version:'1',label:null,start_time:structuredClone(t),end_time:null},
  {observation_period_id:v,period_id:v,version:'1',label:null,start_time:null,end_time:structuredClone(t)},
 ]};
}
function freeze(value) {if(value && typeof value==='object') {Object.freeze(value);Object.values(value).forEach(freeze);}return value;}
const clone=structuredClone;
let response, calls=[];
globalThis.fetch=async (url, request={})=>{calls.push({url,method:request.method});return Response.json(response);};
for(const dynamic of [false,true]) {
 const read=dynamic?readDynamicOptions:readAssessmentCreationOptions, project=dynamic?projectDynamicCreationOptions:projectAssessmentCreationOptions;
 for(const identity of [{},{id:u},{patient_id:u},{id:u,patient_id:u}]) {
  const raw=options(dynamic,{...time,...identity}), before=clone(raw), frozen=freeze(raw);
  const projected=project(frozen);assert.deepEqual(raw,before);assert.notEqual(projected,raw);
  assert.notEqual(projected.plans,raw.plans);assert.notEqual(projected.observation_periods,raw.observation_periods);
  assert.notEqual(projected.plans[0].selection_variables[0].candidates,raw.plans[0].selection_variables[0].candidates);
  assert.deepEqual(projected,options(dynamic,time));pass();
  response=raw;const actual=await read(u,stamp,'current');assert.deepEqual(actual,options(dynamic,time));assert.deepEqual(raw,before);pass();
 }
 const locations=[x=>x.plans[0].selection_variables[0].candidates[0],x=>x.observation_periods[0],x=>x.observation_periods[1]];
 for(const [i,loc] of locations.entries()) for(const field of Object.keys(time)) {
  response=options(dynamic,{...time,id:u,patient_id:u});delete loc(response)[['medical_time','start_time','end_time'][i]][field];
  await assert.rejects(()=>read(u,stamp,'current'),/Schema/);pass();
 }
 for(const [i,loc] of locations.entries()) for(const id of ['id','patient_id']) {
  response=options(dynamic,clone(time));loc(response)[['medical_time','start_time','end_time'][i]][id]=null;await assert.rejects(()=>read(u,stamp,'current'),/Schema/);pass();
 }
 for(const [i,loc] of locations.entries()) {
  response=options(dynamic,clone(time));delete loc(response)[['medical_time','start_time','end_time'][i]];
  await assert.rejects(()=>read(u,stamp,'current'),/Schema/);pass();
 }
 response=options(dynamic,null);assert.deepEqual(await read(u,stamp,'current'),response);pass();
}
assert.equal(projectAssessmentObservationTime(null),null);pass();
// Exact non-null values, including partial/unknown representations, are copied unchanged.
for(const precision of ['unknown','year','month','month_range','relative','date','minute','second']) {
 const raw={...time,precision,raw_text:'synthetic raw',local_year:2026,local_month:10,end_year:2026,end_month:12,relative_text:'synthetic relative',local_date:'2026-10-01',local_datetime:'2026-10-01T12:00:00',timezone_name:'Etc/UTC',utc_offset_minutes:0,instant:stamp,id:u,patient_id:v};
 const expected={...raw};delete expected.id;delete expected.patient_id;
 assert.deepEqual(projectAssessmentObservationTime(freeze(raw)),expected);pass();
}
const view={schema_version:'static-assessment-view-2',assessment_id:u,patient_id:u,kind:'classification',assessment_at:stamp,known_at:stamp,mode:'current',
 task:{task_id:u,task_type:'assessment',request_id:null,accepted_at:stamp,status:'completed',status_version:'1',poll_after_ms:null,allowed_actions:[],result_type:'assessment',result_ref:u,error_code:null},
 validity:'invalid',computation_status:'failed',missing_items:[],rule_id:u,window_policy_id:u,input_sha256:digest,implementation_sha256:digest,
 context_states:{population:'unknown',therapy:'unknown',clinical_finding:'unknown',late_result:'unknown'},limitations:[],applicability:'insufficient',computed_at:stamp,inputs:[],exclusions:[],clinical_selections:[],output:null,reason_code:'SYNTHETIC_INVALID'};
response=clone(view);assert.deepEqual(await readAssessment(u),view);pass();
response=clone(view);delete response.clinical_selections;assert.doesNotThrow(()=>validateApiValue(schema+'AssessmentView',response));await assert.rejects(()=>readAssessment(u),/评估缺少保存的明确选择状态/);pass();
for(const value of [null,{},'bad',[null],[{}],[{variable_code:'bad'}],[{variable_code:'synthetic',fact_version_id:u,reason:'synthetic',evidence_ref:'synthetic',prior_task_id:null,actor_id:u,saved_at:stamp,extra:true}]]) {
 response={...clone(view),clinical_selections:value};await assert.rejects(()=>readAssessment(u),/Schema/);pass();
}
response=clone(view);delete response.patient_id;await assert.rejects(()=>readAssessment(u),/Schema/);pass();
response=clone(view);delete response.clinical_selections;response.task.task_type='alert_evaluation';await assert.rejects(()=>readAssessment(u),/静态评估任务状态无效/);pass();
const selection={variable_code:'synthetic',fact_version_id:u,reason:'synthetic',evidence_ref:'synthetic',prior_task_id:null,actor_id:u,saved_at:stamp};
const valid={...view,validity:'valid',reason_code:null,computation_status:'completed',applicability:'applicable',clinical_selections:[selection],
 inputs:[{variable_code:'synthetic',input_snapshot:{role:'current',value:'1',unit:null,reference:{fact_version_id:u,record_version_id:u,field_id:u,verification_id:u,normalization_id:null,medical_time_id:u,source_revision_id:u,document_version_id:null,page_id:null}}}],
 output:{classification_conditions:{},classification_result:'not_meets',ms_contributions:null,ms_total:null,ms_threshold_result:null}};
response=valid;assert.deepEqual(await readAssessment(u),valid);pass();
assert.ok(calls.every(c=>c.method==='GET'));pass();
console.log(JSON.stringify({status:'PASS',checks,synthetic_fetch_calls:calls.length,real_network_calls:0,scope:'Actual Schema, static/dynamic options projections and checked AssessmentView read'},null,2));
