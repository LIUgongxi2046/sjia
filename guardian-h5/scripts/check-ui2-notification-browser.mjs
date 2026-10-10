import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const sources = ['src/features/support/NotificationPage.tsx', 'src/features/support/SupportApp.tsx',
  'src/features/alerts/UI2AlertsPage.tsx', 'src/features/alerts/useNotificationDelivery.ts',
  'src/api/notifications.ts', 'src/api/schema.ts', '../backend-design/notification.schema.json', 'scripts/check-ui2-notification-browser.mjs'];
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
    await page.goto('http://127.0.0.1:4173/support/delivery'); await loaded;
    await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
    assert.equal(await page.locator('[data-screen-id="UI2-39"]').count(), 1);
    assert.deepEqual(await page.locator('.pair strong').allTextContents(), ['尚未授权选择', '尚无记录', '查询原请求后处理', '停止新的发送']);
    assert.deepEqual(await page.locator('.process-list strong').allTextContents(), ['待发送', '已受理', '已送达', '已查看']);
    assert.equal(await page.getByRole('button', { name: '取得许可并发送当前通知', exact: true }).count(), 0);
    const dimensions = await page.locator('.phone-scroll').evaluate(node => ({ client: node.clientWidth, scroll: node.scrollWidth }));
    assert.ok(dimensions.scroll <= dimensions.client, JSON.stringify(dimensions));
    checks.push({ name: 'actual_anonymous_notification_and_width', width, status: 'PASS' });
  }
  await page.getByRole('button', { name: '页面说明', exact: true }).click(); await page.getByRole('dialog', { name: '页面说明', exact: true }).waitFor();
  assert.equal(await page.locator('.phone-scroll[inert]').count(), 1); await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '页面说明', exact: true }).waitFor({ state: 'detached' });
  checks.push({ name: 'keyboard_and_explanation', status: 'PASS' });
  await context.setOffline(true); await page.getByRole('button', { name: '核验访问权限', exact: true }).click();
  await page.getByRole('heading', { name: '网络连接已中断', exact: true }).waitFor();
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
    business_writes: 0, positive_notification: 'NOT_RUN', visual: 'NOT_RUN' };
  await writeFile('../.planning-work/ui2-notification-browser.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, checks: checks.length, report: '.planning-work/ui2-notification-browser.json', visual: report.visual }) + '\n');
} finally { await browser.close(); }
