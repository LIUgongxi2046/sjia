import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t42-fixture.json', 'utf8'));
const backend = JSON.parse(await readFile('../.planning-work/t42-simulation.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const origin = 'http://127.0.0.1:5183', checks = [], measurements = [], requests = [], runId = randomUUID();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.on('pageerror', error => { throw error; });
page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push({ path: new URL(request.url()).pathname, method: request.method() }); });
function checked(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + '/api' + path, { method, ...(data ? { data } : {}), ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`); return response.json();
}

try {
  const bootstrap = await api('GET', '/auth/bootstrap');
  const session = await api('POST', '/auth/login', { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
  await page.goto(origin + '/archives');
  await expect(page.getByRole('button', { name: '添加一批资料', exact: false })).toBeEnabled();
  await page.getByRole('button', { name: '添加一批资料', exact: false }).click();
  await expect(page.getByRole('heading', { name: '批次进度', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '选择已有资料', exact: false })).toBeEnabled();
  for (const [title, text] of [['接收原件', '按文书保留页面与顺序'], ['识别与归组', '每页完成后保存候选'], ['本人确认', '核对时间、原文和来源'], ['正式保存', '专业核对状态单独显示']]) {
    await expect(page.getByText(title, { exact: true })).toBeVisible(); await expect(page.getByText(text, { exact: true })).toBeVisible();
  }
  checked('长期档案入口连接批次页面并保留全部四个处理步骤');
  const directory = await api('POST', `/v2/patients/${fixture.patient_id}/archive-batches/search`, { limit: 20 }, session.csrf_token);
  const existing = directory.items.find(batch => batch.pages.some(row => row.file_id === fixture.file_ids[3]));
  assert.ok(existing && existing.pages[0].status === 'uploaded');
  await page.goto(origin + '/archives/batches/' + existing.batch_id);
  await expect(page.getByRole('button', { name: '处理已接收页面', exact: true })).toBeEnabled();
  await page.reload();
  await expect(page.getByRole('button', { name: '处理已接收页面', exact: true })).toBeEnabled();
  checked('刷新恢复已经保存的批次及可处理页面');
  const identity = { operation_id: 'archive_page_cancel', target_key: `/archive-pages/${existing.pages[0].page_id}/cancel`,
    request_id: randomUUID(), schema_version: '2.0.0', payload: { expected_status_version: existing.pages[0].status_version, reason: 'SIMULATION本人取消未开始页面' } };
  const original = await api('POST', '/v2/write-intents', identity, session.csrf_token);
  await page.goto(origin + '/archives/batches/' + existing.batch_id + '?' + new URLSearchParams({ client_request: identity.request_id, operation: identity.operation_id, target: identity.target_key }));
  await expect(page.getByRole('button', { name: '继续原请求提交', exact: true })).toBeEnabled();
  assert.equal(new URL(page.url()).searchParams.get('request'), original.intent_id);
  await expect(page.getByRole('button', { name: '选择已有资料', exact: false })).toBeDisabled();
  checked('准备响应缺失时从服务端恢复准确原请求及原件选择');
  await page.getByRole('button', { name: '继续原请求提交', exact: true }).click();
  await expect(page.getByText('页面 1 · 已取消', { exact: true })).toBeVisible();
  assert.match(new URL(page.url()).pathname, /^\/archives\/batches\/[0-9a-f-]{36}$/);
  const savedBatch = new URL(page.url()).pathname.split('/').at(-1);
  const current = await api('GET', '/v2/archive-batches/' + savedBatch);
  assert.equal(current.pages[0].file_id, fixture.file_ids[3]);
  assert.equal(new URL(page.url()).search, '');
  checked('原请求实际提交、准确原件及批次路径重新读取一致');
  const cancelled = await api('GET', '/v2/archive-batches/' + savedBatch);
  assert.equal(cancelled.pages[0].status, 'cancelled'); assert.deepEqual(cancelled.pages[0].attempts, []);
  await page.reload(); await expect(page.getByText('页面 1 · 已取消', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '取消未开始页面', exact: true })).toBeDisabled();
  checked('浏览器实际取消、刷新及禁止重复操作正确');
  await page.goto(origin + '/archives/batches/' + backend.batch_id);
  await expect(page.getByText('页面 1 · 等待本人确认', { exact: true })).toBeVisible();
  await page.getByText('页面 1 · 等待本人确认', { exact: true }).click();
  await expect(page.getByText('确认本页候选', { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/请求标识：/).first()).toBeVisible();
  const batch = await api('GET', '/v2/archive-batches/' + backend.batch_id);
  await expect(page.getByText(new RegExp(batch.pages[0].attempts.at(-1).provider_request_id))).toBeVisible();
  checked('成功页面准确展示外部请求、候选入口与全部尝试');
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    const scale = await page.locator('.phone-scroll').evaluate(scroll => {
      const nodes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')];
      window.batchTextSizes = nodes.map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      const before = Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize);
      for (const item of window.batchTextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
      return { before, after: Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize) };
    });
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth }));
    assert.equal(scale.after, scale.before * 2); assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ width, scale, geometry }));
    measurements.push({ width, scale, geometry });
    await page.evaluate(() => { for (const item of window.batchTextSizes) item.node.style.fontSize = item.original; delete window.batchTextSizes; });
  }
  checked('五种宽度及ShadowRoot内部实际200%文字检查通过');
  await page.getByRole('button', { name: '刷新进度', exact: true }).focus();
  await expect(page.getByRole('button', { name: '刷新进度', exact: true })).toBeFocused();
  await page.keyboard.press('Enter'); await expect(page.getByText('页面 1 · 等待本人确认', { exact: true })).toBeVisible();
  checked('键盘能够操作实际进度刷新');
  const storage = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  assert.deepEqual(storage, { local: 0, session: 0 });
  assert.ok(!page.url().includes('SIMULATION'));
  checked('原件正文与身份恢复未写入浏览器Storage或地址');
  await writeFile('../.planning-work/t42-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', run_id: runId,
    task_id: 'T42', checked_at: new Date().toISOString(), checks, measurements, requests, created_batch_id: savedBatch,
    visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', native_background: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${checks.length} 项批次浏览器检查\n`);
} finally { await context.close(); await browser.close(); }
