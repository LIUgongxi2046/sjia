import { chromium } from '../../guardian-h5/node_modules/playwright/index.mjs';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const browser = await chromium.launch({channel:'chrome',headless:true});
const page = await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
for (const category of ['screens','states','alerts']) {
  await page.goto(`http://127.0.0.1:5186/?board=${category}`);
  await page.locator(category==='alerts'?'.alert-board':'.contact-board').waitFor();
  await page.evaluate(async () => {await document.fonts.ready;await Promise.all([...document.images].map(image => image.decode()));});
  await page.screenshot({path:root+`${category}/contact-board.png`,fullPage:true});
}
await page.setViewportSize({width:1440,height:900});
await page.goto('http://127.0.0.1:5186/?page=home');
await page.locator('.phone').waitFor();
await page.evaluate(async () => {await document.fonts.ready;await Promise.all([...document.images].map(image => image.decode()));});
await page.screenshot({path:root+'screens/design-overview.png',fullPage:true});
await browser.close();
console.log('页面、状态、预警与首页预览板已导出');
