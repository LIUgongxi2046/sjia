import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, dataset: { type: 'string' },
  credentials: { type: 'string' }, computed: { type: 'string' }, 'receipt-only': { type: 'boolean', default: false },
  origin: { type: 'string', default: 'http://127.0.0.1:5178' } } });
for (const key of ['report', 'dataset', 'credentials', 'computed']) assert.ok(values[key], key);
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const dataset = JSON.parse(await readFile(resolve(values.dataset), 'utf8'));
const computed = JSON.parse(await readFile(resolve(values.computed), 'utf8'));
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
assert.equal(computed.result, 'PASS');
const report = { result: 'FAIL', phase: values['receipt-only'] ? 'receipt' : 'complete', checks: [], page_errors: [], visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: 9243, headless: true });
const page = await native.context.newPage();
page.setDefaultTimeout(120000);
page.on('pageerror', error => report.page_errors.push(error.message));
async function check(name, details = {}) {
  report.checks.push({ name, result: 'PASS', ...details });
  await writeFile(output, JSON.stringify(report, null, 2));
}
async function get(path) {
  const response = await page.evaluate(async url => {
    const result = await fetch(url, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(120000) });
    return { status: result.status, body: await result.json() };
  }, values.origin + path);
  assert.equal(response.status, 200, path);
  return response.body;
}
async function loaded() {
  await page.locator('.home-patient').waitFor();
  await page.waitForFunction(() => !Array.from(document.querySelectorAll('.rich-read-state.loading'))
    .some(element => element.getClientRects().length > 0));
  assert.deepEqual(await page.getByRole('alert').allTextContents(), []);
}
try {
  await page.goto(values.origin + '/login');
  await page.getByRole('region', { name: '本地测试账号', exact: true }).getByRole('button', { name: '填入测试账号', exact: true }).click();
  assert.equal(await page.getByPlaceholder('账号', { exact: true }).inputValue(), credentials.username);
  assert.equal(await page.locator('input[type="password"]').inputValue(), credentials.password);
  const login = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/login');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  assert.equal((await login).status(), 200);
  await page.waitForURL('**/home');
  await loaded();
  if (!values['receipt-only']) for (const [key, patient] of Object.entries(dataset.patients)) {
    await page.locator('.home-patient>summary').click();
    await page.locator('.home-patient select').selectOption(patient.id);
    await page.waitForFunction(async identity => {
      const response = await fetch('/api/session');
      return response.ok && (await response.json()).selected_patient_id === identity;
    }, patient.id);
    await page.reload();
    await loaded();
    assert.equal(await page.getByRole('note').filter({ hasText: '模拟数据 · 仅用于功能体验' }).count(), 1);
    assert.equal((await page.locator('body').innerText()).includes('记录时间无法确定先后'), false);
    assert.equal((await page.locator('body').innerText()).includes('记录时间待确认'), false);
    const home = await get(`/api/patients/${patient.id}/home`);
    assert.equal(home.display_id, patient.display_id);
    assert.equal(home.assessment_state, 'current');
    assert.equal(home.processing_state, 'idle');
    if (key === 'child_a') {
      assert.equal(home.current_alert_id, computed.event_id);
      assert.equal(home.clinical.approved_text.includes('模拟'), false);
      assert.equal(home.clinical.approved_text.includes('演示'), false);
      assert.ok(home.clinical.approved_text.includes('皮疹'));
      await page.getByText(home.clinical.approved_text, { exact: true }).waitFor();
      await page.reload(); await loaded();
      assert.equal((await get(`/api/patients/${patient.id}/home`)).current_alert_id, computed.event_id);
      await check('COMPUTED_ALERT_AND_GLOBAL_DATA_LABEL_SURVIVE_RELOAD', { child: patient.display_id });
    } else {
      assert.equal(home.current_alert_id, null);
      await check('NEGATIVE_INPUT_HAS_NO_POSITIVE_ALERT', { child: patient.display_id });
    }
    await check('HOME_OBSERVATIONS_HAVE_DETERMINATE_HISTORICAL_ORDER', { child: patient.display_id });
    const saved = Object.entries(dataset.dataset.records).find(([name]) => name.startsWith(key + '/report-'))[1];
    const record = await get(`/api/records/${saved.record_id}`);
    const sample = record.report.sample_time;
    const result = record.report.result_time;
    assert.equal(sample.resolution_status, 'resolved');
    assert.equal(sample.timezone_name, 'Asia/Shanghai');
    assert.ok(Date.parse(sample.instant) < Date.parse(result.instant));
    assert.ok(Date.parse(result.instant) < Date.parse(record.recorded_at));
    for (const field of record.fields) {
      assert.equal(field.medical_time.instant, sample.instant);
      assert.ok(field.method);
    }
    await page.goto(values.origin + `/records/${saved.record_id}`);
    await page.locator('.rich-record-field').first().waitFor();
    await check('SAMPLE_RESULT_AND_REGISTRATION_TIMES_ARE_ORDERED', { child: patient.display_id });
    const history = await get(`/api/records/${saved.record_id}/history`);
    assert.ok(history);
    await check('HISTORY_REMAINS_READABLE_AFTER_TIME_CORRECTION', { child: patient.display_id });
    await page.goto(values.origin + '/home'); await loaded();
  }
  await page.locator('.home-patient>summary').click();
  await page.locator('.home-patient select').selectOption(dataset.patient_id);
  await page.waitForFunction(async identity => (await (await fetch('/api/session')).json()).selected_patient_id === identity, dataset.patient_id);
  const detail = await get(`/api/alerts/${computed.event_id}`);
  assert.equal(detail.validity, 'valid');
  assert.equal(detail.content_state, 'available');
  const reading = detail.read_at ? null : page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/alerts/${computed.event_id}/read` && response.request().method() === 'POST');
  await page.goto(values.origin + `/alerts/${computed.event_id}`);
  await page.locator('[data-screen-id="UI2-12"]').waitFor();
  await page.getByText(detail.content.approved_text, { exact: true }).scrollIntoViewIfNeeded();
  if (reading) assert.equal((await reading).status(), 200);
  assert.ok((await get(`/api/alerts/${computed.event_id}`)).read_at);
  await check('ACTUAL_CALCULATED_ALERT_DETAIL_AND_READ_RECEIPT');
  await page.getByText('已送达本人浏览器', { exact: true }).waitFor();
  const delivered = await get(`/api/v2/alerts/${computed.event_id}/notification`);
  assert.equal(delivered.record.delivery_status, 'delivered');
  assert.equal(delivered.record.trace_state, 'available');
  assert.ok(delivered.record.attempts.some(item => item.receipts.some(receipt => receipt.status === 'delivered')));
  await check('ACTUAL_BROWSER_DELIVERY_ACKNOWLEDGEMENT');
  assert.deepEqual(await page.getByRole('alert').allTextContents(), []);
  assert.deepEqual(report.page_errors, []);
  report.result = 'PASS';
} finally {
  await writeFile(output, JSON.stringify(report, null, 2));
  await native.close();
}
assert.equal(report.result, 'PASS');
console.log(JSON.stringify({ result: report.result, checks: report.checks.length }));
