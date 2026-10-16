// Presentation contract regression only. Uses actual components, synthetic labels,
// default anonymous access, and no browser/backend/clinical services.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format:'module', source:`export default ${JSON.stringify(url)};`, shortCircuit:true };
  return nextLoad(url,context);
} });
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/account'});
for(const name of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLButtonElement','Event','KeyboardEvent','Node','ShadowRoot']) Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Visual-only anonymous checks must not send requests');};
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {createMemoryRouter,RouterProvider}=await import('react-router');
const {UI2AccountPage}=await import('../src/features/account/UI2AccountPage.tsx');
const {DataUsePage}=await import('../src/features/account/AccountPage.tsx');
const {LoginPage}=await import('../src/features/account/LoginPage.tsx');
const {UI2StatusPage}=await import('../src/features/status/UI2StatusPage.tsx');
const {UI2Frame}=await import('../src/components/UI2Frame.tsx');
const {HomeCompanion,HomeQuickActions}=await import('../src/components/HomeVisual.tsx');
const root=createRoot(document.getElementById('root'));
const checks=[];
let observed=0,reports=0,therapies=0,archive=0;
const router=createMemoryRouter([
 {path:'/account',element:h(UI2AccountPage)},
 {path:'/account/data-use',element:h(DataUsePage)},
 {path:'/login',element:h(LoginPage)},
 {path:'/access-denied',element:h(UI2StatusPage,{kind:'access'})},
 {path:'/visual-component',element:h(UI2Frame,{title:'首页',screenId:'TEST-HOME',audience:'family',primary:'home',note:'测试',onBack:()=>{},onNavigate:()=>{}},
   h(HomeCompanion,{name:'合成视觉测试',observations:['体温','皮疹','关节表现'].map(label=>({label,value:'尚无记录'})),patientPicker:h('p',null,'仅为测试资料'),onArchive:()=>archive++,onObserve:()=>observed++}),
   h(HomeQuickActions,{onObserve:()=>observed++,onReports:()=>reports++,onTherapies:()=>therapies++}))},
],{initialEntries:['/account']});
const page=()=>document.querySelector('[data-screen-id]')?.shadowRoot;
try{
 await act(async()=>root.render(h(RouterProvider,{router})));
 assert.ok(page().querySelector('.rich-profile'));assert.ok(page().textContent.includes('尚未登录'));
 assert.ok(!page().textContent.includes('提醒方案'));assert.ok(!page().textContent.includes('退出登录'));
 assert.equal(page().querySelectorAll('.bottom-nav button').length,3);
 checks.push('Anonymous account has original profile/navigation structure, no privileged preset entry or logout claim');
 await act(async()=>page().querySelector('.rich-profile').click());
 assert.equal(router.state.location.pathname,'/login');assert.ok(page().querySelector('form[aria-label="登录"]'));
 checks.push('Profile opens actual anonymous login form');
 await act(async()=>router.navigate('/account/data-use'));
 const disclosures=page().querySelectorAll('.rich-disclosures details');assert.equal(disclosures.length,8);
 assert.ok(page().textContent.includes('申请处理完成前，不显示资料已删除'));
 assert.equal(page().querySelector('.bottom-nav'),null);
 checks.push('Data-use keeps all eight disclosures and deletion caveat; no secondary-page bottom navigation');
 await act(async()=>router.navigate('/access-denied'));
 assert.ok(page().textContent.includes('当前关联或操作权限需要重新核验'));assert.equal(page().querySelector('.bottom-nav'),null);
 checks.push('Access-denied meaning and recovery remain intact');
 await act(async()=>router.navigate('/visual-component'));
 assert.equal(page().querySelectorAll('.symptom-columns button').length,3);assert.equal(page().querySelectorAll('.symptom-pin').length,3);
 assert.equal(page().querySelectorAll('.home-entry-grid button').length,3);
 await act(async()=>{const buttons=page().querySelectorAll('.home-entry-grid button');buttons[0].click();buttons[1].click();buttons[2].click();page().querySelector('.home-twin-heading>button').click();});
 assert.deepEqual([observed,reports,therapies,archive],[1,1,1,1]);
 const img=page().querySelector('.home-child-view>img');await act(async()=>img.dispatchEvent(new Event('error')));
 assert.ok(page().textContent.includes('重新加载形象'));assert.equal(page().querySelectorAll('.symptom-columns button').length,3);
 checks.push('Three recording actions/archive stay operable, image failure retains all symptom text entries');
 assert.equal(requests,0);
 checks.push('No network requests or clinical actions in this presentation regression');
}finally{await act(async()=>root.unmount());dom.window.close();}
console.log(JSON.stringify({status:'PASS',mode:'SYNTHETIC_NONVISUAL_DOM_NOT_PIXEL_EVIDENCE',checks},null,2));
