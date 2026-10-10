import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.planning-work/t40-simulation-progress.json', 'utf8'));
const origin = 'http://127.0.0.1:5183', checks = [], responses = [], requests = [], measurements = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.on('pageerror', error => { throw error; });
page.on('request', request => { const path = new URL(request.url()).pathname; if (path.startsWith('/api/')) requests.push({ path, method: request.method() }); });
function check(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(path, schema) {
  const response = await context.request.get(origin + path);
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  const value = await response.json(); if (schema) responses.push({ schema, value }); return value;
}
async function login(support) {
  const bootstrap = await api('/api/auth/bootstrap');
  const response = await context.request.post(origin + (support ? '/api/v2/support/session/login' : '/api/auth/login'), {
    data: { username: support ? 'simulation-support' : 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(response.ok());
}
try {
  await login(false);
  const group = await api('/api/uploads/' + fixture.group_id, 'UploadGroupView');
  const options = await api(`/api/v2/patients/${group.patient_id}/disposition-options`, 'DispositionOptions');
  const target = options.targets.find(item => item.target_id === fixture.copy_ids[2]); assert.ok(target);
  await page.goto(origin + '/account/data-use');
  await page.getByRole('button', { name: '申请处理我的资料', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '资料保存策略', exact: true })).toBeEnabled();
  await page.getByRole('combobox', { name: '资料保存策略', exact: true }).selectOption(target.policy_id);
  await page.getByRole('checkbox', { name: `${target.label} · 版本 ${target.target_version}`, exact: true }).check();
  await page.getByLabel('处置申请说明与依据', { exact: true }).fill('SIMULATION浏览器本人申请；使用当前版本副本验证继续保存与刷新');
  await page.getByRole('button', { name: '提交准确资料范围', exact: true }).click();
  await expect(page.getByText('申请已经保存，当前状态 pending。', { exact: true })).toBeVisible();
  const id = new URL(page.url()).searchParams.get('disposition_id'); assert.ok(id);
  const submitted = await api('/api/v2/dispositions/' + id, 'DispositionView');
  assert.deepEqual(submitted.targets, [{ target_type: target.target_type, target_id: target.target_id, target_version: target.target_version }]);
  check('家庭页面实际选择准确副本并保存资料处置申请');
  await page.reload(); await expect(page.getByText('申请已经保存，当前状态 pending。', { exact: true })).toBeVisible();
  check('家庭页面刷新恢复原申请回执与准确范围');
  await login(true);
  await page.goto(`${origin}/support/data-use?disposition_id=${id}`);
  await expect(page.getByLabel('处置批准证明', { exact: true })).toBeVisible();
  await page.getByLabel('处置批准证明', { exact: true }).fill('SIMULATION浏览器独立批准准确副本继续保存；实际保存结果逐项读取');
  await page.getByRole('button', { name: '批准准确处置范围', exact: true }).click();
  await expect(page.getByLabel('继续保存证明', { exact: true })).toBeVisible();
  check('受控页面实际批准准确资料范围');
  await page.getByLabel('继续保存证明', { exact: true }).fill('SIMULATION浏览器继续保存此项副本的证明');
  await page.getByLabel('重新核查时间', { exact: true }).fill('2026-10-11T08:30');
  await page.getByRole('button', { name: '记录此项继续保存', exact: true }).click();
  await expect(page.getByText('申请状态 completed；保存策略版本 1。', { exact: true })).toBeVisible();
  const processed = await api('/api/v2/support/dispositions/' + id, 'DispositionView');
  assert.equal(processed.items[0].status, 'retained'); assert.equal(processed.items[0].recheck_at, '2026-10-11T00:30:00+00:00');
  check('受控页面实际保存继续保存证明与重新核查时间');
  await page.reload(); await expect(page.getByText('申请状态 completed；保存策略版本 1。', { exact: true })).toBeVisible();
  check('受控页面刷新准确恢复处置结果与原请求');
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await page.locator('.phone-scroll').evaluate(scroll => {
      window.dispositionTextSizes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')]
        .map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      for (const item of window.dispositionTextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
    });
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth }));
    assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ width, geometry })); measurements.push({ width, geometry });
    await page.evaluate(() => { for (const item of window.dispositionTextSizes) item.node.style.fontSize = item.original; delete window.dispositionTextSizes; });
  }
  check('五种宽度及200%文字显示完整处置内容');
  await login(false); await page.goto(`${origin}/account/data-use?disposition_id=${id}`);
  await expect(page.getByText('申请已经保存，当前状态 completed。', { exact: true })).toBeVisible();
  check('家庭页面读取受控人员保存的最终逐项结果');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  await page.waitForLoadState('networkidle'); const count = requests.length; await page.waitForTimeout(1200); assert.equal(requests.length, count);
  check('处置证明没有浏览器存储且没有循环读取');
  await writeFile('../.planning-work/t40-disposition-browser.json', JSON.stringify({ mode: 'SIMULATION', task_id: 'T40', status: 'PASS',
    checked_at: new Date().toISOString(), disposition_id: id, checks, responses, requests, measurements,
    visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat_browser: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
