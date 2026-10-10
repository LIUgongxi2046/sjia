import assert from 'node:assert/strict';
import { writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../guardian-h5/node_modules/playwright/index.mjs';
import { pages, states } from '../preview/catalog.js';
import { alertViews, careGuidance, makeAlert, transitionAlert } from '../preview/alert-machine.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const base = 'http://127.0.0.1:5186/';
const sample = process.argv.includes('--sample');
const browser = await chromium.launch({channel:'chrome',headless:true});
const context = await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:2,reducedMotion:'reduce'});
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => {if(message.type() === 'error') errors.push({text:message.text(),location:message.location()});});
const captures = [];
async function open(query) {
  const response = await page.goto(base + query);
  assert.equal(response.status(),200);
  await page.locator('.phone').waitFor();
  await page.evaluate(async () => { await document.fonts.ready;await Promise.all([...document.images].map(image => image.decode())); });
  assert.deepEqual(errors,[]);
  assert.equal(await page.locator('.mobile-brand').count(),0);
  assert.equal(await page.locator('.phone .design-example').count(),0);
  assert.equal(await page.locator('.phone .notice-template').count(),0);
  const key=await page.locator('.phone').getAttribute('data-page');
  const stateId=await page.locator('.phone').getAttribute('data-state');
  const primary=['home','records','account'].includes(key)&&!stateId;
  assert.equal(await page.locator('.phone .mobile-nav').count(),primary?1:0,`底部导航 ${key} ${stateId}`);
  assert.equal(await page.getByRole('button',{name:'返回上级页面',exact:true}).count(),primary?0:1);
  const brand=page.locator('.phone .brand-signature');
  const branded=!stateId&&['login','account'].includes(key);
  assert.equal(await brand.count(),branded?1:0,`品牌位置 ${key} ${stateId}`);
  assert.equal(await page.locator('.app-masthead .brand-signature').count(),0);
  if(branded){
    assert.equal(await brand.locator('strong').innerText(),'sJIA 守护者联盟');
    const logo=brand.locator('img');
    assert.ok((await logo.getAttribute('src')).includes('logo-embrace'));
    assert.equal(await logo.evaluate(image=>image.naturalWidth),256);
    assert.ok((await logo.boundingBox()).width>=32);
    if(key==='account')assert.equal(await brand.evaluate(node=>node.nextElementSibling?.textContent.includes('退出登录')),true);
    if(key==='login')assert.equal(await brand.evaluate(node=>node.nextElementSibling?.classList.contains('session-ready')),true);
  }
}
for (const [id,name,key] of (sample ? pages.slice(0,3) : pages)) {
  await open(`?capture=1&page=${key}`);
  const file = `screens/png/${id}.png`;
  await page.locator('.phone').screenshot({path:root+file});
  captures.push({id,name,file});
}
if (!sample) {
  for (const state of states) {
    await open(`?capture=1&state=${state[0]}`);
    const file = `states/png/${state[0]}.png`;
    await page.locator('.phone').screenshot({path:root+file});
    captures.push({id:state[0],name:state[1],file});
  }
  await open('?capture=1&page=home&sheet=孩子的记录');
  await page.locator('dialog[open]').waitFor();
  await page.screenshot({path:root+'screens/png/TW-PANEL.png'});
  await page.getByRole('button',{name:'关闭资料面板'}).click();
  await page.getByRole('button',{name:'健康档案',exact:true}).click();
  await page.getByRole('button',{name:'体温记录 38.2 °C'}).first().click();
  await page.locator('dialog[open]').waitFor();
  assert.equal(await page.locator('.sheet-title h2').innerText(),'体温记录');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('dialog[open]').count(),0);
  await page.getByRole('navigation',{name:'主要导航'}).getByRole('button',{name:'记录',exact:true}).click();
  assert.equal(await page.locator('.phone').getAttribute('data-page'),'records');
  await open('?capture=1&page=reports&step=confirm');
  await page.getByRole('button',{name:'保存原图与记录'}).waitFor();
  assert.equal(await page.getByRole('button',{name:'保存原图与记录'}).isDisabled(),true);
  await page.locator('.phone').screenshot({path:root+'screens/png/S05-confirm.png'});
  await open('?capture=1&page=therapies&step=confirm');
  await page.locator('.phone').screenshot({path:root+'screens/png/S06-confirm.png'});
  for(const [key,id] of [['reports','S05'],['therapies','S06']]){
    await open(`?capture=1&page=${key}`);
    await page.getByRole('button',{name:'拍照添加',exact:true}).click();
    assert.equal(await page.getByLabel('拍照文件',{exact:true}).getAttribute('capture'),'environment');
    await page.locator('.phone').screenshot({path:root+`screens/png/${id}-camera.png`});
  }
  await open('?capture=1&page=home&theme=dark');
  await page.locator('.phone').screenshot({path:root+'screens/png/S03-dark.png'});
  await mkdir(root+'alerts/png',{recursive:true});
  for (const [status,view] of Object.entries(alertViews)) {
    await open(`?capture=1&page=home&alert=${status}`);
    assert.equal(await page.locator('.notice-card').getAttribute('data-alert-state'),status);
    assert.equal(await page.locator('.notice-card h2').innerText(),view.title);
    assert.equal(await page.locator('.notice-care strong').innerText(),careGuidance[status].decision);
    await page.locator('.phone').screenshot({path:root+`alerts/png/${status}.png`});
  }
  await open('?capture=1&page=home');
  assert.equal(await page.locator('.app-masthead h1').innerText(),'陪伴安安，健康成长');
  assert.equal(await page.locator('.page-body > *').count(),3);
  assert.equal(await page.locator('.phone-scroll .clinical-panel,.phone-scroll .daily-summary,.phone-scroll .record-card').count(),0);
  assert.doesNotMatch(await page.locator('.phone-scroll').innerText(),/设计|情景|规则版本|MS 评分/);
  assert.ok((await page.locator('.phone').boundingBox()).height<=900);
  for (const name of ['发热','皮疹','关节炎']) {
    await page.getByRole('button',{name:`查看${name}示意与记录`,exact:true}).click();
    await page.locator('dialog[open]').waitFor();
    assert.equal(await page.locator('.sheet-title h2').innerText(),name);
    assert.ok((await page.locator('.symptom-detail').innerText()).includes('待核对，未作为评分变量'));
    if(name==='关节炎')assert.ok((await page.locator('.symptom-detail').innerText()).includes('关节炎尚未确认'));
    await page.keyboard.press('Escape');
  }
  await page.getByRole('button',{name:'查看详情',exact:true}).click();
  await page.getByRole('button',{name:'就医联系',exact:true}).click();
  assert.ok((await page.locator('dialog[open]').innerText()).includes('尚未配置获准联系人'));
  await page.keyboard.press('Escape');
  await open('?capture=1&page=reports');
  await page.getByText('查看待补充项目 · 7组',{exact:true}).click();
  assert.equal(await page.locator('.missing-labs li').count(),7);
  await open('?capture=1&page=home&alert=urgent');
  await page.getByRole('button',{name:'查看详情',exact:true}).click();
  assert.equal(await page.locator('.notice-card').getAttribute('data-alert-state'),'urgent');
  assert.ok((await page.locator('.notice-kicker').innerText()).includes('已查看'));
  await open('?capture=1&page=observations');
  await page.getByRole('button',{name:'文字输入',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'确认加入症状说明',exact:true}).isDisabled(),true);
  await page.locator('.phone').screenshot({path:root+'screens/png/S04-voice-review.png'});
  await page.getByRole('button',{name:'保存记录',exact:true}).click();
  await page.getByRole('heading',{name:'记录已保存'}).waitFor();
  await open('?capture=1&page=records');
  await page.getByRole('button',{name:'检验报告',exact:true}).click();
  assert.equal(await page.locator('.record-card').count(),1);
  for (const width of [320,360,390,430,580]) {
    await page.setViewportSize({width,height:844});
    for (const [, , key] of pages) {
      await open(`?capture=1&page=${key}`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),false,`${width} ${key}`);
      if(key==='home')assert.ok((await page.locator('.phone').boundingBox()).height<=844,`首页首屏高度 ${width}`);
    }
  }
  await page.setViewportSize({width:1440,height:1050});
  await open('?page=home');
  await page.screenshot({path:root+'screens/design-overview.png',fullPage:true});
  await page.goto(base+'?board=alerts');
  await page.locator('.alert-board').waitFor();
  await page.screenshot({path:root+'alerts/contact-board.png',fullPage:true});
}
const urgent = makeAlert('urgent');
assert.equal(transitionAlert(urgent,'READ').status,'urgent');
assert.equal(transitionAlert(urgent,'READ').read,true);
assert.equal(transitionAlert(urgent,'INVALIDATE').status,'expired');
assert.throws(()=>transitionAlert(makeAlert('expired'),'READ'));
assert.equal(transitionAlert(makeAlert('failed'),'RETRY').status,'evaluating');
assert.equal(transitionAlert(makeAlert('evaluating'),'NEEDS_DATA').status,'incomplete');
assert.deepEqual(errors,[]);
await mkdir(root+'checks',{recursive:true});
await writeFile(root+`checks/${sample ? 'sample' : 'design'}-browser-check.json`,JSON.stringify({time:new Date().toISOString(),browser:browser.version(),capability:'DESIGN',content:'DESIGN_EXAMPLE',sample,captures,errors,overflowChecks:sample?0:115,homeFirstScreenChecks:sample?0:5,alertScreens:sample?0:10,careGuidanceChecks:sample?0:10,symptomChecks:sample?0:3,machineAssertions:6,interactionChecks:sample?0:13},null,2));
console.log(JSON.stringify({captures:captures.length,errors,mode:sample?'sample':'full'}));
await browser.close();
