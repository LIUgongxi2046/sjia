// Synthetic rich snapshots through actual summary components; no browser/backend.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
for(const name of ['window','document','navigator','HTMLElement'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Summary grouping fixture must not fetch');};
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');const {MemoryRouter}=await import('react-router');
const {SummarySnapshotPresentation,summaryRecordCount}=await import('../src/features/summaries/SummarySnapshotPresentation.tsx');
const {SummaryTrend}=await import('../src/features/summaries/SummaryTrend.tsx');const {fieldValue}=await import('../src/api/records.ts');
const time=index=>({precision:'minute',raw_text:`合成原文时间${index}`,local_date:null,local_datetime:`2026-10-${String(1+Math.floor(index/2)).padStart(2,'0')}T${index%2?'18':'08'}:00:00`,instant:`2026-10-${String(1+Math.floor(index/2)).padStart(2,'0')}T${index%2?'10':'00'}:00:00Z`,timezone_name:'Asia/Shanghai',resolution_status:'resolved',local_year:null,local_month:null,end_year:null,end_month:null,relative_text:null,utc_offset_minutes:480});
function item(record,fieldIndex,kind='observation',version=`version-${record}`){
 const code=kind==='observation'?['temp','rash','joint_discomfort'][fieldIndex%3]:`lab_${fieldIndex}`;
 return {field_id:`${record}-${version}-${fieldIndex}`,record_version_id:version,snapshot:{record_id:record,record_version_id:version,kind,origin_type:'self_report',source_organization:null,confirmation_snapshot_state:'not_recorded',source_anchor_snapshot_state:'not_recorded',field:{field_id:`${record}-${version}-${fieldIndex}`,field_code:code,concept_code:null,original_name:null,value_type:fieldIndex%3===0?'decimal':'categorical',value_state:'known',raw_numeric:fieldIndex%3===0?`${36+fieldIndex}.7000`:null,raw_unit:fieldIndex%3===0?'原单位':null,raw_text:null,categorical_value:fieldIndex%3===0?null:'unknown',qualifier:'le',method:null,reference_interval:null,report_flag:null,confirmation_id:null,source_anchor:null,verification_states:[],medical_time:time(Number(record.replace(/\D/g,''))||0)}}};
}
const observations=Array.from({length:28},(_,i)=>Array.from({length:3},(_,f)=>item(`record-${i}`,f))).flat();
const labs=Array.from({length:8},(_,f)=>item('lab-record-1',f,'report'));
const therapy=[item('therapy-1',0,'therapy')];
const summary={summary_id:'synthetic-only',display_id:'合成患儿',from_date:'2026-10-01',to_date:'2026-10-14',scope_timezone:'Asia/Shanghai',generated_at:'2026-10-14T12:00:00Z',validity:'valid',needs_regeneration:false,include_unknown_time:true,include_source_status:true,record_kinds:['observation','report','therapy'],items:[...observations,...labs,...therapy]};
const points=observations.filter(i=>i.snapshot.field.field_code==='temp').map(i=>({field_id:i.field_id,record_version_id:i.record_version_id,source_definition_revision_id:null,raw_value:i.snapshot.field.raw_numeric,raw_unit:i.snapshot.field.raw_unit,plot_at:i.snapshot.field.medical_time.instant,plottable:true,reason_codes:[]}));
summary.trend_projection={schema_version:'summary-raw-trend-1',snapshot_sha256:'synthetic',clinical_interpretation:false,series:[{field_code:'temp',concept_code:'temp',points,segments:points.slice(1).map((p,i)=>({from_field_id:points[i].field_id,to_field_id:p.field_id,connect:false,reason_codes:['SOURCE_NOT_COMPARABLE']}))}]};
const root=createRoot(document.getElementById('root'));
async function render(value){await act(async()=>root.render(h(MemoryRouter,null,h(SummarySnapshotPresentation,{summary:value,trend:value?h(SummaryTrend,{summary:value}):undefined}))));}
const click=async node=>{assert.ok(node);await act(async()=>node.click());};
try{
 const before=JSON.stringify(summary);await render(summary);
 assert.deepEqual([...document.querySelectorAll('.rich-summary-counts strong')].map(n=>n.textContent),['28 条','1 条','1 条']);
 const collection=document.querySelector('.rich-summary-observations');assert.ok(collection);assert.equal(collection.open,false);assert.match(collection.querySelector(':scope>summary').textContent,/28 条/);
 const groups=[...collection.querySelectorAll('.rich-summary-record')];assert.equal(groups.length,28);assert.ok(groups.every(n=>!n.open));assert.equal(collection.querySelectorAll('.rich-summary-field').length,84);
 await click(collection.querySelector(':scope>summary'));assert.equal(collection.open,true);
 for(let index=0;index<groups.length;index++){
  const group=groups[index];assert.equal(group.dataset.summaryRecordVersion,`version-record-${index}`);await click(group.querySelector(':scope>summary'));assert.equal(group.open,true);
  const articles=[...group.querySelectorAll(':scope>.rich-summary-field')];assert.equal(articles.length,3);
  articles.forEach((node,f)=>{assert.equal(node.querySelector('.rich-summary-value strong').textContent,fieldValue(observations[index*3+f].snapshot.field));assert.equal(node.querySelector('a').getAttribute('href'),`/records/versions/version-record-${index}`);assert.equal(node.closest('details:not([open])'),null);});
  await click(group.querySelector(':scope>summary'));assert.equal(group.open,false);
 }
 const report=document.querySelector('.rich-summary-kind');assert.equal(report.open,false);await click(report.querySelector(':scope>summary'));assert.equal(report.open,true);const reportRecord=report.querySelector('.rich-summary-record');await click(reportRecord.querySelector(':scope>summary'));assert.equal(reportRecord.querySelectorAll('.rich-summary-field').length,8);
 assert.equal(document.querySelectorAll('[data-summary-point]').length,28);assert.equal(document.querySelectorAll('[data-summary-segment]').length,0);assert.equal(document.querySelectorAll('.rich-summary-trend svg text').length,0,'dense points remain unlabelled, not hidden or falsely connected');
 assert.equal(summaryRecordCount(summary,'observation'),28);assert.equal(JSON.stringify(summary),before);
 // Distinct immutable versions of one record must never merge into a single group.
 const versions={...summary,trend_projection:undefined,items:[item('same-record',0,'observation','v1'),item('same-record',1,'observation','v2')]};await render(versions);
 assert.equal(document.querySelector('.rich-summary-counts strong').textContent,'1 条');assert.deepEqual([...document.querySelectorAll('.rich-summary-record')].map(n=>n.dataset.summaryRecordVersion),['v1','v2']);
 assert.deepEqual([...document.querySelectorAll('.rich-summary-field a')].map(n=>n.getAttribute('href')),['/records/versions/v1','/records/versions/v2']);
 // No recency inference, sorting, value conversion or lost unknown-time fields.
 const uncertain=item('unknown-first',0);uncertain.snapshot.field.medical_time=null;uncertain.snapshot.field.raw_numeric='9007199254740993.000';
 const mixed={...summary,trend_projection:undefined,items:[uncertain,observations[3]]};await render(mixed);const headers=[...document.querySelectorAll('.rich-summary-record>summary')];assert.ok(headers[0].textContent.includes('9007199254740993.000'));assert.ok(headers[0].textContent.includes('时间待补充'));assert.ok(!document.body.textContent.includes('最近'));
 await render({...summary,items:[],record_kinds:['report'],trend_projection:undefined});assert.equal(document.querySelector('.rich-summary-observations'),null);assert.ok(document.body.textContent.includes('本次未选择此类资料'));
 for(const value of [null,{...summary,validity:'invalid'},{...summary,needs_regeneration:true}]){await render(value);assert.equal(document.querySelectorAll('.rich-summary-record,.rich-summary-field,[data-summary-point]').length,0);assert.equal(document.querySelectorAll('a').length,0);assert.ok(!document.body.textContent.includes('36.7000'));}
 assert.equal(requests,0);
 // Presentation changes may not relax or replace immutable trend eligibility rules.
 const trendSource=readFileSync(new URL('../src/features/summaries/SummaryTrend.tsx',import.meta.url),'utf8');assert.match(trendSource,/segment\.connect/);assert.match(trendSource,/coords\.length <= 6/);
 console.log('PASS rich summary grouping DOM: 28 records/84 fields plus 8-item report, complete native expand/collapse and immutable links, distinct versions, preserved order/unknown time/raw values, 28 scatter points/zero lines, excluded-kind and stale/invalid hiding; zero fetch');
}finally{await act(async()=>root.unmount());dom.window.close();}
