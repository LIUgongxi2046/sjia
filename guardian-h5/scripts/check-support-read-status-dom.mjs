// Actual shared support status component; no authorization changes/network.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {JSDOM} from 'jsdom';
registerHooks({load(url,context,nextLoad){if(/\.(png|webp|jpg|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};return nextLoad(url,context)}});
const dom=new JSDOM('<!doctype html><div id="root"></div>');for(const name of ['window','document','navigator','HTMLElement'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
let requests=0;globalThis.fetch=async()=>{requests++;throw new Error('Read-status presentation must not send requests');};
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');const {SupportReadStatus}=await import('../src/features/support/SupportApp.tsx');
const root=createRoot(document.getElementById('root'));let retries=0;
for(const [error,text] of [['SUPPORT_AUDIENCE_REQUIRED','当前账号没有支持端访问权限'],['AUTH_REQUIRED','当前尚未取得有效登录身份'],['RUNTIME_CONFIGURATION_PENDING','服务配置尚未完成']]){
 await act(async()=>root.render(h(SupportReadStatus,{state:'permission',error,onRefresh:()=>retries++})));
 assert.ok(document.querySelector('section>p').textContent.includes(text));
 assert.ok(!document.querySelector('section>p').textContent.includes(error));
 assert.ok(document.querySelector('details').textContent.includes(error));assert.equal(document.querySelector('details').open,false);
 await act(async()=>document.querySelector('button').click());
}
assert.equal(retries,3);assert.equal(requests,0);
await act(async()=>root.render(h(SupportReadStatus,{state:'default',error:null,onRefresh:()=>{}})));assert.equal(document.querySelector('section'),null);
await act(async()=>root.unmount());dom.window.close();console.log('PASS actual support status DOM: accurate user explanations, original error codes retained collapsed, manual refresh unchanged, no network/authorization changes');
