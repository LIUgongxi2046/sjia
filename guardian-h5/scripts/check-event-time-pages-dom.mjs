// Actual route components with synthetic read hooks; presentation-only, no backend.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { JSDOM } from 'jsdom';
registerHooks({load(url,context,nextLoad){
 if(url.endsWith('/state/access.tsx'))return {format:'module',source:'export function useAccess(){return globalThis.__eventAccess;}',shortCircuit:true};
 if(url.endsWith('/api/useFamilyResource.ts'))return {format:'module',source:'export function useFamilyResource(path){return globalThis.__eventRead(path);}',shortCircuit:true};
 if(url.endsWith('/api/useRelationResource.ts'))return {format:'module',source:'export function useRelationResource(path){return globalThis.__eventRead(path);}',shortCircuit:true};
 if(/\.(png|webp|jpg|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};
 return nextLoad(url,context);
}});
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/drafts'});
for(const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLButtonElement','Node','ShadowRoot'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Display-only test must not make network requests');};
const relation={relation_id:'synthetic-relation',patient_id:'synthetic-patient',display_id:'合成时间测试',status:'active',version:'1',auth_epoch:'1',actions:['read_raw'],valid_from:'2020-01-01T08:00:00+08:00',valid_until:null};
globalThis.__eventAccess={principalId:'synthetic-principal',patientId:relation.patient_id,relations:[relation],loading:false,error:null,refresh:()=>{}};
const fixture={
 '/api/patients/synthetic-patient/drafts':{drafts:[{draft_id:'synthetic-draft',patient_id:relation.patient_id,form_type:'observation',status:'editing',version:'1',payload:{},saved_at:'2026-10-11T11:14:14.622379+00:00'}]},
 '/api/patients/synthetic-patient/home':{assessment_state:'configuration_pending',current_alert_id:null},
 '/api/patients/synthetic-patient/alerts':{alerts:[{alert_id:'synthetic-stale-alert',validity:'stale',created_at:'2026-10-11T11:14:14Z',read_at:null}]},
 '/api/relations/synthetic-relation':relation,
};
const resources=Object.fromEntries(Object.entries(fixture).map(([path,data])=>[path,{data,loading:false,error:null,reload:()=>{}}]));
globalThis.__eventRead=path=>{assert.ok(resources[path],`Unexpected read: ${path}`);return resources[path]};
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');const {createMemoryRouter,RouterProvider}=await import('react-router');
const {UI2DraftsPage}=await import('../src/features/account/UI2DraftsPage.tsx');const {UI2AlertsPage}=await import('../src/features/alerts/UI2AlertsPage.tsx');const {UI2AssociationDetailPage}=await import('../src/features/association/UI2AssociationsPage.tsx');
const root=createRoot(document.getElementById('root'));
const router=createMemoryRouter([{path:'/drafts',element:h(UI2DraftsPage)},{path:'/alerts',element:h(UI2AlertsPage)},{path:'/associations/:relationId',element:h(UI2AssociationDetailPage)}],{initialEntries:['/drafts']});
const page=()=>document.querySelector('[data-screen-id]')?.shadowRoot;
try{
 await act(async()=>root.render(h(RouterProvider,{router})));
 assert.ok(page().textContent.includes('保存时间2026-10-11 11:14:14.622379 UTC+00:00'));
 await act(async()=>router.navigate('/alerts'));
 assert.ok(page().textContent.includes('2026-10-11 11:14:14 UTC'));
 assert.ok(page().textContent.includes('已失效提示'));
 await act(async()=>router.navigate('/associations/synthetic-relation'));
 assert.ok(page().textContent.includes('生效时间2020-01-01 08:00:00 UTC+08:00'));
 assert.ok(page().textContent.includes('未指定期限'));
 assert.equal(relation.valid_from,'2020-01-01T08:00:00+08:00');assert.equal(requests,0);
}finally{await act(async()=>root.unmount());router.dispose();dom.window.close()}
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_ROUTE_DOM_NOT_BROWSER',process_timezone:process.env.TZ,checks:['Actual draft save timestamp keeps microseconds and UTC offset','Actual stale-alert timestamp explicitly UTC','Actual association grant instant retains source +08:00; null expiry remains unspecified','No network requests or timestamp mutations']},null,2));
