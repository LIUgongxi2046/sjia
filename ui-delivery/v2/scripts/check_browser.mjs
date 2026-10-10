import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from '../../../guardian-h5/node_modules/playwright/index.mjs';
import {screens,reviewStates,statesFor} from '../src/catalog.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const temporary=fileURLToPath(new URL('../../../.ui-work/ui2-browser/',import.meta.url));
await mkdir(temporary,{recursive:true});
await mkdir(root+'checks',{recursive:true});
const browser=await chromium.launch({channel:'chrome',headless:true,env:{...process.env,TMPDIR:temporary}});
const page=await browser.newPage({viewport:{width:1440,height:1050},reducedMotion:'reduce'});
const errors=[],requests=[],checks=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('request',request=>requests.push({method:request.method(),url:request.url()}));
page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});

async function inspect(label){
  await page.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(image=>image.decode()));});
  const result=await page.evaluate(()=>{
    const content=document.querySelector('.phone-scroll');
    const device=document.querySelector('.device').getBoundingClientRect();
    const nodes=[...document.querySelectorAll('.page-content input,.page-content select,.page-content textarea,.page-content .card,.page-content .button')].filter(node=>node.getClientRects().length>0);
    const overflow=nodes.filter(node=>{const box=node.getBoundingClientRect();return box.left<device.left-1||box.right>device.right+1;}).map(node=>({tag:node.tagName,class:node.className,text:node.textContent.slice(0,60)}));
    return {pageFits:document.documentElement.scrollWidth<=innerWidth,contentFits:content.scrollWidth<=content.clientWidth+1,controlsFit:overflow.length===0,overflow,images:[...document.images].every(image=>image.complete&&image.naturalWidth>0),navigation:[...document.querySelectorAll('.bottom-nav button')].map(node=>node.textContent.trim())};
  });
  assert.equal(result.pageFits,true,`${label} 页面宽度`);
  assert.equal(result.contentFits,true,`${label} 内容宽度`);
  assert.equal(result.controlsFit,true,`${label} 控件边界 ${JSON.stringify(result.overflow)}`);
  assert.equal(result.images,true,`${label} 资产读取`);
  checks.push({label,...result});
}

await page.goto('http://127.0.0.1:5188/?screen=home');
await page.locator('.device').waitFor();
let stateChecks=0;
for(const screen of screens){
  await page.locator('.nav-group').getByRole('button',{name:screen.name,exact:screen.group!=='资料整理 2.0'}).click();
  assert.equal(await page.locator('.canvas-head h1').textContent(),screen.name);
  for(const state of statesFor(screen.key)){
    const label=reviewStates.find(item=>item[0]===state)[1];
    await page.locator('.state-grid').getByRole('button',{name:label,exact:true}).click();
    if(state!=='default')assert.equal(await page.locator('.status-panel h2').textContent(),reviewStates.find(item=>item[0]===state)[2]);
    await inspect(`${screen.key}/${state}`);stateChecks+=1;
  }
}
console.log(JSON.stringify({stage:'状态检查',count:stateChecks}));
for(const width of [320,360,390,430,580]){
  await page.setViewportSize({width,height:844});
  for(const screen of screens){
    await page.goto(`http://127.0.0.1:5188/?screen=${screen.key}&mode=phone`);
    await page.locator('.device').waitFor();
    await inspect(`${screen.key}/${width}`);
    const navigation=await page.locator('.bottom-nav').count();
    assert.equal(navigation,['home','records','account'].includes(screen.key)?1:0);
  }
}
console.log(JSON.stringify({stage:'宽度检查',count:screens.length*5}));
await page.goto('http://127.0.0.1:5188/?screen=home&mode=phone');
await page.getByRole('button',{name:'拍照添加报告 保留原件，逐项确认'}).click();
assert.equal(await page.locator('.app-header h1').textContent(),'拍照添加报告');
await page.getByRole('button',{name:'逐项确认内容 每个项目保留自己的采样时间'}).click();
assert.equal(await page.locator('.app-header h1').textContent(),'逐项确认');
await page.getByLabel('页面说明').click();
await page.getByRole('dialog',{name:'页面说明'}).waitFor();
assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'关闭说明');
await page.keyboard.press('Shift+Tab');
assert.equal(await page.evaluate(()=>document.activeElement.textContent),'继续查看');
await page.keyboard.press('Escape');
assert.equal(await page.locator('.sheet').count(),0);
assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'页面说明');
await page.getByRole('button',{name:'确认并保存资料',exact:true}).click();
assert.match(await page.locator('dialog[open]').textContent(),/尚未提交/);
await page.keyboard.press('Escape');
await page.goto('http://127.0.0.1:5188/?screen=reminders&mode=phone');
const toggle=page.getByRole('switch',{name:'启用资料补充提醒'});
assert.equal(await toggle.isChecked(),false);
await toggle.check();
assert.equal(await toggle.isChecked(),true);
await page.getByRole('button',{name:'关闭提醒',exact:true}).click();
assert.equal(await toggle.isChecked(),false);
await page.keyboard.press('Escape');
for(const screen of screens){
  await page.goto(`http://127.0.0.1:5188/?screen=${screen.key}&mode=phone`);
  await page.evaluate(()=>{for(const node of document.querySelectorAll('.device *')){const size=parseFloat(getComputedStyle(node).fontSize);node.dataset.originalSize=String(size);}for(const node of document.querySelectorAll('.device *'))node.style.fontSize=`${Number(node.dataset.originalSize)*2}px`;});
  await inspect(`${screen.key}/文字200%`);
}
assert.deepEqual(errors,[]);
assert.equal(requests.filter(request=>request.method!=='GET'||!request.url.startsWith('http://127.0.0.1:5188/')).length,0);
const result={createdAt:new Date().toISOString(),screens:screens.length,stateChecks,widthChecks:screens.length*5,textScaleChecks:screens.length,checks,errors,businessRequests:0,screenshots:0,visualVerified:false,scope:'设计预览 DOM、边界、状态和交互'};
await writeFile(root+'checks/browser-check.json',JSON.stringify(result,null,2));
console.log(JSON.stringify({screens:result.screens,stateChecks,widthChecks:result.widthChecks,textScaleChecks:result.textScaleChecks,errors,businessRequests:0}));
await browser.close();
