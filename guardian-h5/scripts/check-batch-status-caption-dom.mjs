import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';
import {JSDOM} from 'jsdom';
registerHooks({load(url,context,nextLoad){if(/\.(png|webp|jpg|svg)$/.test(url))return {format:'module',source:`export default ${JSON.stringify(url)};`,shortCircuit:true};return nextLoad(url,context)}});
const dom=new JSDOM('<!doctype html><div id="root"></div>');for(const name of ['window','document','navigator','HTMLElement'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createElement:h,act}=await import('react');const {createRoot}=await import('react-dom/client');const {BatchPageCounts,batchStatusLabels}=await import('../src/features/archives/UI2BatchPage.tsx');
const root=createRoot(document.getElementById('root'));
const empty={originals:0,pages:0,declared:0,receiving:0,uploaded:0,queued:0,processing:0,awaiting_confirmation:0,confirmed:0,archived:0,failed:0,uncertain:0,cancel_requested:0,cancelled:0,superseded:0,facts:0};
assert.equal(batchStatusLabels.partial,'各页状态不同');assert.ok(!batchStatusLabels.partial.includes('完成'));
for(const [counts,expected] of [[{...empty,pages:3,uploaded:2,cancelled:1},['原件已接收 2 页','已取消 1 页','本人确认完成 0 页','资料已保存 0 页']], [{...empty,pages:4,confirmed:1,archived:2,failed:1},['本人确认完成 1 页','资料已保存 2 页','处理失败 1 页']]]){
 const before=JSON.stringify(counts);await act(async()=>root.render(h(BatchPageCounts,{counts})));
 for(const text of expected)assert.ok(document.querySelector('[role=status]').textContent.includes(text));
 assert.equal(JSON.stringify(counts),before);
}
await act(async()=>root.unmount());dom.window.close();console.log('PASS actual batch-count DOM: uploaded+cancelled does not imply completion; confirmed/archived/failed counts display separately and source counts stay unchanged');
