import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.planning-work/t41-current-progress.json', 'utf8'));
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
  assert.ok(response.headers()['cache-control'].includes('no-store'));
  const value = await response.json(); if (schema) responses.push({ schema, value }); return value;
}
try {
  const bootstrap = await api('/api/auth/bootstrap');
  const response = await context.request.post(origin + '/api/auth/login', { data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(response.ok());
  const candidate = await api(`/api/v2/archive-pages/${fixture.browser_page_id}/candidates`, 'FactCandidateSet');
  let documentId;
  const resuming = process.argv.includes('--resume');
  if (resuming) {
    const prior = JSON.parse(await readFile('../.planning-work/t43-browser-progress.json', 'utf8'));
    const original = await api('/api/v2/requests/' + prior.intent_id, 'RequestView');
    assert.equal(original.state, 'committed'); documentId = prior.document_id;
    assert.equal(original.receipt.resource_id, documentId);
    const query = new URLSearchParams({ page: prior.page_id, batch: prior.batch_id,
      request: prior.intent_id, client_request: original.request_id });
    await page.goto(`${origin}/archives/grouping?${query}`);
    await expect(page.getByRole('heading', { name: '文书归组已经保存', exact: true })).toBeVisible();
    check('已经保存的文书创建请求恢复准确正文与回执');
  } else {
  await page.goto(`${origin}/archives/grouping?page=${fixture.browser_page_id}&batch=${fixture.browser_batch_id}`);
  await expect(page.getByRole('heading', { name: '文书与就诊归组', exact: true })).toBeVisible();
  await expect(page.getByRole('combobox', { name: '文书类型', exact: true })).toBeEnabled();
  await page.getByRole('combobox', { name: '文书类型', exact: true }).selectOption('discharge');
  await page.getByLabel('来源医院', { exact: true }).fill('SIMULATION本人确认医院');
  await page.getByRole('combobox', { name: '就诊类型', exact: true }).first().selectOption('inpatient');
  await page.getByLabel('开始日期', { exact: true }).first().fill('2026-09-01');
  await page.getByLabel('结束日期', { exact: true }).first().fill('2026-09-03');
  await page.getByText('原文、页序与全部就诊依据', { exact: true }).click();
  await page.getByLabel('本人确认原文', { exact: true }).fill(candidate.extraction.page_text);
  await page.getByLabel('文书形成时间', { exact: true }).fill('2026-09-04');
  await page.getByLabel('就诊 1说明', { exact: true }).fill('SIMULATION本人确认住院说明');
  await page.getByRole('button', { name: '添加另一次就诊说明', exact: true }).click();
  await page.getByRole('combobox', { name: '就诊类型', exact: true }).nth(1).selectOption('outpatient');
  await page.getByLabel('开始日期', { exact: true }).nth(1).fill('2026-09-04');
  await page.getByLabel('就诊 2说明', { exact: true }).fill('SIMULATION本人确认另一次门诊说明');
  await page.getByLabel('归属说明', { exact: true }).fill('SIMULATION两次就诊分别保留');
  check('UI2-28实际填写分类、原文、独立日期及两次就诊说明');
  await page.getByRole('button', { name: '确认归组', exact: true }).click();
  await expect(page.getByRole('heading', { name: '文书归组已经保存', exact: true })).toBeVisible();
  const requestId = new URL(page.url()).searchParams.get('request');
  assert.ok(requestId);
  const saved = await api('/api/v2/requests/' + requestId, 'RequestView');
  assert.equal(saved.state, 'committed');
  assert.equal(saved.document_input.document_type, 'discharge');
  assert.equal(saved.document_input.proposed_metadata.episodes.length, 2);
  assert.equal(saved.document_input.documented_time.local_date, '2026-09-04');
  documentId = saved.receipt.resource_id;
  const document = await api('/api/v2/archive-documents/' + documentId, 'DocumentView');
  assert.equal(document.confirmed_text, candidate.extraction.page_text);
  assert.deepEqual(document.proposed_metadata, saved.document_input.proposed_metadata);
  assert.equal(document.grouping_status, 'pending');
  assert.deepEqual(document.episode_ids, []);
  await writeFile('../.planning-work/t43-browser-progress.json', JSON.stringify({ document_id: documentId, intent_id: requestId, page_id: fixture.browser_page_id, batch_id: fixture.browser_batch_id }, null, 2));
  check('页面创建实际保存准确文书及全部本人说明');
  }
  await page.reload();
  await expect(page.getByRole('heading', { name: '文书归组已经保存', exact: true })).toBeVisible();
  await page.getByText('原文、页序与全部就诊依据', { exact: true }).click();
  await expect(page.getByRole('textbox', { name: '本人确认原文', exact: true })).toHaveValue(candidate.extraction.page_text);
  await expect(page.getByRole('textbox', { name: '本人确认原文', exact: true })).toBeDisabled();
  check('刷新恢复已经保存的原请求正文及回执');
  await page.getByRole('link', { name: '读取文书与全部就诊说明', exact: true }).click();
  await expect(page.getByRole('combobox', { name: '文书类型', exact: true })).toBeEnabled();
  await expect(page.getByLabel('来源医院', { exact: true })).toHaveValue('SIMULATION本人确认医院');
  await page.getByText('原文、页序与全部就诊依据', { exact: true }).click();
  await page.getByLabel('更正原因', { exact: true }).fill('SIMULATION本人更正文书归组说明');
  await page.getByRole('textbox', { name: '归属说明', exact: true }).fill('SIMULATION两次就诊保留且完成本人修订');
  const options = await api(`/api/v2/archive-batches/${fixture.browser_batch_id}/document-inputs`, 'DocumentInputsView');
  assert.equal(options.episodes.length, 1);
  await page.getByRole('checkbox', { name: options.episodes[0].label, exact: true }).check();
  await page.getByRole('button', { name: '确认归组', exact: true }).click();
  await expect(page.getByRole('heading', { name: '文书归组已经保存', exact: true })).toBeVisible();
  const updated = await api('/api/v2/archive-documents/' + documentId, 'DocumentView');
  assert.equal(updated.version, '3'); assert.equal(updated.proposed_metadata.episodes.length, 2);
  assert.deepEqual(updated.episode_ids, [options.episodes[0].episode_id]);
  assert.equal(updated.proposed_metadata.grouping_note, 'SIMULATION两次就诊保留且完成本人修订');
  check('已有文书归组页面实际更正并读取新版本');
  check('浏览器选择实际就诊来源并读取保存引用');
  await page.reload();
  await expect(page.getByRole('heading', { name: '文书归组已经保存', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '归属说明', exact: true })).toHaveValue(updated.proposed_metadata.grouping_note);
  await expect(page.getByRole('textbox', { name: '归属说明', exact: true })).toBeDisabled();
  check('更正后刷新准确恢复原文书请求');
  const facts = await api(`/api/v2/archive-pages/${fixture.browser_page_id}/facts`, 'FactList');
  assert.ok(facts.items.length > 0 && facts.items.every(item => item.document_version_id === updated.version_id));
  check('全部保存事实引用准确当前文书版本');
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    const scale = await page.locator('.phone-scroll').evaluate(scroll => {
      window.groupingTextSizes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')]
        .map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      const before = Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize);
      for (const item of window.groupingTextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
      return { before, after: Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize) };
    });
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth }));
    assert.equal(scale.after, scale.before * 2); assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ width, geometry }));
    measurements.push({ width, scale, geometry });
    await page.evaluate(() => { for (const item of window.groupingTextSizes) item.node.style.fontSize = item.original; delete window.groupingTextSizes; });
  }
  check('五种宽度及实际200%文字包含完整保存内容');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  await page.waitForLoadState('networkidle');
  const count = requests.length; await page.waitForTimeout(1200); assert.equal(requests.length, count);
  check('私人文书不进入浏览器存储且没有循环读取');
  await writeFile('../.planning-work/t43-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T43', checked_at: new Date().toISOString(), checks, responses, requests, measurements, visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat_browser: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
