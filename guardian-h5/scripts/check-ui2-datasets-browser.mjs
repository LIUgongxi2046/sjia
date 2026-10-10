import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const origin = 'http://127.0.0.1:4173';
const checks = [], errors = [], requests = [], responses = [];
const sources = ['src/features/support/DatasetPage.tsx', 'src/features/support/DatasetEditor.tsx',
  'src/features/support/EvaluationRunPanel.tsx', 'src/features/support/SupportApp.tsx',
  'src/api/evaluationDatasets.ts', 'src/api/evaluationRuns.ts'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async file =>
  [file, createHash('sha256').update(await readFile(file)).digest('hex')])));
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
try {
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/')) requests.push({ path: new URL(request.url()).pathname, method: request.method() });
  });
  page.on('response', response => {
    if (new URL(response.url()).pathname.startsWith('/api/')) responses.push({ path: new URL(response.url()).pathname,
      status: response.status(), cache_control: response.headers()['cache-control'] });
  });
  for (const width of [320, 360, 390, 430, 580]) {
    await page.setViewportSize({ width, height: 844 });
    const denial = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
    await page.goto(`${origin}/support/datasets`);
    await denial;
    await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
    assert.equal(await page.locator('[data-screen-id="UI2-37"]').count(), 1);
    assert.equal(await page.getByRole('heading', { name: '资料与评价集合', level: 1, exact: true }).count(), 1);
    assert.equal(await page.getByRole('tab').count(), 3);
    assert.equal(await page.locator('.metric-grid strong').allTextContents().then(values => values.join(',')), '未统计,未统计,未统计,未统计');
    assert.equal(await page.getByRole('navigation', { name: '家庭主导航', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '选择获准资料', exact: true }).count(), 0);
    assert.equal(await page.locator('input, textarea, select').count(), 0);
    const dimensions = await page.locator('.phone-scroll').evaluate(node => ({ client: node.clientWidth, scroll: node.scrollWidth }));
    assert.ok(dimensions.scroll <= dimensions.client, JSON.stringify(dimensions));
    checks.push({ name: 'anonymous_scope_and_width', width, status: 'PASS' });
  }
  for (const name of ['独立标签', '评价结果', '用途清单']) {
    const tab = page.getByRole('tab', { name, exact: true });
    await tab.click();
    assert.equal(await tab.getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('[role="tab"][aria-selected="true"]').count(), 1);
  }
  checks.push({ name: 'tabs_without_private_content', status: 'PASS' });
  await page.getByRole('button', { name: '页面说明', exact: true }).click();
  await page.getByRole('dialog', { name: '页面说明', exact: true }).waitFor();
  assert.equal(await page.locator('.phone-scroll[inert]').count(), 1);
  await page.keyboard.press('Escape');
  assert.equal(await page.getByRole('dialog').count(), 0);
  assert.equal(await page.locator('.phone-scroll[inert]').count(), 0);
  checks.push({ name: 'keyboard_and_explanation', status: 'PASS' });
  await context.setOffline(true);
  await page.getByRole('button', { name: '核验访问权限', exact: true }).click();
  await page.getByRole('heading', { name: '网络连接已中断', exact: true }).waitFor();
  assert.equal(await page.locator('input, textarea, select').count(), 0);
  await context.setOffline(false);
  const restored = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
  await page.getByRole('button', { name: '重新读取', exact: true }).click();
  await restored;
  await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
  checks.push({ name: 'actual_offline_and_reauthorization', status: 'PASS' });
  const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
  await page.reload(); await refreshed;
  await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
  checks.push({ name: 'reload_preserves_access_boundary', status: 'PASS' });
  assert.deepEqual(errors, []);
  assert.ok(requests.every(request => request.method === 'GET' && request.path === '/api/v2/support/session'));
  assert.ok(responses.every(response => response.status === 401 && response.cache_control?.includes('no-store')));
  const report = { status: 'PASS', checks, source_hashes: hashes, requests, responses,
    data_writes: 0, visual: 'NOT_RUN', authorized_save_readback: 'NOT_RUN', evaluation_run: 'NOT_RUN' };
  await writeFile('../.planning-work/ui2-datasets-browser.json', JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ status: report.status, checks: checks.length, report: '.planning-work/ui2-datasets-browser.json', visual: report.visual }));
} finally { await browser.close(); }
