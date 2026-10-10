import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../guardian-h5/node_modules/playwright/index.mjs';

const root = new URL('../identity/', import.meta.url);
const browser = await chromium.launch({channel:'chrome',headless:true});
const page = await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('response', response => {if(response.status() >= 400) errors.push(`${response.status()} ${response.url()}`);});
const address = 'http://127.0.0.1:5186/identity/';
async function ready() {
  await page.waitForSelector('.character-card');
  await page.evaluate(async () => {await document.fonts.ready;await Promise.all([...document.images].map(image => image.decode()));});
}
await page.goto(address);
await ready();
assert.equal(await page.locator('.character-card').count(),16);
await page.locator('#brand-board').screenshot({path:fileURLToPath(new URL('previews/brand-board.png',root))});
await page.getByRole('group',{name:'年龄',exact:true}).getByRole('button',{name:'1–3岁',exact:true}).click();
assert.equal(await page.locator('.character-card').count(),8);
await page.getByRole('group',{name:'性别',exact:true}).getByRole('button',{name:'女童',exact:true}).click();
assert.equal(await page.locator('.character-card').count(),4);
await page.getByRole('group',{name:'季节',exact:true}).getByRole('button',{name:'冬季',exact:true}).click();
assert.equal(await page.locator('.character-card').count(),1);
await page.locator('.character-card').click();
assert.match(await page.locator('#selected-image').getAttribute('src'),/toddler-girl-winter/);
assert.match(await page.locator('#download-master').getAttribute('href'),/master\/toddler-girl-winter.png$/);
const widths = [320,390,768,1440];
for (const width of widths) {
  await page.setViewportSize({width,height:1000});
  await page.goto(address);
  await ready();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,`页面横向溢出：${width}`);
}
await page.screenshot({path:fileURLToPath(new URL('previews/gallery-desktop.png',root)),fullPage:true});
await page.goto(address+'?board=all');
await ready();
await page.screenshot({path:fileURLToPath(new URL('previews/character-board.png',root)),fullPage:true});
for (const season of ['spring','summer','autumn','winter']) {
  await page.goto(address+`?board=all&season=${season}`);
  await ready();
  assert.equal(await page.locator('.character-card').count(),4);
  await page.screenshot({path:fileURLToPath(new URL(`previews/${season}-board.png`,root)),fullPage:true});
}
assert.deepEqual(errors,[]);
const result={characters:16,filterChecks:3,selectionAndDownload:true,widths,errors};
await writeFile(new URL('checks/browser-check.json',root),JSON.stringify(result,null,2));
await browser.close();
console.log(JSON.stringify(result));
