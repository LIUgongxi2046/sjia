import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const output = '../.qa-work/QA03/ui-updated';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: 'light' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.goto('http://127.0.0.1:5173/login');
await page.getByPlaceholder('账号', { exact: true }).fill('acceptance-family');
await page.getByPlaceholder('密码（至少 8 位）').fill('Local-accept-2026');
await page.getByRole('button', { name: '登录', exact: true }).click();
await page.waitForURL('http://127.0.0.1:5173/home');
await page.getByRole('button', { name: '健康档案', exact: true }).waitFor();
const routes = [
  ['S03', '/home'], ['S01', '/login'], ['S02-A', '/associations'], ['S02-B', '/associations/new'],
  ['S04', '/register/observations'], ['S05', '/register/reports'], ['S06', '/register/therapies'],
  ['S07', '/records'], ['S09', '/records/analytes/temp'], ['S11-A', '/alerts'],
  ['S12-A', '/summaries'], ['S13-A', '/account'], ['S13-B', '/account/data-use'], ['S14', '/drafts'],
  ['S15-A', '/access-denied'], ['S15-B', '/service-unavailable'], ['S15-C', '/page-unavailable'],
];
const results = [];
for (const [screen, path] of routes) {
  await page.goto(`http://127.0.0.1:5173${path}`);
  await page.locator('h1').waitFor();
  await page.waitForLoadState('networkidle');
  await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.images].map(image => image.complete ? Promise.resolve() : new Promise(resolve => { image.onload = resolve; image.onerror = resolve; }))); });
  await page.screenshot({ path: `${output}/${screen}.png`, fullPage: true });
  await page.screenshot({ path: `${output}/${screen}-viewport.png` });
  const geometry = await page.evaluate(() => {
    const header = document.querySelector('.task-header').getBoundingClientRect();
    const patient = document.querySelector('.patient-context')?.getBoundingClientRect();
    const actions = document.querySelector('.home-quick-actions')?.getBoundingClientRect();
    const navigation = document.querySelector('.tab-bar')?.getBoundingClientRect();
    return { width: innerWidth, scrollWidth: document.documentElement.scrollWidth, header: { x: header.x, y: header.y, width: header.width, bottom: header.bottom }, patientTop: patient?.top, actionsBottom: actions?.bottom, navigationTop: navigation?.top };
  });
  assert.ok(geometry.scrollWidth <= geometry.width, `${screen}: 页面出现横向溢出`);
  assert.equal(geometry.header.y, 0, `${screen}: 标题没有显示在页面顶部`);
  assert.equal(geometry.header.x, 0, `${screen}: 标题没有占满页面宽度`);
  assert.equal(geometry.header.width, geometry.width, `${screen}: 标题宽度不符合页面宽度`);
  if (geometry.patientTop !== undefined) assert.ok(geometry.patientTop >= geometry.header.bottom, `${screen}: 患儿归属需要位于标题下方`);
  if (screen === 'S03') assert.ok(geometry.actionsBottom <= geometry.navigationTop, '首页快捷记录需要在首屏完整显示');
  results.push({ screen, path, headings: await page.locator('h1,h2').allTextContents(), dimensions: await page.evaluate(() => ({ viewport: [innerWidth, innerHeight], scrollHeight: document.documentElement.scrollHeight, scrollWidth: document.documentElement.scrollWidth })), quickActions: screen === 'S03' ? await page.locator('.home-quick-actions').boundingBox() : null });
}
assert.deepEqual(errors, []);
await writeFile(`${output}/inspection.json`, JSON.stringify({ capturedAt: new Date().toISOString(), browser: browser.version(), data: 'existing-local-simulation', scope: 'read-only pages; no clinical records created', results, errors }, null, 2));
await browser.close();
console.log(JSON.stringify({ captured: results.length, errors }));
