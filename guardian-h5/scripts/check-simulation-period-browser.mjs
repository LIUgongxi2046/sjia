import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t47-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const origin = 'http://127.0.0.1:5183', checks = [], requests = [], runId = randomUUID();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(30000);
page.on('pageerror', error => { throw error; });
page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push({ path: new URL(request.url()).pathname, method: request.method() }); });
function checked(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + '/api' + path, { method, ...(data ? { data } : {}), ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`); return response.json();
}
function date(value) { return { precision: 'date', raw_text: value, local_date: value, local_datetime: null, timezone_name: 'Asia/Shanghai',
  utc_offset_minutes: null, instant: null, resolution_status: 'local_only', local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null }; }

try {
  const bootstrap = await api('GET', '/auth/bootstrap');
  const session = await api('POST', '/auth/login', { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
  await page.goto(origin + '/records/period');
  await expect(page.getByRole('heading', { name: '观察时段', exact: true })).toBeVisible();
  const start = page.getByLabel('开始日期', { exact: true }), end = page.getByLabel('结束日期', { exact: true });
  await expect(start).toBeEnabled();
  await expect(page.getByText('标签仅代表本人记录，不判定病情变化。', { exact: true })).toBeVisible();
  await expect(page.getByText('最近医学时间', { exact: true })).toBeVisible();
  await expect(page.getByText('实际观察间隔', { exact: true })).toBeVisible();
  await expect(page.getByText('是否可以比较', { exact: true })).toBeVisible();
  checked('UI2-31保留日期范围、本人标签及三项比较信息');
  await start.fill('2026-10-06'); await end.fill('2026-10-06');
  await page.getByLabel('本人时段标签', { exact: false }).fill('SIMULATION浏览器同日采样范围');
  await page.getByRole('button', { name: '使用此观察范围', exact: true }).click();
  await expect(page.getByRole('heading', { name: '观察范围已保存', exact: true })).toBeVisible();
  const firstUrl = page.url(), requestId = new URL(firstUrl).searchParams.get('request');
  assert.ok(requestId && !firstUrl.includes('SIMULATION') && !firstUrl.includes('2026-10-06'));
  checked('实际提交意图、保存回执与版本重新读取完成');
  await page.reload();
  await expect(page.getByRole('heading', { name: '观察范围已保存', exact: true })).toBeVisible();
  await expect(start).toHaveValue('2026-10-06');
  await expect(page.getByText('实际采样间隔：3 小时', { exact: true }).first()).toBeVisible();
  checked('刷新恢复原请求、日期范围与实际间隔');
  await page.getByRole('button', { name: '读取当前范围并继续设置', exact: true }).click();
  await expect(start).toBeEnabled();
  assert.equal(new URL(page.url()).search, '');
  checked('读取当前范围恢复可编辑状态并保留保存版本');
  const current = await api('GET', `/v2/patients/${fixture.patient_id}/observation-period`);
  const pending = { operation_id: 'observation_period_change', target_key: `/patients/${fixture.patient_id}/observation-period/versions`,
    request_id: randomUUID(), schema_version: '2.0.0', payload: { expected_version: current.version, start: date('2026-10-01'), end: date('2026-10-04'),
      label: 'SIMULATION浏览器待提交范围', episode_id: null, reason: 'SIMULATION浏览器原请求恢复' } };
  const intent = await api('POST', '/v2/write-intents', pending, session.csrf_token);
  await page.goto(`${origin}/records/period?client_request=${pending.request_id}`);
  await expect(start).toHaveValue('2026-10-01'); await expect(end).toHaveValue('2026-10-04');
  await expect(page.getByRole('button', { name: '使用此观察范围', exact: true })).toBeEnabled();
  await expect(start).toBeDisabled();
  checked('准备响应缺失时恢复准确原正文和冻结表单');
  await page.getByRole('button', { name: '使用此观察范围', exact: true }).click();
  await expect(page.getByRole('heading', { name: '观察范围已保存', exact: true })).toBeVisible();
  await expect(page.getByText('实际采样间隔：72 小时', { exact: true })).toBeVisible();
  assert.equal(new URL(page.url()).searchParams.get('request'), intent.intent_id);
  checked('恢复后的原请求准确保存稀疏采样范围');
  const saved = await api('GET', '/v2/requests/' + intent.intent_id);
  await page.goto(`${origin}/records/period?period_version=${saved.receipt.resource_id}`);
  await expect(start).toHaveValue('2026-10-01');
  await expect(start).toBeEnabled();
  checked('准确版本链接能够重新读取实际保存范围');
  await start.fill('2026-10-06'); await end.fill('2026-10-06');
  await page.getByRole('button', { name: '使用此观察范围', exact: true }).click();
  await expect(page.getByRole('heading', { name: '观察范围已保存', exact: true })).toBeVisible();
  const sampling = page.locator('details').filter({ has: page.locator('a').filter({ hasText: '查看原文依据' }) }).first();
  await sampling.locator('summary').click();
  const sourceHref = await sampling.getByRole('link', { name: '查看原文依据', exact: false }).getAttribute('href');
  assert.ok(new URL(sourceHref, origin).searchParams.get('document_version'));
  await sampling.getByRole('link', { name: '查看原文依据', exact: false }).click();
  await expect(page.getByRole('heading', { name: '原始资料', exact: true })).toBeVisible();
  checked('采样资料连接准确文书版本与原件入口');
  await page.goBack();
  await expect(page.getByRole('heading', { name: '观察范围已保存', exact: true })).toBeVisible();
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  }
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  checked('五种宽度及文字放大通过实际文档几何检查');
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
  await page.getByRole('button', { name: '读取当前范围并继续设置', exact: true }).click();
  await expect(start).toBeEnabled(); await start.focus();
  for (let count = 0; count < 8 && !await end.evaluate(element => element === element.getRootNode().activeElement); count++) await page.keyboard.press('Tab');
  await expect(end).toBeFocused();
  checked('日期范围可通过键盘顺序操作');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  const requestCount = requests.length; await page.waitForTimeout(1200);
  assert.equal(requests.length, requestCount);
  checked('敏感正文不写入浏览器存储且没有循环查询');
  await writeFile('../.planning-work/t47-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T47',
    run_id: runId, checked_at: new Date().toISOString(), checks, requests, visual_comparison: 'NOT_RUN',
    actual_devices: 'NOT_RUN', wechat_browser: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
