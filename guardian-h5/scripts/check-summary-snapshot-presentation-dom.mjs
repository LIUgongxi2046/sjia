// Synthetic immutable snapshots through production display components; no backend/clinical actions.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
for(const name of ['window','document','navigator','HTMLElement'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Snapshot display must not fetch live data');};
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');const {MemoryRouter}=await import('react-router');
const {SummarySnapshotPresentation,summaryRecordCount}=await import('../src/features/summaries/SummarySnapshotPresentation.tsx');
const {SummaryTrend,summaryTrendCoordinates}=await import('../src/features/summaries/SummaryTrend.tsx');
const field={field_code:'temp',original_name:null,value_type:'decimal',value_state:'known',raw_numeric:'36.7',raw_unit:'C',raw_text:null,categorical_value:null,method:null,reference_interval:null,report_flag:null,confirmation_id:null,source_anchor:null,medical_time:{precision:'minute',raw_text:'原文时间',timezone_name:null,utc_offset_minutes:null}};
function item(id,record,kind='observation') {return {field_id:id,record_version_id:`version-${record}`,snapshot:{record_id:record,record_version_id:`version-${record}`,kind,origin_type:'guardian_observation',source_organization:null,confirmation_snapshot_state:'not_recorded',source_anchor_snapshot_state:'not_recorded',field:{...field,field_id:id}}};}
const items=[item('f1','r1'),item('f2','r1'),item('f3','r2','report'),item('f4','r3','therapy')];
const summary={summary_id:'synthetic',display_id:'合成患儿',from_date:'2026-10-01',to_date:'2026-10-11',scope_timezone:'UTC',generated_at:'2026-10-11T12:00:00Z',validity:'valid',needs_regeneration:false,include_unknown_time:true,include_source_status:true,record_kinds:['observation','report','therapy'],items};
const root=createRoot(document.getElementById('root'));
async function render(value){await act(async()=>root.render(h(MemoryRouter,null,value)));}
assert.equal(summaryRecordCount(summary,'observation'),1);
const before=JSON.stringify(summary);
await render(h(SummarySnapshotPresentation,{summary}));
assert.equal(document.querySelectorAll('.rich-summary-counts strong').length,3);
assert.deepEqual([...document.querySelectorAll('.rich-summary-counts strong')].map(node=>node.textContent.trim()),['1 条','1 条','1 条']);
assert.ok(document.body.textContent.includes('时区未明确'));
assert.ok(document.body.textContent.includes('36.7 C'));
assert.ok(document.body.textContent.includes('UTC'));
assert.ok([...document.querySelectorAll('a')].every(link=>link.getAttribute('href').startsWith('/records/versions/')));
assert.equal(JSON.stringify(summary),before);
for(const value of [null,{...summary,validity:'invalid'},{...summary,needs_regeneration:true}]){
 await render(h(SummarySnapshotPresentation,{summary:value}));assert.ok(!document.body.textContent.includes('36.7'));assert.equal(document.querySelectorAll('a').length,0);
 assert.deepEqual([...document.querySelectorAll('.rich-summary-counts strong')].map(node=>node.textContent.trim()),['—','—','—']);
}
const point=(field_id,raw_value,plot_at)=>({field_id,record_version_id:'version-r1',source_definition_revision_id:null,raw_value,raw_unit:'C',plot_at,plottable:true,reason_codes:[]});
const group={field_code:'temp',concept_code:'temp',points:[point('f1','36.7','2026-10-01T00:00:00Z'),point('f2','36.8','2026-10-02T00:00:00Z')],segments:[{from_field_id:'f1',to_field_id:'f2',connect:false,reason_codes:['UNPOSITIONED_TIME_GAP']}]};
const withTrend={...summary,trend_projection:{schema_version:'summary-raw-trend-1',snapshot_sha256:'synthetic-hash',clinical_interpretation:false,series:[group]}};
await render(h(SummaryTrend,{summary:withTrend}));
assert.equal(document.querySelectorAll('[data-summary-point]').length,2);assert.equal(document.querySelectorAll('[data-summary-segment]').length,0);
assert.ok(document.body.textContent.includes('不连接为趋势'));
await render(h(SummaryTrend,{summary:{...withTrend,trend_projection:{...withTrend.trend_projection,series:[{...group,segments:[{...group.segments[0],connect:true,reason_codes:[]}]}]}}}));
assert.equal(document.querySelectorAll('[data-summary-segment]').length,1);
const mixed={...group,points:[group.points[0],{...group.points[1],raw_unit:'F'}]};
assert.equal(summaryTrendCoordinates(mixed),null);
assert.equal(summaryTrendCoordinates({...group,points:[{...group.points[0],raw_unit:null}]}),null);
await render(h(SummaryTrend,{summary:{...withTrend,trend_projection:{...withTrend.trend_projection,series:[mixed]}}}));
assert.equal(document.querySelector('svg'),null);assert.ok(document.body.textContent.includes('原始单位不同'));assert.equal(document.querySelector('.rich-summary-trend>details').open,true);
await render(h(SummaryTrend,{summary:withTrend}));
assert.ok(document.querySelector('.rich-summary-trend-dates').textContent.includes('原文时间'));
assert.ok(document.querySelector('.rich-summary-trend-dates').textContent.includes('时区未明确'));
for(const raw of ['1e999999','NaN','Infinity','9007199254740992','1.234567890123456789'])assert.equal(summaryTrendCoordinates({...group,points:[{...group.points[0],raw_value:raw}]}),null);
assert.equal(summaryTrendCoordinates({...group,points:[{...group.points[0],plot_at:'unknown'}]}),null);
await render(h(SummaryTrend,{summary}));assert.ok(document.body.textContent.includes('未保存可用的趋势依据'));assert.equal(document.querySelector('svg'),null);
await render(h(SummaryTrend,{summary:{...withTrend,trend_projection:{...withTrend.trend_projection,series:[{...group,points:[{...group.points[0],raw_value:'1e999999'}]}]}}}));
assert.equal(document.querySelector('svg'),null);assert.ok(document.body.textContent.includes('1e999999'));
assert.equal(requests,0);await act(async()=>root.unmount());dom.window.close();
console.log('PASS fixed-snapshot summary DOM: distinct-record counts, raw units/source time, fixed-version links, invalid/structure hiding, explicit segment gates, unknown/missing projection and huge-value table fallback; no live fetch');
