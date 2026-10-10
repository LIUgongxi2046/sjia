import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const sources = ['src/features/support/PublicationPage.tsx', 'src/features/support/PublicationEditor.tsx', 'src/features/support/SupportApp.tsx',
  'src/api/publication.ts', 'src/api/schema.ts', '../backend-design/publication.schema.json', 'scripts/check-ui2-publication-browser.mjs'];
const hashes = Object.fromEntries(await Promise.all(sources.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
const checks = [], errors = [], requests = [], responses = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
try {
  const page = await context.newPage(); page.setDefaultTimeout(10000); page.setDefaultNavigationTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push({ path: new URL(request.url()).pathname, method: request.method() }); });
  page.on('response', response => { if (new URL(response.url()).pathname.startsWith('/api/')) responses.push({ path: new URL(response.url()).pathname, status: response.status(), cache_control: response.headers()['cache-control'] }); });
  for (const width of [320, 360, 390, 430, 580]) {
    await page.setViewportSize({ width, height: 844 });
    const loaded = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
    await page.goto('http://127.0.0.1:4173/support/publication'); await loaded;
    await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
    assert.equal(await page.locator('[data-screen-id="UI2-36"]').count(), 1);
    assert.deepEqual(await page.locator('.pair strong').allTextContents(), ['尚未授权选择', ...Array(6).fill('待确认')]);
    assert.equal(await page.getByRole('textbox', { name: '家庭展示文字', exact: true }).isDisabled(), true);
    assert.equal(await page.getByRole('combobox', { name: '启用环境', exact: true }).isDisabled(), true);
    assert.deepEqual(await page.locator('select option').allTextContents(), ['演示', '受控试点', '获准家庭使用']);
    assert.equal(await page.getByRole('button', { name: '条件齐备后启用', exact: true }).isDisabled(), true);
    const dimensions = await page.locator('.phone-scroll').evaluate(node => ({ client: node.clientWidth, scroll: node.scrollWidth }));
    assert.ok(dimensions.scroll <= dimensions.client, JSON.stringify(dimensions));
    checks.push({ name: 'actual_anonymous_publication_and_width', width, status: 'PASS' });
  }
  await page.getByRole('button', { name: '页面说明', exact: true }).click(); await page.getByRole('dialog', { name: '页面说明', exact: true }).waitFor();
  assert.equal(await page.locator('.phone-scroll[inert]').count(), 1); await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '页面说明', exact: true }).waitFor({ state: 'detached' });
  assert.equal(await page.locator('.phone-scroll[inert]').count(), 0); checks.push({ name: 'keyboard_and_explanation', status: 'PASS' });
  await context.setOffline(true); await page.getByRole('button', { name: '核验访问权限', exact: true }).click();
  await page.getByRole('heading', { name: '网络连接已中断', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '条件齐备后启用', exact: true }).isDisabled(), true);
  await context.setOffline(false);
  const restored = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
  await page.getByRole('button', { name: '重新读取', exact: true }).click(); await restored;
  await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
  checks.push({ name: 'actual_offline_and_restore', status: 'PASS' });
  const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v2/support/session' && response.status() === 401);
  await page.reload(); await refreshed; await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
  checks.push({ name: 'reload_keeps_private_content_unavailable', status: 'PASS' });
  assert.deepEqual(errors, []); assert.ok(requests.every(item => item.method === 'GET' && item.path === '/api/v2/support/session'));
  assert.ok(responses.every(item => item.status === 401 && item.cache_control?.includes('no-store')));
  const report = { checked_at: new Date().toISOString(), status: 'PASS', checks, requests, responses, source_sha256: hashes,
    business_writes: 0, positive_publication: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/ui2-publication-browser.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, checks: checks.length, report: '.planning-work/ui2-publication-browser.json', visual: report.visual }) + '\n');
} finally { await browser.close(); }
