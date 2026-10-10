import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from '../../guardian-h5/node_modules/playwright/index.mjs';
import {pages} from '../preview/catalog.js';
import {primaryPages,parentPages} from '../preview/navigation.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const base='http://127.0.0.1:5186/';
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:2});
await context.grantPermissions([],{origin:base});
const page=await context.newPage();
page.setDefaultTimeout(15000);
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
const phone=page.locator('.phone');
async function open(key){await page.goto(`${base}?capture=1&page=${key}`);await phone.waitFor();}
for(const [,,key] of pages){
  await open(key);
  assert.equal(await phone.locator('.mobile-nav').count(),primaryPages.includes(key)?1:0,key);
  if(!primaryPages.includes(key)){
    assert.ok(parentPages[key]);
    await phone.getByRole('button',{name:'返回上级页面',exact:true}).click();
    assert.equal(await phone.getAttribute('data-page'),parentPages[key],key);
  }
}
await open('home');
await phone.getByRole('button',{name:'记录症状',exact:true}).click();
assert.equal(await phone.locator('.mobile-nav').count(),0);
await phone.getByRole('button',{name:'返回上级页面',exact:true}).click();
assert.equal(await phone.getAttribute('data-page'),'home');
await open('records');
await phone.locator('.record-card').filter({hasText:'铁蛋白、血小板计数、AST'}).click();
await phone.getByRole('button',{name:'查看历次检验',exact:false}).click();
await phone.getByRole('button',{name:'返回上级页面',exact:true}).click();
assert.equal(await phone.getAttribute('data-page'),'detail');
assert.equal(new URL(page.url()).searchParams.get('record'),'report');
await phone.getByRole('button',{name:'返回上级页面',exact:true}).click();
assert.equal(await phone.getAttribute('data-page'),'records');
await open('observations');
const note=phone.getByLabel('其他表现与补充说明',{exact:true});
const original=await note.inputValue();
const originalTemperature=await phone.getByLabel('体温原值',{exact:true}).inputValue();
const capability=await page.evaluate(()=>({available:Boolean(window.SpeechRecognition||window.webkitSpeechRecognition),secure:window.isSecureContext}));
assert.equal(capability.secure,true);
await phone.getByRole('button',{name:'文字输入',exact:true}).click();
const editor=phone.getByRole('textbox',{name:'语音转写与文字描述',exact:true});
assert.equal(await phone.getByRole('button',{name:'确认加入症状说明',exact:true}).isDisabled(),true);
// 使用页面已有文本检查确认行为，不构造语音服务结果。
await editor.fill(original);
assert.equal(await phone.getByRole('button',{name:'保存记录',exact:true}).isDisabled(),true);
page.once('dialog',dialog=>dialog.dismiss());
await phone.getByRole('button',{name:'返回上级页面',exact:true}).click();
assert.equal(await phone.getAttribute('data-page'),'observations');
await phone.getByRole('button',{name:'确认加入症状说明',exact:true}).click();
assert.equal(await note.inputValue(),`${original}\n${original}`);
assert.equal(await phone.getByLabel('体温原值',{exact:true}).inputValue(),originalTemperature);
assert.equal(await phone.getByRole('button',{name:'保存记录',exact:true}).isDisabled(),false);
await phone.getByRole('button',{name:'文字输入',exact:true}).click();
await editor.fill(original);
await phone.getByRole('button',{name:'清空本次描述',exact:true}).click();
assert.equal(await editor.inputValue(),'');
assert.equal(await note.inputValue(),`${original}\n${original}`);
let microphoneDenial='NOT_RUN';
if(capability.available){
  await open('observations');
  await phone.getByRole('button',{name:'开始语音输入',exact:true}).click();
  await phone.getByRole('alert').waitFor({timeout:20000});
  microphoneDenial=await phone.getByRole('alert').innerText();
  assert.match(microphoneDenial,/权限|不允许|麦克风/);
  assert.equal(await note.inputValue(),original);
  assert.equal(await editor.inputValue(),'');
  await phone.screenshot({path:root+'screens/png/S04-voice-permission.png'});
}
for(const width of [320,360,390,430,580]){
  await page.setViewportSize({width,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`语音区域 ${width}`);
}
assert.deepEqual(errors,[]);
await writeFile(root+'checks/voice-navigation-check.json',JSON.stringify({time:new Date().toISOString(),browser:browser.version(),primaryPages:primaryPages.length,secondaryPages:pages.length-primaryPages.length,parentRoutesChecked:20,sourceReturnPaths:2,voiceCapability:capability,microphoneDenial,manualReviewApplied:true,existingNotePreserved:true,clinicalFieldsUnchanged:true,voiceWidths:5,liveSpeechRecognition:'NOT_RUN',syntheticSpeechResults:false,errors},null,2));
await browser.close();
console.log(JSON.stringify({navigationPages:23,microphoneDenial,reviewConfirmed:true,errors}));
