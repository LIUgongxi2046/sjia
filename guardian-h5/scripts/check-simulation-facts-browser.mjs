import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.planning-work/t41-current-progress.json', 'utf8'));
const origin = 'http://127.0.0.1:5183', checks = [], requests = [], responses = [], measurements = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.on('pageerror', error => { throw error; });
page.on('request', request => { const url = new URL(request.url()); if (url.pathname.startsWith('/api/')) requests.push({ path: url.pathname, method: request.method(), occurred_at: new Date().toISOString() }); });
function check(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(method, path, data, csrf, schema) {
  const response = await context.request.fetch(origin + path, { method, ...(data ? { data } : {}), ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  assert.ok(response.headers()['cache-control'].includes('no-store'));
  const value = await response.json(); if (schema) responses.push({ schema, value }); return value;
}
try {
  const bootstrap = await api('GET', '/api/auth/bootstrap');
  const session = await api('POST', '/api/auth/login', { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
  const source = await api('GET', `/api/v2/archive-pages/${fixture.browser_page_id}/original`, null, null, 'PageOriginalView');
  const candidate = await api('GET', `/api/v2/archive-pages/${fixture.browser_page_id}/candidates`, null, null, 'FactCandidateSet');
  assert.ok(['awaiting_confirmation', 'confirmed'].includes(source.page.status));
  const route = `${origin}/archives/confirm?page=${fixture.browser_page_id}&batch=${fixture.browser_batch_id}`;
  await page.goto(route);
  await expect(page.getByRole('heading', { name: '逐项确认', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '确认并保存资料', exact: true })).toBeDisabled();
  const target = candidate.items.findIndex(item => item.field.value_type === 'text' && item.field.raw_text);
  assert.ok(target >= 0);
  const excluded = candidate.items.findIndex((item, index) => index !== target);
  const unknown = candidate.items.findIndex((item, index) => index !== target && index !== excluded && item.field.value_type === 'text');
  assert.ok(excluded >= 0 && unknown >= 0);
  let original = { operation_id: 'archive_candidates_confirm', target_key: `/archive-pages/${fixture.browser_page_id}/confirm`,
    request_id: randomUUID(), schema_version: '2.0.0', payload: { candidate_set_id: candidate.candidate_set_id,
      expected_page_version: source.page.version, items: candidate.items.map((item, index) => ({ candidate_id: item.candidate_id,
        decision: index === target ? 'modified' : index === excluded ? 'excluded' : index === unknown ? 'unknown' : 'accepted',
        field: index === excluded ? null : index === target ? { ...item.field, raw_text: item.field.raw_text + '；SIMULATION本人逐字确认' }
          : index === unknown ? { ...item.field, value_state: 'unknown', raw_numeric: null, categorical_value: null } : item.field,
        context: index === excluded ? null : item.context,
        reason: index === target ? 'SIMULATION浏览器修订依据' : index === excluded ? 'SIMULATION本人确认排除当前候选' : null })) } };
  let prepared;
  if (source.page.status === 'confirmed') {
    const prior = await api('GET', '/api/v2/requests/' + fixture.browser_intent_id, null, null, 'RequestView');
    assert.equal(prior.state, 'committed');
    original = { ...original, request_id: prior.request_id, payload: prior.fact_input };
    prepared = { intent_id: prior.intent_id };
    check('已经提交的原请求保留准确本人决定与完整正文');
  } else {
    await expect(page.getByLabel('原始项目名称', { exact: true })).toHaveCount(candidate.items.length);
    check('UI2-27逐项读取真实候选并要求本人决定');
    prepared = await api('POST', '/api/v2/write-intents', original, session.csrf_token);
  }
  const query = new URLSearchParams({ page: fixture.browser_page_id, batch: fixture.browser_batch_id,
    client_request: original.request_id, operation: original.operation_id, target: original.target_key });
  await page.goto(`${origin}/archives/confirm?${query}`);
  if (source.page.status === 'awaiting_confirmation') {
  await expect(page.getByRole('button', { name: '继续原请求提交', exact: true })).toBeVisible();
  await expect(page.getByLabel('原始数值', { exact: true }).nth(target)).toHaveValue(original.payload.items[target].field.raw_text);
  await expect(page.getByLabel('原始数值', { exact: true }).nth(target)).toBeDisabled();
  check('恢复原请求准确显示修订正文并冻结表单');
  await page.reload();
  await expect(page.getByRole('button', { name: '继续原请求提交', exact: true })).toBeVisible();
  await expect(page.getByLabel('原始数值', { exact: true }).nth(target)).toHaveValue(original.payload.items[target].field.raw_text);
  check('刷新准确恢复持久事实请求');
  await page.getByRole('button', { name: '继续原请求提交', exact: true }).click();
  }
  await expect(page.getByRole('button', { name: '更正此项事实', exact: true })).toHaveCount(candidate.items.length - 1);
  const saved = await api('GET', '/api/v2/requests/' + prepared.intent_id, null, null, 'RequestView');
  assert.equal(saved.state, 'committed'); assert.deepEqual(saved.fact_input, original.payload);
  check('恢复页面读取已经提交的完整正文与同一回执');
  await page.reload();
  await expect(page.getByRole('button', { name: '更正此项事实', exact: true })).toHaveCount(candidate.items.length - 1);
  const facts = await api('GET', `/api/v2/archive-pages/${fixture.browser_page_id}/facts`, null, null, 'FactList');
  const modified = facts.items.find(item => item.field.field.original_name === original.payload.items[target].field.original_name);
  assert.ok(modified && modified.field.verification_status === 'unverified');
  assert.ok(facts.items.some(item => item.field.field.value_state === 'unknown'));
  assert.equal(facts.items.length, candidate.items.length - 1);
  check('保存后刷新恢复准确原值与未核对状态');
  if (modified.version === '1') {
  await page.locator('section.card.form-card').filter({ has: page.getByRole('heading', { name: modified.field.field.original_name, exact: true }) }).getByRole('button', { name: '更正此项事实', exact: true }).click();
  await expect(page.getByRole('heading', { name: '更正事实内容', exact: true })).toBeVisible();
  const sheet = page.locator('.sheet');
  await page.keyboard.press('Tab');
  assert.ok(await sheet.evaluate(element => element.contains(element.getRootNode().activeElement)));
  await page.keyboard.press('Shift+Tab');
  assert.ok(await sheet.evaluate(element => element.contains(element.getRootNode().activeElement)));
  check('更正弹层键盘焦点保持在当前弹层');
  await sheet.getByLabel('原始数值', { exact: true }).fill('SIMULATION浏览器更正原文');
  await sheet.getByLabel('更正原因', { exact: true }).fill('SIMULATION本人确认更正原因');
  await sheet.getByRole('button', { name: '保存更正', exact: true }).click();
  await expect(page.getByRole('heading', { name: '更正事实内容', exact: true })).toHaveCount(0);
  }
  await expect(page.getByText('SIMULATION浏览器更正原文', { exact: false }).first()).toBeVisible();
  const current = await api('GET', `/api/v2/archive-pages/${fixture.browser_page_id}/facts`, null, null, 'FactList');
  assert.ok(current.items.some(item => item.version === '2' && item.field.field.raw_text === 'SIMULATION浏览器更正原文'));
  check('更正事实保持新版本并恢复保存结果');
  await page.getByRole('link', { name: '对照原始文书', exact: false }).click();
  await expect(page.getByRole('heading', { name: '原始资料', exact: true })).toBeVisible();
  await expect(page.getByText('文书形成时间', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '查看原件', exact: true })).toBeVisible();
  check('事实连接准确原页及原件元数据');
  await page.getByRole('link', { name: '逐项内容与时间', exact: true }).click();
  await expect(page.getByRole('heading', { name: '逐项确认', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '更正此项事实', exact: true })).toHaveCount(candidate.items.length - 1);
  await expect(page.getByRole('heading', { name: '正在读取当前资料', exact: true })).toHaveCount(0);
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    const scale = await page.locator('.phone-scroll').evaluate(scroll => {
      window.factTextSizes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')]
        .map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      const before = Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize);
      for (const item of window.factTextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
      return { before, after: Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize) };
    });
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth }));
    assert.equal(scale.after, scale.before * 2); assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ width, geometry }));
    measurements.push({ width, scale, geometry });
    await page.evaluate(() => { for (const item of window.factTextSizes) item.node.style.fontSize = item.original; delete window.factTextSizes; });
  }
  check('五种手机宽度及实际200%文字检查通过');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  await page.waitForLoadState('networkidle');
  const count = requests.length; await page.waitForTimeout(1200);
  await writeFile('../.planning-work/t41-browser-requests.json', JSON.stringify({ checks, requests, responses, measurements, count, after: requests.length }, null, 2));
  assert.equal(requests.length, count);
  check('私有正文不进入浏览器存储且没有循环查询');
  await writeFile('../.planning-work/t41-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T41',
    checked_at: new Date().toISOString(), checks, requests, responses, measurements, visual_comparison: 'NOT_RUN',
    actual_devices: 'NOT_RUN', wechat_browser: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
