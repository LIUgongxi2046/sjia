import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {JSDOM} from 'jsdom';
registerHooks({load(url,context,nextLoad){if(url.endsWith('/state/access.tsx'))return {format:'module',source:'export function useAccess(){return globalThis.__statusAccess;}',shortCircuit:true};if(/\.(png|webp|jpg|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};return nextLoad(url,context)}});
const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});for(const name of ['window','document','navigator','HTMLElement','ShadowRoot'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Direct state pages must not invent a request retry');};
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');const {createMemoryRouter,RouterProvider}=await import('react-router');const {UI2StatusPage}=await import('../src/features/status/UI2StatusPage.tsx');
for(const signedIn of [false,true])for(const kind of ['access','service','missing']){
 globalThis.__statusAccess={principalId:signedIn?'synthetic-principal':null,accountStatus:signedIn?'active':'anonymous',loading:false,error:null};
 const root=createRoot(document.getElementById('root'));const router=createMemoryRouter([{path:'/',element:h(UI2StatusPage,{kind})},...['home','records','associations','account','login'].map(path=>({path:'/'+path,element:h('p',null,path)}))]);
 await act(async()=>root.render(h(RouterProvider,{router})));
 const page=document.querySelector('[data-screen-id]').shadowRoot;
 assert.ok(page.querySelector('.rich-status-detail .button'));assert.equal(page.querySelectorAll('.rich-status-links .list-row').length,2);
 assert.ok(page.textContent.includes('还可以继续'));assert.ok(!page.textContent.includes('重新加载'));
 if(kind==='service')assert.ok(page.textContent.includes('不能将本次失败视为没有资料'));
 if(kind==='missing')assert.ok(page.textContent.includes('这个页面暂时无法找到'));
 await act(async()=>page.querySelector('.rich-status-detail .button').click());
 const expected=kind==='access'?(signedIn?'/associations':'/login'):kind==='missing'&&signedIn?'/records':'/home';assert.equal(router.state.location.pathname,expected);
 await act(async()=>root.unmount());router.dispose();document.getElementById('root').replaceChildren();
}
assert.equal(requests,0);dom.window.close();console.log('PASS six actual status-page DOM cases: original recovery hierarchy, session-aware targets, distinct access/service/missing semantics, no invented retry/network');
