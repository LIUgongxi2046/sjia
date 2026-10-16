import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { JSDOM } from 'jsdom';
const dom=new JSDOM('<!doctype html><div id="root"></div>');
for(const name of ['window','document','navigator','HTMLElement'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {TemperatureUnit}=await import('../src/components/TemperatureUnit.tsx');
const root=createRoot(document.getElementById('root'));
for(const [props,expected] of [[{rawUnit:'C'},'C'],[{rawUnit:'F'},'F'],[{rawUnit:'°C'},'°C'],[{rawUnit:'source-unit'},'source-unit'],[{rawUnit:null},'未知'],[{},'未知'],[{selection:'尚未选择'},'待选单位'],[{selection:'摄氏度（°C）'},'°C'],[{selection:'华氏度（°F）'},'°F'],[{selection:'无法确定'},'未知']]){
 const before=JSON.stringify(props);
 await act(async()=>root.render(h(TemperatureUnit,props)));
 assert.equal(document.querySelector('#root span').textContent,expected);
 assert.equal(JSON.stringify(props),before);
}
const correction=await readFile(new URL('../src/features/records/CorrectionPage.tsx',import.meta.url),'utf8');
const creation=await readFile(new URL('../src/features/registration/ObservationsPage.tsx',import.meta.url),'utf8');
assert.ok(correction.includes('<TemperatureUnit rawUnit={temperature?.raw_unit} />'));
assert.ok(creation.includes('<TemperatureUnit selection={tempUnit} />'));
assert.ok(creation.includes("raw_unit: tempUnit === '摄氏度（°C）' ? 'C' : tempUnit === '华氏度（°F）' ? 'F' : null"));
await act(async()=>root.unmount());dom.window.close();
console.log('PASS shared actual temperature-unit DOM: exact C/F/source units, unknown missing source, unselected/create C/F/unknown; payload mapping unchanged');
