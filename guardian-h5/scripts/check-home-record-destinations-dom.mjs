// Synthetic DOM/transport regressions. Actual home, selection, visuals, record
// transport/schema and detail hook; access/assessment/archive body are doubles.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {registerHooks} from 'node:module';
import {JSDOM} from 'jsdom';
registerHooks({load(url,context,nextLoad){
 if(url.endsWith('/state/access.tsx'))return {format:'module',source:`import {useSyncExternalStore} from 'react'; export function useAccess(){return useSyncExternalStore(globalThis.__homeSubscribe,()=>globalThis.__homeAccess);}`,shortCircuit:true};
 if(url.endsWith('/api/useFamilyResource.ts'))return {format:'module',source:'export function useFamilyResource(){return {data:null,error:null,loading:false,reload(){}}}',shortCircuit:true};
 if(url.endsWith('/features/home/HealthArchive.tsx'))return {format:'module',source:`import {createElement as h} from 'react'; import {navigationMemory} from '../../state/navigation'; export function HealthArchive(){return h('p',{'data-archive-category':navigationMemory.archive.category},'Synthetic category destination');}`,shortCircuit:true};
 if(/\.(png|jpg|webp|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};
 return nextLoad(url,context);
}});
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/home',pretendToBeVisual:true});
for(const name of ['window','document','navigator','HTMLElement','HTMLButtonElement','Element','Node','ShadowRoot'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
Object.defineProperty(document,'visibilityState',{value:'visible',configurable:true});
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
HTMLElement.prototype.getClientRects=function(){return [{x:0,y:0,width:100,height:30}];};
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {UI2HomePage}=await import('../src/features/home/UI2HomePage.tsx');
const {UI2RecordDetailPage,UI2RecordVersionPage}=await import('../src/features/records/UI2RecordDetailPage.tsx');
const {selectLatestMedicalEntries:latest,projectHomeObservationEntries:project,observationEntries,recordSnapshotPath}=await import('../src/features/home/observationSelection.ts');
const {navigationMemory}=await import('../src/state/navigation.ts');
const {validateRecordSearchPage}=await import('../src/api/recordSearchSchema.ts');
const patient=randomUUID(),principal=randomUUID(),timestamp='2026-10-11T00:00:00Z';
const time=overrides=>({precision:'date',raw_text:null,local_date:'2026-10-05',local_datetime:null,instant:null,timezone_name:'Asia/Shanghai',resolution_status:'local_only',local_year:null,local_month:null,end_year:null,end_month:null,relative_text:null,utc_offset_minutes:null,...overrides});
function field(code,medical_time,overrides={}){return {field_id:randomUUID(),field_code:code,concept_code:null,original_name:null,value_type:'categorical',value_state:'known',raw_text:null,raw_numeric:null,categorical_value:'yes',qualifier:null,raw_unit:null,medical_time_id:medical_time?randomUUID():null,sample_time_origin:'observation',method:null,reference_interval:null,report_flag:null,verification_states:[],confirmation_id:null,source_anchor:null,medical_time,...overrides};}
function record(fields){const record_id=randomUUID();return {record_id,kind:'observation',status:'active',current_version:1,created_at:timestamp,detail:{patient_id:patient,record_id,record_version_id:randomUUID(),kind:'observation',version:1,origin_type:'self_report',capture_method:'manual',quality_status:'valid',source_organization:null,correction_reason:null,corrected_at:null,recorded_at:timestamp,report:null,fields,attachments:[],fact_refs:fields.map(value=>({field_id:value.field_id,fact_id:randomUUID(),version_id:randomUUID(),version:'1'})),allowed_actions:[]}};}
const older=record([field('temp',time({local_date:'2026-10-01'}),{value_type:'decimal',raw_numeric:'36.000000001',raw_unit:'C',categorical_value:null,qualifier:'eq'}),field('rash',time({local_date:'2026-10-09'}))]);
const newer=record([field('temp',time({raw_text:'原文：十月五日'}),{value_type:'decimal',raw_numeric:'37.123456789',raw_unit:'SYNTHETIC-UNIT',categorical_value:null,qualifier:'ge'})]);
const fixturesBefore=JSON.stringify([older,newer]);
assert.equal(latest(observationEntries([older,newer],'temp')).entries[0].recordVersionId,newer.detail.record_version_id);
const unknown=field('temp',null,{value_state:'unknown',categorical_value:null});
assert.equal(latest([{field:unknown}]).state,'single');
assert.equal(latest([{field:unknown},{field:newer.detail.fields[0]}]).state,'indeterminate');
assert.equal(latest([{field:newer.detail.fields[0]},{field:{...newer.detail.fields[0],field_id:randomUUID()}}]).state,'same_time');
const month=field('temp',time({precision:'month',local_date:null,local_year:2026,local_month:10}));
assert.equal(latest([{field:month},{field:newer.detail.fields[0]}]).state,'indeterminate');
for(const resolution_status of ['ambiguous','invalid','unknown'])assert.equal(latest([{field:field('temp',time({resolution_status}))},{field:newer.detail.fields[0]}]).state,'indeterminate');
const instant=at=>field('temp',time({precision:'second',resolution_status:'resolved',local_date:null,instant:at}));
const earlier=instant('2026-10-05T00:00:00.000001Z'),later=instant('2026-10-05T08:00:01.000001+08:00');
assert.equal(latest([{field:later},{field:earlier}]).entries[0].field,later);
assert.equal(latest([{field:earlier},{field:instant('2026-10-05T00:00:01.000000Z')}]).state,'indeterminate','one-microsecond precision overlap must not round into a definite latest');
assert.equal(latest([{field:earlier},{field:instant('2026-10-05T08:00:00.000001+08:00')}]).state,'same_time');
assert.equal(latest([{field:field('temp',time({timezone_name:null}))},{field:newer.detail.fields[0]}]).state,'indeterminate');
const explicitOffset=field('temp',time({timezone_name:null,utc_offset_minutes:480}));
const offsetBefore=JSON.stringify(explicitOffset);
assert.equal(latest([{field:explicitOffset},{field:newer.detail.fields[0]}]).state,'same_time','explicit +08:00 date matches the qualified Asia/Shanghai date');
const offsetLater=field('temp',time({timezone_name:null,utc_offset_minutes:480,local_date:'2026-10-06'}));
assert.equal(latest([{field:explicitOffset},{field:offsetLater}]).entries[0].field,offsetLater);
for(const utc_offset_minutes of [1440,-1440,480.5])assert.equal(latest([{field:field('temp',time({timezone_name:null,utc_offset_minutes}))},{field:newer.detail.fields[0]}]).state,'indeterminate');
const offsetUtc=field('temp',time({timezone_name:null,utc_offset_minutes:0}));
assert.equal(latest([{field:offsetUtc},{field:field('temp',time({timezone_name:'UTC'}))}]).state,'same_time','zero offset is explicit, not missing');
assert.equal(JSON.stringify(explicitOffset),offsetBefore,'offset comparison must not modify the source time');
const unqualifiedDate=record([field('temp',time({local_date:'2026-10-01',timezone_name:null}),{value_type:'decimal',raw_numeric:'36.9',raw_unit:'C',categorical_value:null})]);
const exactTime=time({precision:'second',local_date:null,local_datetime:'2026-10-09T08:30:00',instant:'2026-10-09T00:30:00Z',timezone_name:'Asia/Shanghai',utc_offset_minutes:480,resolution_status:'resolved'});
const dated=record([field('temp',exactTime,{value_type:'decimal',raw_numeric:'38.456789',raw_unit:'C',categorical_value:null})]);
const datedTie=record([field('temp',{...exactTime},{value_type:'decimal',raw_numeric:'37.5',raw_unit:'C',categorical_value:null})]);
const mixedEntries=observationEntries([unqualifiedDate,dated,record([unknown])],'temp');
const projected=project(mixedEntries);
assert.equal(projected.all.state,'indeterminate','the global chronology gate must still retain unknown candidates');
assert.equal(projected.display.entries[0].recordVersionId,dated.detail.record_version_id);
assert.equal(projected.scopedToTimed,true);assert.equal(projected.pendingTimeCount,2);
assert.deepEqual(project([...mixedEntries].reverse()).display,projected.display,'input order cannot change the dated projection');
const allUnknown=project(observationEntries([unqualifiedDate,record([unknown])],'temp'));
assert.equal(allUnknown.scopedToTimed,false);assert.equal(allUnknown.display.state,'indeterminate');assert.equal(allUnknown.display.entries.length,2);
const tiedProjection=project(observationEntries([unqualifiedDate,dated,datedTie],'temp'));
assert.equal(tiedProjection.all.state,'indeterminate');assert.equal(tiedProjection.display.state,'same_time');assert.equal(tiedProjection.display.entries.length,2);
const overlappingProjection=project([{field:month},{field:newer.detail.fields[0]},{field:unknown}]);
assert.equal(overlappingProjection.display.state,'indeterminate','qualifying time does not erase overlapping precision');

const listeners=new Set();globalThis.__homeSubscribe=fn=>{listeners.add(fn);return()=>listeners.delete(fn);};
const initialAccess=()=>({principalId:principal,patientId:patient,scopeVersion:1,relations:[{relation_id:randomUUID(),patient_id:patient,display_id:'SYNTHETIC ONLY',status:'active',actions:['read_raw'],valid_from:'2020-01-01T00:00:00Z',valid_until:null}],loading:false,error:null,selectPatient:async()=>{},hasCurrentAccess:()=>true,refresh:async()=>{}});
let root,router,calls=[],rows=[older,newer],detailOverride=null,denied=false,deferred=null;
const failure=()=>Response.json({code:'CASE_ACTION_DENIED',message:'SYNTHETIC DENIED',retryable:false,recovery:'none',field_errors:[]},{status:403});
globalThis.fetch=async(path,options={})=>{
 calls.push({path,options});
 if(path.endsWith('/records/search')){
  if(deferred)return deferred;if(denied)return failure();
  const query=JSON.parse(options.body);assert.equal(query.kind,'observation');
  const matching=rows.filter(row=>row.detail.fields.some(value=>value.field_code.includes(query.original_name)));
  const offset=query.cursor?Number(query.cursor):0;
  return Response.json({records:matching.slice(offset,offset+1),next_cursor:offset+1<matching.length?String(offset+1):null});
 }
 const item=rows.find(value=>path===`/api/records/${value.record_id}`||path===`/api/record-versions/${value.detail.record_version_id}`);
 if(item)return denied?failure():Response.json(detailOverride??item.detail);
 throw new Error(`Unexpected request ${path}`);
};
const flush=async()=>act(async()=>{await new Promise(done=>setTimeout(done,15));});
const page=()=>document.querySelector('[data-screen-id]')?.shadowRoot??document;
const text=()=>page().textContent;
const click=async node=>{assert.ok(node);assert.equal(node.disabled??false,false);await act(async()=>node.click());await flush();};
const button=label=>[...page().querySelectorAll('button')].find(value=>value.textContent.includes(label));
const pin=index=>page().querySelectorAll('.symptom-pin')[index];
async function mount(path='/home'){
 navigationMemory.clear();calls=[];globalThis.__homeAccess=initialAccess();root=createRoot(document.getElementById('root'));
 router=createMemoryRouter([{path:'/home',element:h(UI2HomePage)},{path:'/records/:recordId',element:h(UI2RecordDetailPage)},{path:'/records/versions/:versionId',element:h(UI2RecordVersionPage)},{path:'*',element:h('p',null,'Synthetic destination')}],{initialEntries:[path]});
 await act(async()=>root.render(h(RouterProvider,{router})));await flush();await flush();
}
async function unmount(){await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();}
async function navigate(path){await act(async()=>router.navigate(path));await flush();await flush();}
function pinned(item,index=0){return recordSnapshotPath({recordId:item.record_id,recordVersionId:item.detail.record_version_id,fieldId:item.detail.fields[index].field_id});}
try{
 validateRecordSearchPage({records:rows,next_cursor:null});
 await mount();assert.ok(text().includes('≥37.123456789 SYNTHETIC-UNIT'));assert.ok(text().includes('原文：十月五日'));
 assert.equal(calls.filter(call=>call.path.endsWith('/records/search')).length,4,'all pages must be read');
 await click(page().querySelectorAll('.symptom-columns button')[0]);assert.equal(router.state.location.pathname+router.state.location.search,pinned(newer));
 assert.ok(text().includes('≥37.123456789 SYNTHETIC-UNIT'));assert.equal(page().querySelectorAll('.rich-field-primary').length,1);
 assert.equal(calls.some(call=>call.path.startsWith('/api/record-versions/')),false,'current read-only target does not require history grant');
 await navigate('/home');await click(pin(1));assert.equal(router.state.location.pathname+router.state.location.search,pinned(older,1));
 assert.equal(page().querySelector('.rich-record-value').textContent,'观察到');assert.equal(page().querySelector('.rich-field-primary>span').textContent,'皮疹观察');
 await navigate('/home');await click(pin(2));assert.equal(page().querySelector('[data-archive-category]').getAttribute('data-archive-category'),'symptoms');assert.equal(router.state.location.pathname,'/home');
 await click(page().querySelector('[aria-label="关闭孩子的记录"]'));await click(button('记录症状'));assert.equal(router.state.location.pathname,'/register/observations');await unmount();
 rows=[record([unknown])];await mount();assert.ok(text().includes('无法确定'));assert.ok(text().includes('时间待补充'));await click(pin(0));assert.equal(router.state.location.pathname+router.state.location.search,pinned(rows[0]));await unmount();
 rows=[newer,record([field('temp',time({}),{value_type:'decimal',raw_numeric:'39',raw_unit:'C',categorical_value:null})])];await mount();assert.ok(text().includes('同时间有多条记录'));await click(pin(0));assert.equal(page().querySelector('[data-archive-category]').getAttribute('data-archive-category'),'temperature');await unmount();
 rows=[newer,record([unknown])];await mount();assert.ok(text().includes('最近有明确时间的记录'));assert.ok(text().includes('≥37.123456789 SYNTHETIC-UNIT'));assert.ok(text().includes('另有 1 条记录时间待确认'));await click(pin(0));assert.equal(router.state.location.pathname+router.state.location.search,pinned(newer));await unmount();
 rows=[unqualifiedDate,dated,record([unknown])];await mount();
 assert.ok(text().includes('最近有明确时间的记录'));assert.ok(text().includes('38.456789 C'));assert.ok(text().includes('2026-10-09T08:30:00'));assert.ok(text().includes('另有 2 条记录时间待确认'));
 assert.ok(!page().querySelector('.symptom-columns').textContent.includes('36.9'));await click(pin(0));assert.equal(router.state.location.pathname+router.state.location.search,pinned(dated));await unmount();
 rows=[record([unknown]),dated];await mount();
 const pagedTemperatureReads=calls.filter(call=>call.path.endsWith('/records/search')&&JSON.parse(call.options.body).original_name==='temp');
 assert.deepEqual(pagedTemperatureReads.map(call=>JSON.parse(call.options.body).cursor),[null,'1'],'unknown on page one must not stop the fetch before the exact-time record on page two');
 assert.ok(page().querySelector('.symptom-columns').textContent.includes('38.456789 C'));assert.ok(page().querySelector('.symptom-columns').textContent.includes('2026-10-09T08:30:00'));
 assert.ok(text().includes('最近有明确时间的记录'));assert.ok(text().includes('另有 1 条记录时间待确认'));
 await click(page().querySelectorAll('.symptom-columns button')[0]);assert.equal(router.state.location.pathname+router.state.location.search,pinned(dated));
 assert.equal(new URLSearchParams(router.state.location.search).get('version'),dated.detail.record_version_id);await unmount();
 rows=[unqualifiedDate,record([unknown])];await mount();assert.ok(text().includes('记录时间无法确定先后'));assert.ok(!text().includes('最近有明确时间的记录'));await click(pin(0));assert.equal(page().querySelector('[data-archive-category]').getAttribute('data-archive-category'),'temperature');await unmount();
 rows=[unqualifiedDate,dated,datedTie];await mount();assert.ok(text().includes('最近有明确时间的记录'));assert.ok(text().includes('同时间有多条记录'));assert.ok(text().includes('另有 1 条记录时间待确认'));assert.ok(!page().querySelector('.symptom-columns').textContent.includes('38.456789'));await click(pin(0));assert.equal(page().querySelector('[data-archive-category]').getAttribute('data-archive-category'),'temperature');await unmount();
 const utcDate=record([field('temp',time({timezone_name:'UTC'}),{value_type:'decimal',raw_numeric:'36.3',raw_unit:'C',categorical_value:null})]);
 rows=[newer,utcDate];await mount();
 assert.equal(project(observationEntries(rows,'temp')).display.state,'indeterminate','same calendar date in Asia/Shanghai and UTC has overlapping day windows, not a definite latest record');
 assert.ok(text().includes('记录时间无法确定先后'));assert.ok(!text().includes('最近有明确时间的记录'));assert.ok(!text().includes('37.123456789'));assert.ok(!text().includes('36.3 C'));
 await click(pin(0));assert.equal(page().querySelector('[data-archive-category]').getAttribute('data-archive-category'),'temperature');await unmount();
 rows=[newer];detailOverride={...newer.detail,record_version_id:randomUUID(),version:2};await mount(pinned(newer));assert.ok(text().includes('这条记录已有新版本'));assert.equal(page().querySelectorAll('.rich-field-primary').length,0);assert.ok(!text().includes('37.123456789'));assert.ok(!text().includes('查看所选历史版本'));await unmount();
 detailOverride={...detailOverride,allowed_actions:['read_history']};await mount(pinned(newer));const historical=[...page().querySelectorAll('a')].find(a=>a.textContent==='查看所选历史版本');assert.equal(historical.getAttribute('href'),`/records/versions/${newer.detail.record_version_id}?field=${newer.detail.fields[0].field_id}`);detailOverride=null;await click(historical);assert.ok(text().includes('37.123456789'));assert.equal(router.state.location.pathname,`/records/versions/${newer.detail.record_version_id}`);await unmount();
 await mount(recordSnapshotPath({recordId:newer.record_id,recordVersionId:newer.detail.record_version_id,fieldId:randomUUID()}));assert.ok(text().includes('所选字段当前不可读取'));assert.equal(page().querySelectorAll('.rich-field-primary').length,0);await unmount();
 denied=true;await mount();assert.ok(text().includes('读取失败'));assert.equal(pin(0).disabled,true);assert.ok(!text().includes('37.123456789'));await unmount();
 await mount(pinned(newer));assert.ok(text().includes('当前访问关系需要核验'));assert.equal(page().querySelectorAll('.rich-field-primary').length,0);await unmount();denied=false;
 await mount();assert.ok(text().includes('37.123456789'));let resolve;deferred=new Promise(done=>{resolve=done;});
 await act(async()=>{globalThis.__homeAccess={...globalThis.__homeAccess,scopeVersion:2};listeners.forEach(fn=>fn());});assert.ok(!text().includes('37.123456789'));assert.equal(pin(0).disabled,true);
 await act(async()=>{globalThis.__homeAccess={...globalThis.__homeAccess,patientId:null,scopeVersion:3,hasCurrentAccess:()=>false};listeners.forEach(fn=>fn());});
 await act(async()=>resolve(Response.json({records:rows,next_cursor:null})));await flush();assert.ok(!text().includes('37.123456789'));await click(pin(0));assert.equal(router.state.location.pathname,'/associations');await unmount();deferred=null;
 rows=[unqualifiedDate,dated];await mount();assert.ok(text().includes('38.456789'));let resolvePrevious;deferred=new Promise(done=>{resolvePrevious=done;});const previousRows=rows;
 await act(async()=>{globalThis.__homeAccess={...globalThis.__homeAccess,scopeVersion:2};listeners.forEach(fn=>fn());});assert.ok(!text().includes('38.456789'));assert.equal(pin(0).disabled,true);
 const otherPatient=randomUUID(),otherDated=record([field('temp',exactTime,{value_type:'decimal',raw_numeric:'36.222',raw_unit:'C',categorical_value:null})]);otherDated.detail.patient_id=otherPatient;rows=[otherDated];deferred=null;
 await act(async()=>{globalThis.__homeAccess={...globalThis.__homeAccess,patientId:otherPatient,scopeVersion:3,relations:[{...globalThis.__homeAccess.relations[0],patient_id:otherPatient,display_id:'SYNTHETIC OTHER'}]};listeners.forEach(fn=>fn());});await flush();await flush();
 assert.ok(text().includes('36.222 C'));assert.ok(!text().includes('38.456789'));assert.ok(!text().includes('记录时间待确认'));
 await act(async()=>resolvePrevious(Response.json({records:previousRows,next_cursor:null})));await flush();assert.ok(text().includes('36.222 C'));assert.ok(!text().includes('38.456789'));await click(pin(0));assert.equal(router.state.location.pathname+router.state.location.search,pinned(otherDated));await unmount();
 assert.equal(JSON.stringify([older,newer]),fixturesBefore,'raw values, units and times unchanged');
 console.log('PASS synthetic home/detail DOM: full chronology unchanged; separate qualified-time projection with exact raw value/date and unknown-time notice; first-page unknown plus second-page exact-time record with pinned version navigation; all-unknown, exact ties and overlapping precision; exact record/version/field navigation; read-only/empty states; separate registration; supersession and permission failures; cross-patient scope and late-response fencing. No browser or backend.');
}finally{if(root?._internalRoot)await unmount();dom.window.close();}
