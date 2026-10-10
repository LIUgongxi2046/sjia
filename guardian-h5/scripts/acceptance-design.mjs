import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const startedAt = new Date().toISOString();
const baseURL = process.env.SJIA_TEST_URL || 'http://127.0.0.1:5173';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
const page = await context.newPage();
const results = [];
const pageErrors = [];
page.on('pageerror', error => pageErrors.push(error.message));
async function check(id, assertion) {
  try {
    await assertion();
    results.push({ id, status: 'PASS' });
  } catch (error) {
    results.push({ id, status: 'FAIL', error: error.message });
  }
}
await page.goto(`${baseURL}/prototype`);
await page.locator('h1').waitFor();
const routes = await page.locator('.link-group a').evaluateAll(links => links.map(link => link.getAttribute('href')));
assert.equal(routes.length, 23);
for (const route of routes) {
  await page.goto(`${baseURL}${route}`);
  await page.locator('h1').waitFor();
  await check(`${route}:heading`, async () => assert.equal(await page.locator('h1').count(), 1));
  await check(`${route}:navigation`, async () => {
    assert.equal(await page.getByRole('navigation', { name: '主要导航' }).count(), ['/home', '/records', '/account'].includes(route) ? 1 : 0);
  });
  await check(`${route}:brand`, async () => {
    assert.equal(await page.locator('.brand').count(), ['/login', '/account'].includes(route) ? 1 : 0);
  });
  await check(`${route}:task-header`, async () => {
    assert.equal(await page.locator('.task-header h1').count(), 1);
    assert.match(await page.locator('.task-header').evaluate(element => getComputedStyle(element).backgroundImage), /23, 63, 112/);
  });
  for (const width of [320, 390, 580]) {
    await page.setViewportSize({ width, height: 844 });
    await check(`${route}:width-${width}`, async () => {
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '页面内容超出视口');
    });
  }
  await page.setViewportSize({ width: 320, height: 844 });
  await page.evaluate(() => { document.documentElement.style.fontSize = '32px'; });
  await check(`${route}:large-text`, async () => {
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '放大文字后内容超出视口');
  });
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
}
await check('all:runtime', async () => assert.deepEqual(pageErrors, []));
await page.goto(`${baseURL}/records`);
await page.getByText('筛选记录', { exact: true }).click();
await page.getByLabel('资料来源', { exact: true }).selectOption({ label: '报告转录' });
await page.reload();
await check('records:filter-restored', async () => assert.equal(await page.getByLabel('资料来源', { exact: true }).inputValue(), '报告转录'));
await page.getByRole('button', { name: '清除全部筛选' }).click();
await page.getByText('筛选记录', { exact: true }).click();
await check('records:filter-cleared', async () => assert.equal(await page.getByLabel('资料来源', { exact: true }).inputValue(), '全部来源'));
await check('privacy:browser-storage', async () => {
  assert.deepEqual(await page.evaluate(async () => ({ local: localStorage.length, session: sessionStorage.length, databases: await indexedDB.databases() })), { local: 0, session: 0, databases: [] });
});
await page.goto(`${baseURL}/login`);
await page.getByRole('button', { name: '没有账号，去注册' }).click();
const refreshed = page.waitForResponse(response => response.url().endsWith('/api/session'));
await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
await refreshed;
await page.getByRole('heading', { name: '建立账号', exact: true }).waitFor();
await check('session:restore-keeps-form-state', async () => assert.equal(await page.getByRole('heading', { name: '建立账号', exact: true }).count(), 1));
await check('registration:empty-submit-disabled', async () => assert.equal(await page.getByRole('button', { name: '注册并登录', exact: true }).isDisabled(), true));
await browser.close();
const report = { startedAt, finishedAt: new Date().toISOString(), baseURL, evidenceType: 'STRUCTURE', visual: 'NOT_RUN', routes, results, pageErrors };
await mkdir('../.qa-work', { recursive: true });
await writeFile('../.qa-work/design-check.json', JSON.stringify(report, null, 2));
console.log(JSON.stringify({ checks: results.length, passed: results.filter(result => result.status === 'PASS').length, failures: results.filter(result => result.status === 'FAIL') }, null, 2));
process.exitCode = results.some(result => result.status === 'FAIL') ? 1 : 0;
