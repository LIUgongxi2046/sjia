// Actual record detail presentation with isolated synthetic reads. No browser or backend.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { JSDOM } from 'jsdom';
registerHooks({load(url,context,nextLoad){
 if(url.endsWith('/state/access.tsx'))return {format:'module',source:'export function useAccess(){return globalThis.__detailAccess}',shortCircuit:true};
 if(url.endsWith('/api/useRecordResource.ts'))return {format:'module',source:'export function useRecordResource(){return globalThis.__detailResource}',shortCircuit:true};
 if(/\.(png|jpg|webp|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};
 return nextLoad(url,context);
}});
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/records/synthetic-record',pretendToBeVisual:true});
for(const name of ['window','document','navigator','HTMLElement','HTMLButtonElement','Element','Node','ShadowRoot'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
window.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){}});
HTMLElement.prototype.getClientRects=function(){return [{x:0,y:0,width:100,height:30}]};
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Record layout fixture may not fetch');};
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {RecordFieldValue}=await import('../src/components/RecordFieldPrimary.tsx');
const {fieldValue}=await import('../src/api/records.ts');
const {UI2RecordDetailPage,UI2RecordVersionPage}=await import('../src/features/records/UI2RecordDetailPage.tsx');
const patient='synthetic-patient';
const time={precision:'minute',raw_text:'原文时间不缩写',local_date:null,local_datetime:'2026-10-07T18:00:00',instant:'2026-10-07T10:00:00Z',timezone_name:'Asia/Shanghai',resolution_status:'resolved',local_year:null,local_month:null,end_year:null,end_month:null,relative_text:null,utc_offset_minutes:480};
const field=(id,code,overrides={})=>({field_id:id,field_code:code,concept_code:null,original_name:null,value_type:'decimal',value_state:'known',raw_numeric:'38.2000',raw_unit:'°C',raw_text:null,categorical_value:null,qualifier:'eq',medical_time_id:null,medical_time:time,sample_time_origin:'observation',method:'保留原方法',reference_interval:null,report_flag:null,verification_states:[],confirmation_id:null,source_anchor:null,...overrides});
const base={record_id:'synthetic-record',record_version_id:'synthetic-version',patient_id:patient,kind:'observation',version:1,origin_type:'self_report',capture_method:'manual',quality_status:'valid',source_organization:null,corrected_at:null,correction_reason:null,recorded_at:'2026-10-07T10:01:00Z',fields:[field('temp-id','temp'),field('rash-id','rash',{value_type:'categorical',raw_numeric:null,raw_unit:null,categorical_value:'unknown',qualifier:null}),field('joint-id','joint_discomfort',{value_type:'categorical',raw_numeric:null,raw_unit:null,categorical_value:'no',qualifier:null})],attachments:[],report:null,fact_refs:[],allowed_actions:['correct_record','read_history']};
globalThis.__detailAccess={principalId:'synthetic-principal',patientId:patient,loading:false,error:null,relations:[{patient_id:patient,display_id:'合成患儿（仅排版测试）'}]};
globalThis.__detailResource={data:base,error:null,loading:false,reload(){}};
let root,router;
const page=()=>document.querySelector('[data-screen-id]')?.shadowRoot??document;
const flush=async()=>act(async()=>{await Promise.resolve()});
async function mount(path='/records/synthetic-record?version=synthetic-version&field=temp-id'){
 root=createRoot(document.getElementById('root'));router=createMemoryRouter([{path:'/records/:recordId',element:h(UI2RecordDetailPage)},{path:'/records/versions/:versionId',element:h(UI2RecordVersionPage)},{path:'*',element:h('p',null,'合成目标入口')}],{initialEntries:[path]});await act(async()=>root.render(h(RouterProvider,{router})));await flush();
}
async function unmount(){await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();}
try{
 // The unit slot must change presentation, never the exact value/qualifier/unit text.
 root=createRoot(document.getElementById('root'));
 for(const f of [base.fields[0],...['eq','lt','le','gt','ge',null].map(qualifier=>field('x','temp',{qualifier})),field('x','temp',{raw_numeric:'9007199254740993.0000',raw_unit:'原单位 / 很长单位'}),field('x','temp',{raw_unit:null}),field('x','temp',{raw_unit:''}),field('x','temp',{value_state:'unknown'}),field('x','temp',{value_state:'invalid'}),field('x','temp',{raw_numeric:null,raw_unit:null,raw_text:'完整原始文字保留',value_type:'text'})]){
  const before=JSON.stringify(f);await act(async()=>root.render(h('div',{className:'value'},h(RecordFieldValue,{field:f}))));assert.equal(document.querySelector('.value').textContent,fieldValue(f));assert.equal(JSON.stringify(f),before);
  if(f.value_state==='unknown'||f.raw_numeric===null)assert.equal(document.querySelector('.rich-record-unit'),null);
 }
 await act(async()=>root.unmount());document.getElementById('root').replaceChildren();
 const before=JSON.stringify(base);await mount();
 assert.equal(page().querySelectorAll('.rich-record-fields').length,1);assert.equal(page().querySelectorAll('.rich-record-fields>.rich-record-field').length,3);
 assert.equal(page().querySelector('.rich-record-value').textContent,'=38.2000 °C');assert.equal(page().querySelector('.rich-record-unit').textContent,' °C');
 for(const f of base.fields){const node=page().querySelector(`#record-field-${f.field_id}`);assert.ok(node);assert.match(node.textContent,/完整字段/);assert.ok(node.textContent.includes(fieldValue(f)));assert.ok(node.textContent.includes('原文时间不缩写'));}
 const correction=[...page().querySelectorAll('a')].find(a=>a.textContent==='更正记录');assert.equal(correction.getAttribute('href'),'/records/synthetic-record/correct');assert.ok(correction.classList.contains('button'));assert.ok(!correction.classList.contains('secondary'));
 await act(async()=>correction.click());assert.equal(router.state.location.pathname,'/records/synthetic-record/correct');await unmount();
 globalThis.__detailResource={...globalThis.__detailResource,data:{...base,allowed_actions:[]}};await mount();assert.equal([...page().querySelectorAll('a')].some(a=>a.textContent==='更正记录'),false);assert.equal([...page().querySelectorAll('button')].find(a=>a.textContent==='更正记录').disabled,true);await unmount();
 globalThis.__detailResource.data={...base,fields:Array.from({length:8},(_,i)=>field(`lab-${i}`,`lab_${i}`,{original_name:`合成检验 ${i+1}`,raw_numeric:`${i}.000`,raw_unit:'原单位'})),kind:'report'};await mount('/records/synthetic-record');assert.equal(page().querySelectorAll('.rich-record-fields').length,1);assert.equal(page().querySelectorAll('.rich-record-field').length,8);assert.equal(page().querySelector('.rich-record-value').textContent,'8 项原始内容');await unmount();
 globalThis.__detailResource.data=base;await mount('/records/synthetic-record?version=synthetic-version&field=rash-id');assert.equal(page().querySelector('.rich-record-value').textContent,'无法确定');assert.equal(page().querySelector('.rich-record-unit'),null);assert.equal(page().querySelector('.rich-record-fields>.rich-record-field').id,'record-field-rash-id');await unmount();
 await mount('/records/versions/synthetic-version');assert.equal([...page().querySelectorAll('a')].some(a=>a.textContent==='更正记录'),false);assert.ok(page().textContent.includes('历史只读内容'));await unmount();
 globalThis.__detailResource.data={...base,record_version_id:'superseded-version',version:2};await mount();assert.equal(page().querySelector('.rich-record-fields'),null);assert.ok(page().textContent.includes('这条记录已有新版本'));assert.ok(!page().textContent.includes('38.2000'));await unmount();
 globalThis.__detailResource.data=base;globalThis.__detailAccess={...globalThis.__detailAccess,patientId:null};await mount();assert.equal(page().querySelector('.rich-record-fields'),null);assert.ok(!page().textContent.includes('38.2000'));await unmount();
 assert.equal(JSON.stringify(base),before);assert.equal(requests,0);
 console.log('PASS record detail layout DOM: exact raw/qualifier/unit text, one field group, selected-field order and IDs, 8-item report, unknown/text values, correction permission/target, historical read-only and changed-version/permission hiding; no browser/backend/fetch');
}finally{if(root?._internalRoot)await act(async()=>root.unmount());router?.dispose();dom.window.close();}
