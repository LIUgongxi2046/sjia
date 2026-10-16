// Render the actual detail primary row; no backend/browser or clinical actions.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const dom=new JSDOM('<!doctype html><div id="root"></div>');
for(const name of ['window','document','navigator','HTMLElement'])Object.defineProperty(globalThis,name,{value:dom.window[name],configurable:true});
globalThis.IS_REACT_ACT_ENVIRONMENT=true;
const {createElement:h,act}=await import('react');
const {createRoot}=await import('react-dom/client');
const {RecordFieldPrimary}=await import('../src/components/RecordFieldPrimary.tsx');
const root=createRoot(document.getElementById('root'));
const source={original_name:null,value_state:'unknown',raw_numeric:null,raw_unit:null,categorical_value:null,raw_text:null};
const labels={dose:'单次剂量',dose_unit:'剂量单位',concentration_text:'制剂浓度',route_text:'给药途径',frequency_text:'原文频次',interval_value:'间隔数值',interval_unit:'间隔单位',unrecognized_test_code:'其他字段（unrecognized_test_code）'};
for(const [field_code,label] of Object.entries(labels)){
 const field={...source,field_code};const before=JSON.stringify(field);
 await act(async()=>root.render(h(RecordFieldPrimary,{field})));
 assert.equal(document.querySelector('.rich-field-primary>span').textContent,label);
 assert.equal(document.querySelector('.rich-field-primary>strong').textContent,'无法确定');
 assert.equal(JSON.stringify(field),before);
}
await act(async()=>root.render(h(RecordFieldPrimary,{field:{...source,field_code:'dose',original_name:'原文项目名',value_state:'known',raw_numeric:'1.20',raw_unit:'原单位',qualifier:'le'}})));
assert.equal(document.querySelector('.rich-field-primary>span').textContent,'原文项目名');
assert.equal(document.querySelector('.rich-field-primary>strong').textContent,'≤1.20 原单位');
await act(async()=>root.unmount());dom.window.close();
console.log('PASS actual primary-row DOM: seven configured Chinese labels, unknown-code safe fallback, original-name precedence, unchanged unknown/raw value/unit');
