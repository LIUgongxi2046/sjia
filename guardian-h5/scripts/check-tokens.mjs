import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const baseURL = 'http://127.0.0.1:4173';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
const page = await context.newPage();

await page.goto(`${baseURL}/home`);
const tokens = await page.evaluate(() => {
  const style = getComputedStyle(document.documentElement);
  const names = ['--color-brand', '--color-page', '--header-start', '--radius-card', '--touch-target', '--motion-normal', '--focus-width'];
  return Object.fromEntries(names.map(n => [n, style.getPropertyValue(n).trim()]));
});
assert.equal(tokens['--color-brand'], '#007aff');
assert.equal(tokens['--color-page'], '#f2f2f7');
assert.equal(tokens['--header-start'], '#173f70');
assert.equal(tokens['--radius-card'], '24px');
assert.equal(tokens['--touch-target'], '44px');
assert.ok(tokens['--motion-normal'] === '240ms' || tokens['--motion-normal'] === '.24s', tokens['--motion-normal']);

const tabMinHeight = await page.locator('nav[aria-label="主要导航"] .tab-link').first().evaluate(el => getComputedStyle(el).minHeight);
assert.equal(tabMinHeight, '56px');

const threeDIconCount = await page.locator('nav svg.three-d-icon image').count();
assert.equal(threeDIconCount, 3);

console.log(JSON.stringify({ tokens, tabMinHeight, threeDIconCount }, null, 2));
await browser.close();
