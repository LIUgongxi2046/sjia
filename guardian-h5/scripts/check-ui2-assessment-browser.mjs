import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const sources = ['src/features/support/AssessmentPage.tsx', 'src/features/support/SupportApp.tsx',
  'src/features/support/useAssessmentCreation.tsx', 'src/features/support/AssessmentSelectionFields.tsx',
  'src/features/support/DynamicSelectionFields.tsx', 'src/features/support/DynamicSources.tsx', 'src/features/support/DynamicTriggerRecords.tsx', 'src/api/dynamic.ts',
  'src/api/assessments.ts', 'src/api/schema.ts', '../frontend-design/v2-types.ts',
  '../backend-design/v2-protocol.schema.json', '../alert-design/node.schema.json', 'scripts/check-ui2-assessment-browser.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path =>
  [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const checks = [], errors = [], requests = [], responses = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
try {
  const page = await context.newPage();
  page.setDefaultTimeout(10000); page.setDefaultNavigationTimeout(10000);
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
    await page.goto('http://127.0.0.1:4173/support/assessment'); await denial;
    await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
    assert.equal(await page.locator('[data-screen-id="UI2-35"]').count(), 1);
    assert.equal(await page.getByRole('heading', { name: '评估依据', level: 1, exact: true }).count(), 1);
    assert.equal(await page.getByRole('tab').count(), 3);
    assert.deepEqual(await page.locator('.pair strong').allTextContents(),
      ['尚未授权选择', '尚无有效评估', '尚无有效评估', '尚无有效评估', '尚无有效评估']);
    assert.equal(await page.getByRole('navigation', { name: '家庭主导航', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '观察时段与来源', exact: true }).isDisabled(), true);
    assert.equal(await page.locator('input, textarea, select').count(), 0);
    const dimensions = await page.locator('.phone-scroll').evaluate(node => ({ client: node.clientWidth, scroll: node.scrollWidth }));
    assert.ok(dimensions.scroll <= dimensions.client, JSON.stringify(dimensions));
    checks.push({ name: 'anonymous_scope_and_width', width, status: 'PASS' });
  }
  for (const name of ['MS 评分', '动态变化', '静态分类']) {
    const tab = page.getByRole('tab', { name, exact: true }); await tab.click();
    assert.equal(await tab.getAttribute('aria-selected'), 'true');
    assert.equal(await page.locator('[role="tab"][aria-selected="true"]').count(), 1);
    assert.deepEqual(await page.locator('.pair strong').allTextContents(),
      ['尚未授权选择', '尚无有效评估', '尚无有效评估', '尚无有效评估', '尚无有效评估']);
  }
  checks.push({ name: 'independent_tabs_without_private_results', status: 'PASS' });
  await page.getByRole('button', { name: '页面说明', exact: true }).click();
  await page.getByRole('dialog', { name: '页面说明', exact: true }).waitFor();
  assert.equal(await page.locator('.phone-scroll[inert]').count(), 1);
  assert.equal(await page.getByRole('button', { name: '关闭说明', exact: true }).evaluate(node => node.getRootNode().activeElement === node), true);
  await page.keyboard.press('Escape'); await page.getByRole('dialog', { name: '页面说明', exact: true }).waitFor({ state: 'detached' });
  assert.equal(await page.getByRole('dialog').count(), 0);
  assert.equal(await page.locator('.phone-scroll[inert]').count(), 0);
  checks.push({ name: 'keyboard_and_explanation', status: 'PASS' });
  await context.setOffline(true); await page.getByRole('button', { name: '核验访问权限', exact: true }).click();
  await page.getByRole('heading', { name: '网络连接已中断', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '观察时段与来源', exact: true }).isDisabled(), true);
  await context.setOffline(false);
  const restored = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
  await page.getByRole('button', { name: '重新读取', exact: true }).click(); await restored;
  await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
  checks.push({ name: 'actual_offline_and_reauthorization', status: 'PASS' });
  const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
  await page.reload(); await refreshed;
  await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
  checks.push({ name: 'reload_preserves_access_boundary', status: 'PASS' });
  assert.deepEqual(errors, []);
  assert.ok(requests.every(request => request.method === 'GET' && request.path === '/api/v2/support/session'));
  assert.ok(responses.every(response => response.status === 401 && response.cache_control?.includes('no-store')));
  const report = { checked_at: new Date().toISOString(), status: 'PASS', checks, source_sha256: hashes, requests, responses,
    business_writes: 0, positive_assessment: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/ui2-assessment-browser.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, checks: checks.length,
    report: '.planning-work/ui2-assessment-browser.json', visual: report.visual }) + '\n');
} finally { await browser.close(); }
