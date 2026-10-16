import { acceptanceUsername, acceptancePassword } from './acceptance-credentials.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const baseURL = 'http://127.0.0.1:5174';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.goto(`${baseURL}/login`);
await page.getByPlaceholder('账号', { exact: true }).fill(acceptanceUsername);
await page.locator('input[type=password]').fill(acceptancePassword);
await page.getByRole('button', { name: '登录', exact: true }).click();
await page.waitForURL(`${baseURL}/home`);
const csrf = (await (await context.request.get(`${baseURL}/api/auth/bootstrap`)).json()).csrf_token;
const relations = (await (await context.request.get(`${baseURL}/api/relations`)).json()).relations;
const patient = relations.find(item => item.display_id === '模拟患儿B').patient_id;
await page.getByLabel('当前患儿').selectOption(patient);
const concept = `qa03-${randomUUID()}`;
const points = [
  ['2026-09-01', '37', 'C', 'eq', '模拟耳温', 'known'],
  ['2026-09-02', '38', 'C', 'eq', '模拟耳温', 'known'],
  ['2026-09-03', '38', 'C', 'eq', '模拟耳温', 'known'],
  ['2026-09-04', '37', 'C', 'eq', '模拟耳温', 'known'],
  ['2026-09-05', '39', 'C', 'lt', '模拟耳温', 'known'],
  ['2026-09-06', '36', 'C', 'eq', '模拟耳温', 'known'],
  ['2026-09-06', '37', 'C', 'eq', '模拟耳温', 'known'],
  ['2026-09-07', '38', 'C', 'eq', '模拟耳温', 'known'],
  ['2026-09-08', null, 'C', null, '模拟耳温', 'unknown'],
  ['2026-09-09', '37', null, 'eq', '模拟耳温', 'known'],
  ['2026-09-10', '37', 'C', 'eq', '模拟额温', 'known'],
];
const target = `/api/patients/${patient}/records`;
for (const [date, value, unit, qualifier, method, state] of points) {
  const payload = { kind: 'observation', capture_method: 'manual', fields: [{ field_code: 'temp', concept_code: concept,
    value_type: 'decimal', value_state: state, raw_numeric: value, raw_unit: unit, qualifier, method,
    medical_time: { precision: 'date', local_date: date, timezone_name: 'Asia/Shanghai', resolution_status: 'local_only' }, sample_time_origin: 'observation' }] };
  const requestId = randomUUID();
  const intentResponse = await context.request.post(`${baseURL}/api/write-intents`, { data: { operation_id: 'record_create', target_key: target, request_id: requestId, payload, schema_version: 'back-json-1' }, headers: { 'X-CSRFToken': csrf } });
  assert.equal(intentResponse.status(), 201);
  const intent = await intentResponse.json();
  const saved = await context.request.post(`${baseURL}${target}`, { data: payload, headers: { 'X-CSRFToken': csrf, 'X-Write-Intent': intent.intent_id, 'X-Intent-Generation': intent.generation, 'Idempotency-Key': requestId } });
  assert.equal(saved.status(), 201, await saved.text());
}
const response = await context.request.post(`${baseURL}/api/patients/${patient}/analytes/${concept}/search`, { data: {}, headers: { 'X-CSRFToken': csrf } });
assert.equal(response.status(), 200, await response.text());
const trend = (await response.json()).trend;
assert.equal(trend.length, points.length);
assert.deepEqual(trend.map(item => item.direction), [null, 'up', 'same', 'down', null, null, null, null, null, null, null]);
assert.ok(trend[4].comparison_reasons.includes('qualified_value'));
assert.ok(trend[6].comparison_reasons.includes('same_time'));
assert.ok(trend[7].comparison_reasons.includes('same_time'));
assert.ok(trend[9].comparison_reasons.includes('unit_unknown'));
assert.ok(trend[10].comparison_reasons.includes('method_mismatch'));
await page.getByRole('link', { name: '记录', exact: true }).click();
await page.evaluate(path => window.history.pushState({}, '', path), `/records/analytes/${concept}`);
await page.evaluate(() => window.dispatchEvent(new PopStateEvent('popstate')));
await page.getByRole('img', { name: '检验趋势图，完整原值与来源见历次结果列表' }).waitFor();
const chart = await page.evaluate(async () => {
  const resource = performance.getEntriesByType('resource').find(item => item.name.includes('/chart__js_auto.js?'));
  if (!resource) throw new Error('Chart.js 模块未读取');
  const { default: Chart } = await import(resource.name);
  const instance = Chart.getChart(document.querySelector('canvas'));
  if (!instance) throw new Error('Chart.js 实例未建立');
  const point = instance.getDatasetMeta(0).data[1];
  return { values: instance.data.datasets[0].data, point: { x: point.x, y: point.y }, spanGaps: instance.data.datasets[0].spanGaps };
});
assert.deepEqual(chart.values, [37, 38, 38, 37, null, null, null, null, null, null, null]);
assert.equal(chart.spanGaps, false);
await page.locator('canvas').click({ position: chart.point });
const tooltip = await page.evaluate(async () => {
  const resource = performance.getEntriesByType('resource').find(item => item.name.includes('/chart__js_auto.js?'));
  const { default: Chart } = await import(resource.name);
  return Chart.getChart(document.querySelector('canvas')).tooltip.body;
});
assert.match(JSON.stringify(tooltip), /38 C/);
assert.match(JSON.stringify(tooltip), /来源/);
assert.deepEqual(errors, []);
await writeFile('../.qa-work/QA03/trends.json', JSON.stringify({ testedAt: new Date().toISOString(), database: 'sjia_qa03', results: [
  { id: 'comparable-points-and-conflict-gaps', status: 'PASS', trend },
  { id: 'chart-points-and-source-tooltip', status: 'PASS', chart, tooltip },
], errors }, null, 2));
await browser.close();
console.log(JSON.stringify({ checks: 2, passed: 2 }));
