import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from '../../guardian-h5/node_modules/playwright/index.mjs';
import {pages} from '../preview/catalog.js';
import {alertViews} from '../preview/alert-machine.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const browser=await chromium.launch({channel:'chrome',headless:true});
const page=await browser.newPage({reducedMotion:'reduce'});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
async function settle(){
  await page.evaluate(async()=>{
    await document.fonts.ready;
    await Promise.all([...document.images].map(image=>image.decode()));
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  });
}
const checks=[];
async function check(label){
  await settle();
  const result=await page.evaluate(()=>{
    const phone=document.querySelector('.phone');
    const frame=document.querySelector('.phone-preview').getBoundingClientRect();
    const bounds=phone.getBoundingClientRect();
    const scroll=phone.querySelector('.phone-scroll');
    const home=phone.dataset.page==='home';
    const defects=[];
    let illustrationSpacing=null;
    if(home){
      const image=phone.querySelector('.symptom-compact>img').getBoundingClientRect();
      const card=phone.querySelector('.home-twin').getBoundingClientRect();
      const stage=phone.querySelector('.home-child-stage').getBoundingClientRect();
      const columns=phone.querySelector('.symptom-columns').getBoundingClientRect();
      const scale=bounds.width/phone.clientWidth;
      illustrationSpacing={left:(image.left-card.left)/scale,right:(card.right-image.right)/scale,top:(image.top-stage.bottom)/scale,bottom:(columns.top-image.bottom)/scale};
      if(illustrationSpacing.left<20-0.1||illustrationSpacing.right<20-0.1||illustrationSpacing.top<8-0.1||illustrationSpacing.bottom<8-0.1)defects.push('症状插图安全间距不足');
      for(const node of phone.querySelectorAll('.page-body button,.page-body img,.page-body strong,.page-body small')){
        const r=node.getBoundingClientRect();
        const card=node.closest('.notice-card,.home-twin,.home-entry-grid').getBoundingClientRect();
        if(r.left<card.left-0.5||r.right>card.right+0.5||r.top<card.top-0.5||r.bottom>card.bottom+0.5)defects.push(node.outerHTML.slice(0,160));
      }
      for(const button of phone.querySelectorAll('.page-body button')){
        const b=button.getBoundingClientRect();
        const walker=document.createTreeWalker(button,NodeFilter.SHOW_TEXT);
        while(walker.nextNode()){
          if(!walker.currentNode.textContent.trim())continue;
          const range=document.createRange();range.selectNodeContents(walker.currentNode);
          for(const r of range.getClientRects())if(r.left<b.left-0.5||r.right>b.right+0.5||r.top<b.top-0.5||r.bottom>b.bottom+0.5)defects.push(walker.currentNode.textContent);
        }
      }
      const regions=[...phone.querySelectorAll('.page-body>*')].map(node=>node.getBoundingClientRect());
      if(regions.some((r,index)=>index&&r.top<regions[index-1].bottom-0.5))defects.push('首页区域重叠');
    }
    return {viewport:[innerWidth,innerHeight],page:phone.dataset.page,rootFits:document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight,frameFits:bounds.left>=frame.left&&bounds.right<=frame.right&&bounds.top>=frame.top&&bounds.bottom<=frame.bottom,phoneSize:[phone.clientWidth,phone.clientHeight],horizontalFits:scroll.scrollWidth<=scroll.clientWidth,homeFits:!home||scroll.scrollHeight<=scroll.clientHeight,illustrationSpacing,defects};
  });
  assert.equal(result.rootFits,true,`${label} 页面边界`);
  assert.equal(result.frameFits,true,`${label} 画框边界`);
  assert.deepEqual(result.phoneSize,[390,844],`${label} 设计尺寸`);
  assert.equal(result.horizontalFits,true,`${label} 内容宽度`);
  assert.equal(result.homeFits,true,`${label} 首页高度`);
  assert.deepEqual(result.defects,[],`${label} 卡片与文字边界`);
  checks.push({label,...result});
}
await page.goto('http://127.0.0.1:5186/?page=home');
await page.locator('.phone').waitFor();
for(const [width,height] of [[320,568],[360,640],[390,844],[430,932],[768,1024],[1119,1293],[1280,720],[1440,900],[1440,1050]]){
  await page.setViewportSize({width,height});
  await check(`首页 ${width}×${height}`);
}
for(const viewport of [{width:390,height:844},{width:1280,height:720}]){
  await page.setViewportSize(viewport);
  for(const [,,key] of pages){
    await page.getByLabel('切换设计页面',{exact:true}).selectOption(key);
    await page.locator(`.phone[data-page="${key}"]`).waitFor();
    await check(`${key} ${viewport.width}×${viewport.height}`);
  }
}
await page.setViewportSize({width:1440,height:900});
await page.getByLabel('切换设计页面',{exact:true}).selectOption('home');
for(const theme of ['light','dark']){
  if(theme==='dark')await page.getByRole('button',{name:'深色',exact:true}).click();
  for(const state of Object.keys(alertViews)){
    await page.getByLabel('切换预警设计状态',{exact:true}).selectOption(state);
    await check(`${theme} ${state}`);
  }
}
for(const [width,height] of [[320,568],[390,844],[1280,720]]){
  await page.setViewportSize({width,height});
  for(const name of ['健康档案','查看发热示意与记录','查看皮疹示意与记录','查看关节炎示意与记录']){
    await page.getByRole('button',{name,exact:true}).click();
    const dialog=page.locator('dialog[open]');
    await dialog.waitFor();
    const fits=await dialog.evaluate(node=>{
      const r=node.getBoundingClientRect();
      const close=node.querySelector('button[aria-label="关闭资料面板"]').getBoundingClientRect();
      return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight&&close.top>=r.top&&close.bottom<=r.bottom;
    });
    assert.equal(fits,true,`${name} 弹层 ${width}×${height}`);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog[open]').count(),0);
  }
}
await page.setViewportSize({width:1440,height:900});
await page.getByRole('button',{name:'浅色',exact:true}).click();
await page.getByLabel('切换预警设计状态',{exact:true}).selectOption('warning');
await settle();
await page.screenshot({path:root+'screens/design-overview.png'});
const capture=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:2,reducedMotion:'reduce'});
capture.on('pageerror',error=>errors.push(error.message));
capture.on('console',message=>{if(message.type()==='error')errors.push(message.text());});
await capture.goto('http://127.0.0.1:5186/?capture=1&page=home');
await capture.locator('.phone').waitFor();
await capture.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(image=>image.decode()));});
assert.equal((await capture.locator('.phone').boundingBox()).height,844);
await capture.locator('.phone').screenshot({path:root+'screens/png/S03.png'});
const captureChecks=[];
for(const width of [320,360,390,430,580]){
  await capture.setViewportSize({width,height:844});
  await capture.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal((await capture.locator('.phone').boundingBox()).height,844,`首页图稿 ${width}`);
  assert.equal(await capture.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`首页宽度 ${width}`);
  const spacing=await capture.evaluate(()=>{
    const image=document.querySelector('.home-twin .symptom-compact>img').getBoundingClientRect();
    const card=document.querySelector('.home-twin').getBoundingClientRect();
    const stage=document.querySelector('.home-child-stage').getBoundingClientRect();
    const columns=document.querySelector('.home-twin .symptom-columns').getBoundingClientRect();
    return {left:image.left-card.left,right:card.right-image.right,top:image.top-stage.bottom,bottom:columns.top-image.bottom};
  });
  assert.equal(spacing.left>=20-0.1&&spacing.right>=20-0.1&&spacing.top>=8-0.1&&spacing.bottom>=8-0.1,true,`症状图片间距 ${width}`);
  captureChecks.push({width,spacing,height:844});
}
await capture.setViewportSize({width:390,height:844});
for(const [query,path] of [['theme=dark','screens/png/S03-dark.png'],...Object.keys(alertViews).map(state=>[`alert=${state}`,`alerts/png/${state}.png`])]){
  await capture.goto(`http://127.0.0.1:5186/?capture=1&page=home&${query}`);
  await capture.locator('.phone').waitFor();
  await capture.evaluate(async()=>{await document.fonts.ready;await Promise.all([...document.images].map(image=>image.decode()));});
  assert.equal((await capture.locator('.phone').boundingBox()).height,844);
  await capture.locator('.phone').screenshot({path:root+path});
}
assert.deepEqual(errors,[]);
const result={createdAt:new Date().toISOString(),checks,captureChecks,previewChecks:checks.length,dialogChecks:12,captureWidthChecks:captureChecks.length,captureHeight:844,errors,visualVerified:false};
await writeFile(root+'checks/preview-layout-check.json',JSON.stringify(result,null,2));
console.log(JSON.stringify({previewChecks:checks.length,dialogChecks:12,captureHeight:844,errors}));
await browser.close();
