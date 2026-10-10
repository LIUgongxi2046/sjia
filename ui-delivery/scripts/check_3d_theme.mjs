import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from '../../guardian-h5/node_modules/playwright/index.mjs';
import {pages} from '../preview/catalog.js';

const root=fileURLToPath(new URL('../',import.meta.url));
const browser=await chromium.launch({channel:'chrome',headless:true});
const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:2,reducedMotion:'reduce'});
const page=await context.newPage();
const errors=[];
const sources=new Set();
page.on('pageerror',error=>errors.push(error.message));
page.on('console',message=>{if(message.type()==='error')errors.push(message.text());});

for(const [id,,key] of pages){
  const response=await page.goto(`http://127.0.0.1:5186/?capture=1&page=${key}`);
  assert.equal(response.status(),200,id);
  await page.locator('.phone').waitFor();
  const result=await page.evaluate(()=>{
    const phone=document.querySelector('.phone');
    const header=document.querySelector('.app-masthead');
    const icons=[...phone.querySelectorAll('svg')].filter(icon=>!icon.closest('.trend'));
    return {
      icons:icons.length,
      oldIcons:icons.filter(icon=>!icon.classList.contains('three-d-icon')).length,
      unclippedIcons:icons.filter(icon=>getComputedStyle(icon).overflow!=='hidden').length,
      sources:[...new Set(icons.map(icon=>icon.querySelector('image')?.href.baseVal))],
      headerGradient:getComputedStyle(header).backgroundImage,
      overflow:document.documentElement.scrollWidth>innerWidth,
      nav:phone.querySelector('.mobile-nav')?.getBoundingClientRect().height||0,
    };
  });
  assert.ok(result.icons>0,id);
  assert.equal(result.oldIcons,0,id);
  assert.equal(result.unclippedIcons,0,id);
  assert.ok(result.headerGradient.includes('radial-gradient'),id);
  assert.equal(result.overflow,false,id);
  assert.equal(result.nav>0,['home','records','account'].includes(key),id);
  for(const source of result.sources){assert.ok(source,id);sources.add(source);}
}

assert.equal(sources.size,3);
for(const source of sources){
  const image=await page.evaluate(async url=>{
    const image=new Image();image.src=url;await image.decode();
    return {width:image.naturalWidth,height:image.naturalHeight};
  },source);
  assert.deepEqual(image,{width:1254,height:1254});
}

for(const theme of ['light','dark']){
  await page.goto(`http://127.0.0.1:5186/?capture=1&page=home&theme=${theme}`);
  const material=await page.evaluate(()=>({
    card:getComputedStyle(document.querySelector('.home-twin')).boxShadow,
    action:getComputedStyle(document.querySelector('.home-entry-grid>button:first-child')).backgroundImage,
    stage:getComputedStyle(document.querySelector('.home-child-stage')).backgroundImage,
  }));
  assert.notEqual(material.card,'none',theme);
  assert.ok(material.action.includes('linear-gradient'),theme);
  assert.ok(material.stage.includes('radial-gradient'),theme);
}

const warningBorders=new Set();
for(const status of ['attention','warning','urgent']){
  await page.goto(`http://127.0.0.1:5186/?capture=1&page=home&alert=${status}`);
  assert.equal(await page.locator('.notice-card').getAttribute('data-alert-state'),status);
  assert.equal(await page.locator('.notice-care strong').count(),1);
  warningBorders.add(await page.locator('.notice-card').evaluate(card=>getComputedStyle(card).borderLeftColor));
}
assert.equal(warningBorders.size,3);

assert.deepEqual(errors,[]);
const output={time:new Date().toISOString(),browser:browser.version(),pagesChecked:pages.length,iconAtlases:sources.size,lightAndDarkMaterials:true,warningStates:3,errors,visualVerified:false};
await writeFile(root+'checks/theme-3d-check.json',JSON.stringify(output,null,2));
await browser.close();
console.log(JSON.stringify(output));
