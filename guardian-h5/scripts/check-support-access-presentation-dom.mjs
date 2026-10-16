// Actual support pages under explicit access states; no backend or clinical approval.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {JSDOM} from 'jsdom';
registerHooks({load(url,context,nextLoad){if(/\.(png|webp|jpg|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};return nextLoad(url,context)}});
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
for(const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLButtonElement'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Unexpected network in access-only state');};
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {AssessmentPage}=await import('../src/features/support/AssessmentPage.tsx');
const {VerificationPage}=await import('../src/features/support/VerificationPage.tsx');
let cases=0;
for(const [Page,screen,capability] of [[AssessmentPage,'UI2-35','read_assessment'],[VerificationPage,'UI2-34','verify_field']]){
 for(const state of ['permission','loading','error','default']){
  const session=state==='default'?{principal_id:'synthetic-support',capabilities:[{capability_id:capability,state:'available'}]}:null;
  const root=createRoot(document.getElementById('root'));
  const router=createMemoryRouter([{path:'/',element:h(Page,{session,accessState:state,accessError:state==='permission'?'SUPPORT_AUDIENCE_REQUIRED':state==='error'?'合成读取错误':null,onRefresh:()=>{}})}]);
  await act(async()=>{root.render(h(RouterProvider,{router}));await new Promise(resolve=>setTimeout(resolve,5));});
  const page=document.querySelector(`[data-screen-id="${screen}"]`).shadowRoot;
  const text=page.textContent;
  assert.ok(!text.includes('尚无有效评估'));
  if(state!=='default'){
   const status=page.querySelector('.rich-read-state');assert.ok(status);
   assert.equal(page.querySelector('.support-banner').nextElementSibling,status);
   if(state==='permission'){assert.ok(status.textContent.includes('当前账号没有支持端访问权限'));assert.ok(!text.includes('核对资格待确认'));}
   if(state==='loading')assert.ok(status.textContent.includes('正在读取当前资料'));
   if(state==='error')assert.ok(status.textContent.includes('合成读取错误'));
  }else {assert.equal(page.querySelector('.rich-read-state'),null);if(Page===AssessmentPage)assert.ok(text.includes('请选择获准的评估结果'));}
  await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();cases++;
 }
}
assert.equal(requests,0);dom.window.close();console.log(`PASS ${cases} actual support page access/loading/error/authorized-unselected DOM states; no invented empty result or network`);
