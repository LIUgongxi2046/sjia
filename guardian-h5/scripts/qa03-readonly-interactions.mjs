import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const output = '../.qa-work/QA03/readonly-interactions';
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
const page = await context.newPage();
const errors = [];
const mutations = [];
const results = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  const path = new URL(request.url()).pathname;
  const query = path.endsWith('/records/search') || /\/analytes\/[^/]+\/search$/.test(path);
  if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && path.startsWith('/api/') && path !== '/api/auth/login' && !query) mutations.push(`${request.method()} ${path}`);
});
page.on('dialog', dialog => { errors.push(`出现非预期确认：${dialog.message()}`); void dialog.dismiss(); });
await page.goto('http://127.0.0.1:5173/login');
await page.getByPlaceholder('账号', { exact: true }).fill('acceptance-family');
await page.getByPlaceholder('密码（至少 8 位）').fill('Local-accept-2026');
await page.getByRole('button', { name: '登录', exact: true }).click();
await page.waitForURL('**/home');
await page.getByRole('button', { name: '健康档案', exact: true }).waitFor();
const session = await (await context.request.get('http://127.0.0.1:5173/api/auth/bootstrap')).json();
const relations = (await (await context.request.get('http://127.0.0.1:5173/api/relations')).json()).relations;
const patient = relations.find(relation => relation.display_id === '模拟患儿A');
assert.ok(patient);
const response = await context.request.post(`http://127.0.0.1:5173/api/patients/${patient.patient_id}/records/search`, { data: { limit: 100 }, headers: { 'X-CSRFToken': session.csrf_token } });
assert.equal(response.status(), 200);
const records = (await response.json()).records;
const record = records.find(item => item.detail.attachments.length);
assert.ok(record, '现有资料需要包含原图');
const recordDetail = await (await context.request.get(`http://127.0.0.1:5173/api/records/${record.record_id}`)).json();
assert.ok(Array.isArray(recordDetail.allowed_actions));
assert.ok(recordDetail.allowed_actions.includes('read_history'));
results.push({ id: 'record-object-actions', status: 'PASS' });
const analyte = await (await context.request.post(`http://127.0.0.1:5173/api/patients/${patient.patient_id}/analytes/temp/search`, { data: {}, headers: { 'X-CSRFToken': session.csrf_token } })).json();
assert.ok(analyte.trend.length > 0);
assert.ok(analyte.trend.every(item => Array.isArray(item.comparison_reasons) && Array.isArray(item.verification_states) && typeof item.origin_type === 'string' && item.record_version_id));
results.push({ id: 'analyte-source-comparison-metadata', status: 'PASS' });
const summaries = (await (await context.request.get(`http://127.0.0.1:5173/api/patients/${patient.patient_id}/summaries`)).json()).summaries;
const routes = [
  ['S01', '/login'], ['S02-A', '/associations'], ['S02-B', '/associations/new'], ['S02-C', `/associations/${patient.relation_id}`],
  ['S03', '/home'], ['S04', '/register/observations'], ['S05', '/register/reports'], ['S06', '/register/therapies'],
  ['S07', '/records'], ['S08', `/records/${record.record_id}`], ['S09', '/records/analytes/temp'],
  ['S10-A', `/records/${record.record_id}/correct`], ['S10-B', `/records/${record.record_id}/history`],
  ['S11-A', '/alerts'], ['S12-A', '/summaries'], ['S13-A', '/account'], ['S13-B', '/account/data-use'], ['S14', '/drafts'],
  ['S15-A', '/access-denied'], ['S15-B', '/service-unavailable'], ['S15-C', '/page-unavailable'],
];
if (summaries.length) routes.push(['S12-B', `/summaries/${summaries[0].summary_id}`]);
for (const [screen, path] of routes) {
  await page.goto(`http://127.0.0.1:5173${path}`);
  await page.locator('h1').waitFor();
  await page.waitForLoadState('networkidle');
  for (const [width, font] of [[390, 16], [320, 16], [320, 32], [580, 16]]) {
    await page.setViewportSize({ width, height: 844 });
    await page.evaluate(size => { document.documentElement.style.fontSize = `${size}px`; }, font);
    const dimensions = await page.evaluate(() => ({ width: innerWidth, scroll: document.documentElement.scrollWidth }));
    assert.ok(dimensions.scroll <= dimensions.width, `${screen} 在 ${width}px、字体 ${font}px 出现横向溢出`);
    results.push({ screen, width, font, status: 'PASS' });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
  await page.screenshot({ path: `${output}/${screen}.png` });
  if (screen === 'S01') assert.equal(await page.getByRole('heading', { name: '当前账号已登录' }).count(), 1);
  if (screen === 'S09') {
    if (analyte.trend.some(item => item.direction)) {
      const canvas = page.getByRole('img', { name: '检验趋势图，完整原值与来源见历次结果列表' });
      await canvas.scrollIntoViewIfNeeded();
      assert.ok(await canvas.evaluate(element => [...element.getContext('2d').getImageData(0, 0, element.width, element.height).data].some(value => value !== 0)));
      await page.screenshot({ path: `${output}/S09-chart.png` });
    } else await page.getByText('可比较资料不足，尚未形成趋势图。', { exact: true }).waitFor();
    results.push({ id: 'analyte-chart-existing-data', status: 'PASS' });
  }
  if (screen === 'S10-A') {
    await page.waitForTimeout(11000);
    assert.deepEqual(mutations, [], '仅打开更正页面不能自动创建草稿');
  }
}
await page.goto(`http://127.0.0.1:5173/records/${record.record_id}`);
await page.getByText('查看第 1 张原图', { exact: true }).click();
await page.locator('.source-gallery-trigger').click();
await page.getByRole('dialog', { name: '原图查看器' }).waitFor();
const image = page.getByRole('dialog').locator('.adm-image-viewer-image-wrapper img').first();
await page.getByRole('button', { name: '旋转', exact: true }).click();
assert.equal(await image.evaluate(element => getComputedStyle(element).rotate), '90deg');
await page.screenshot({ path: `${output}/source-viewer.png` });
await page.keyboard.press('Escape');
await page.waitForFunction(() => !document.querySelector('dialog[open]'));
assert.equal(await page.locator('.source-gallery-trigger').evaluate(element => document.activeElement === element), true);
results.push({ id: 'source-viewer-open-rotate-escape-focus', status: 'PASS' });
await page.goto('http://127.0.0.1:5173/home');
await page.getByRole('button', { name: '健康档案', exact: true }).click();
await page.getByRole('dialog').getByRole('button', { name: '体温记录', exact: true }).click();
await page.getByRole('dialog').getByRole('heading', { name: '体温记录', exact: true }).waitFor();
await page.getByLabel('查看记录时间', { exact: true }).waitFor();
const timeOptions = await page.getByLabel('查看记录时间', { exact: true }).locator('option').evaluateAll(elements => elements.map(element => ({ value: element.value, label: element.textContent })));
const historical = timeOptions.find(option => !['latest', 'unknown'].includes(option.value));
if (historical) {
  await page.getByLabel('查看记录时间', { exact: true }).selectOption(historical.value);
  await page.getByRole('dialog').getByText('查看当时记录', { exact: true }).waitFor();
  await page.getByRole('link', { name: '查看完整记录', exact: true }).first().click();
  await page.waitForURL('**/records/*');
  await page.getByRole('link', { name: '返回', exact: true }).click();
  await page.getByRole('dialog').waitFor();
  assert.equal(await page.getByLabel('查看记录时间', { exact: true }).inputValue(), historical.value);
  results.push({ id: 'archive-history-detail-return', status: 'PASS' });
}
await page.getByRole('button', { name: '返回档案分类', exact: true }).click();
for (const category of ['症状记录', '检验报告', '治疗资料']) {
  await page.getByRole('dialog').getByRole('button', { name: category, exact: true }).click();
  await page.getByRole('dialog').getByRole('heading', { name: category, exact: true }).waitFor();
  await page.waitForLoadState('networkidle');
  assert.equal(await page.getByRole('dialog').count(), 1);
  await page.getByRole('button', { name: '返回档案分类', exact: true }).click();
  results.push({ id: `archive-category-${category}`, status: 'PASS' });
}
await page.getByRole('button', { name: '关闭健康档案', exact: true }).click();
assert.equal(await page.getByRole('button', { name: '健康档案', exact: true }).evaluate(element => document.activeElement === element), true);
results.push({ id: 'archive-close-focus', status: 'PASS' });
await page.emulateMedia({ colorScheme: 'dark' });
await page.screenshot({ path: `${output}/S03-dark.png` });
await page.emulateMedia({ colorScheme: 'light' });
for (const [path, title] of [['reports', '填写本次报告'], ['therapies', '填写本次治疗资料']]) {
  await page.goto(`http://127.0.0.1:5173/register/${path}`);
  const disclosure = page.locator('details').filter({ has: page.locator('summary', { hasText: title }) });
  assert.equal(await disclosure.evaluate(element => element.open), false);
  await disclosure.locator('summary').click();
  assert.equal(await disclosure.evaluate(element => element.open), true);
  assert.equal(await page.getByLabel('选择原始照片', { exact: true }).getAttribute('accept'), 'image/jpeg,image/png,image/webp');
  await page.waitForTimeout(11000);
  results.push({ id: `${path}-empty-form-no-autosave`, status: 'PASS' });
}
assert.deepEqual(errors, []);
assert.deepEqual(mutations, []);
await writeFile(`${output}/results.json`, JSON.stringify({ testedAt: new Date().toISOString(), existingDataOnly: true, results, errors, mutations }, null, 2));
await browser.close();
console.log(JSON.stringify({ checks: results.length, errors, mutations }));
