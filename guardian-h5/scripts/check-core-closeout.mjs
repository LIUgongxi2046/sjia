// Nonvisual synthetic DOM tests against the production draft hook.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent, fixtureTime } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','HTMLButtonElement','HTMLAnchorElement','Event','FocusEvent']) Object.defineProperty(globalThis, name, {value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const {createElement:h,act,useState}=await import('react');
const {createRoot}=await import('react-dom/client');
const {Simulate}=await import('react-dom/test-utils');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {AccessProvider}=await import('../src/state/access.tsx');
const {useFormDraft}=await import('../src/state/useFormDraft.tsx');
let now=0,seq=0,intervals=new Map(),writes=[],reject=false;
window.setInterval=(cb,delay)=>{const id=++seq;intervals.set(id,{cb,delay,due:now+delay});return id;};
window.clearInterval=id=>intervals.delete(id);
const id='00000000-0000-4000-8000-000000000001';
globalThis.fetch=async(path,options={})=>{
 const body=options.body?JSON.parse(options.body):null;
 if(path==='/api/auth/bootstrap')return Response.json(syntheticBootstrap());
 if(path==='/api/session')return Response.json(syntheticSession(id));
 if(path==='/api/relations')return Response.json({relations:[{relation_id:id,patient_id:id,display_id:'synthetic',status:'active',version:'1',valid_from:'2020-01-01T00:00:00Z',valid_until:null,actions:['read_raw','manage_draft']}]});
 if(path.includes('/drafts')){
  if(reject)return Response.json({code:'SYNTHETIC_REJECTION',message:'synthetic save failure'},{status:422});
  writes.push(body.payload);
  return Response.json({draft_id:id,patient_id:id,form_type:'observation',version:String(writes.length),status:'editing',saved_at:'2026-10-11T00:00:00Z',payload:body.payload});
 }
 throw new Error(`Unexpected synthetic request ${path}`);
};
function Form(){const [text,setText]=useState('');const draft=useFormDraft('observation',{text},v=>setText(v.text));return h('main',null,h('input',{'aria-label':'synthetic input',value:text,onChange:e=>setText(e.target.value)}),h('p',{'aria-label':'draft error'},draft.error));}
function Frame(){return h(AccessProvider,null,h(Form));}
let root,router;
async function flush(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});}
async function mount(){writes=[];reject=false;intervals.clear();now=0;root=createRoot(document.getElementById('root'));router=createMemoryRouter([{path:'/form',element:h(Frame)},{path:'/other',element:h('p',null,'other')}],{initialEntries:['/form']});await act(async()=>root.render(h(RouterProvider,{router})));await flush();}
async function unmount(){await act(async()=>root.unmount());document.getElementById('root').replaceChildren();}
async function change(value){await act(async()=>Simulate.change(document.querySelector('input'),{target:{value}}));await flush();}
async function tick(ms){now+=ms;await act(async()=>{for(const timer of [...intervals.values()])while(timer.due<=now){timer.due+=timer.delay;timer.cb();}});await flush();}
async function navigate(){await act(async()=>router.navigate('/other'));await flush();}
async function click(text){const button=[...document.querySelectorAll('dialog button')].find(x=>x.textContent===text);assert.ok(button,text);await act(async()=>button.click());await flush();}
await mount();
for(let i=1;i<=10;i++){await change(`continuous ${i}`);await tick(1000);}
assert.equal(writes.length,1);assert.equal(writes[0].text,'continuous 10');
await change('latest before leave');await navigate();assert.equal(router.state.location.pathname,'/form');assert.equal(document.querySelectorAll('dialog button').length,3);
await click('保存草稿后离开');assert.equal(router.state.location.pathname,'/other');assert.equal(writes.at(-1).text,'latest before leave');
await unmount();
await mount();await change('must survive failure');reject=true;await navigate();await click('保存草稿后离开');assert.equal(router.state.location.pathname,'/form');assert.equal(document.querySelector('input').value,'must survive failure');assert.match(document.querySelector('dialog [role=alert]').textContent,/尚未保存成功/);
reject=false;await click('保存草稿后离开');assert.equal(router.state.location.pathname,'/other');assert.equal(writes.at(-1).text,'must survive failure');await unmount();
await mount();await change('discard only this input');await navigate();await click('继续编辑');assert.equal(router.state.location.pathname,'/form');assert.equal(document.querySelector('input').value,'discard only this input');await navigate();await click('放弃未保存修改并离开');assert.equal(router.state.location.pathname,'/other');assert.equal(writes.length,0);await unmount();
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_NONVISUAL_DOM',checks:['continuous editing saves latest input every 10 seconds','explicit save-and-leave awaits confirmed draft','failed save preserves input and pending destination','retry succeeds without silently leaving','continue/discard choices work without implicit save']},null,2));
