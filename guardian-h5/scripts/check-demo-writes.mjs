import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { Temporal } from '@js-temporal/polyfill';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, credentials: { type: 'string' }, dataset: { type: 'string' },
  origin: { type: 'string', default: 'http://127.0.0.1:5178' } } });
for (const key of ['report', 'credentials', 'dataset']) assert.ok(values[key]);
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const dataset = JSON.parse(await readFile(resolve(values.dataset), 'utf8'));
const report = { result: 'IN_PROGRESS', checks: [], stage: 'LOGIN', page_errors: [], visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: 9242, headless: true });
const page = await native.context.newPage();
page.setDefaultTimeout(60000);
page.on('pageerror', error => report.page_errors.push(error.message));
async function get(path) {
  const response = await native.context.request.get(values.origin + path);
  assert.equal(response.status(), 200, path);
  return response.json();
}
async function check(name, detail) {
  report.checks.push({ name, result: 'PASS', ...detail });
  await writeFile(output, JSON.stringify(report, null, 2));
}
try {
  await page.goto(values.origin + '/login');
  await page.getByRole('button', { name: '填入测试账号', exact: true }).click();
  assert.equal(await page.getByPlaceholder('账号', { exact: true }).inputValue(), credentials.username);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL('**/home');
  const session = await get('/api/session');
  assert.equal(session.selected_patient_id, dataset.patient_id);
  for (const [kind, path, selector, button] of [
    ['observation', '/register/observations', 'input[aria-label="体温原值"]', '确认并保存'],
    ['report', '/register/reports/confirm', '.registration-candidate input', '确认并保存'],
    ['therapy', '/register/therapies', 'input[placeholder="按原文填写"]', '确认并保存资料'],
  ]) {
    report.stage = kind;
    const original = await get('/api/drafts/' + dataset.dataset.drafts[`child_a/${kind}`]);
    const response = await native.context.request.post(values.origin + `/api/patients/${dataset.patient_id}/drafts`, {
      data: { form_type: kind, payload: original.payload }, headers: { 'X-CSRFToken': session.csrf_token } });
    assert.ok(response.ok(), kind);
    const draft = await response.json();
    report.created_drafts ??= [];
    report.created_drafts.push({ draft_id: draft.draft_id, version: draft.version, kind });
    await writeFile(output, JSON.stringify(report, null, 2));
    await page.goto(values.origin + path + '?draft=' + draft.draft_id);
    await page.locator(selector).first().waitFor();
    const value = kind === 'observation' ? original.payload.temp
      : kind === 'report' ? original.payload.candidates[0].name : original.payload.medication;
    await page.waitForFunction(({ selector, value }) => {
      function restored(root) {
        return [...root.querySelectorAll(selector)].some(item => item.value === value)
          || [...root.querySelectorAll('*')].some(item => item.shadowRoot && restored(item.shadowRoot));
      }
      return restored(document);
    }, { selector, value });
    if (kind === 'therapy') await page.getByText('资料出处与确认', { exact: true }).click();
    if (kind !== 'observation') await page.locator('.registration-confirmation input[type="checkbox"]').check();
    const receipt = page.waitForResponse(item => new URL(item.url()).pathname === `/api/patients/${dataset.patient_id}/records` && item.request().method() === 'POST');
    await page.getByRole('button', { name: button, exact: true }).click();
    const saved = await receipt;
    assert.ok(saved.ok(), `${kind}:${saved.status()}`);
    const result = await saved.json();
    await page.waitForURL('**/records');
    const record = await get('/api/records/' + result.record_id);
    assert.equal(record.kind, kind);
    assert.equal(record.patient_id, dataset.patient_id);
    const submitted = await native.context.request.get(values.origin + '/api/drafts/' + draft.draft_id);
    assert.equal(submitted.status(), 409);
    assert.equal((await submitted.json()).code, 'DRAFT_NOT_RESTORABLE');
    assert.equal((await get(`/api/patients/${dataset.patient_id}/drafts`)).drafts.some(item => item.draft_id === draft.draft_id), false);
    await page.goto(values.origin + '/records/' + record.record_id);
    await page.locator('.rich-record-field').first().waitFor();
    await page.reload(); await page.locator('.rich-record-field').first().waitFor();
    await check('ACTUAL_FORM_SAVE_AND_RELOAD', { kind, record_id: record.record_id, field_count: record.fields.length,
      submitted_draft_id: draft.draft_id, submitted_draft_restore_status: submitted.status() });
  }
  report.stage = 'SUMMARY';
  await page.goto(values.origin + '/summaries');
  const today = Temporal.Now.plainDateISO('Asia/Shanghai');
  await page.getByLabel('开始日期', { exact: true }).fill(today.subtract({ days: 20 }).toString());
  await page.getByLabel('结束日期', { exact: true }).fill(today.toString());
  await page.getByRole('button', { name: '生成就医摘要', exact: true }).click();
  await page.waitForURL(/\/summaries\/[0-9a-f-]+/, { timeout: 120000 });
  await page.locator('.rich-summary-cover').waitFor();
  await page.reload(); await page.locator('.rich-summary-cover').waitFor();
  await check('NEW_SUMMARY_COMPLETES_THROUGH_RUNNING_WORKER', { summary_id: new URL(page.url()).pathname.split('/').at(-1) });
  report.stage = 'DOCUMENT_SOURCES';
  await writeFile(output, JSON.stringify(report, null, 2));
  for (const [key, patient] of Object.entries(dataset.patients)) {
    await page.goto(values.origin + '/home');
    await page.locator('.home-patient>summary').click();
    await page.locator('.home-patient select').selectOption(patient.id);
    await page.waitForFunction(async expected => {
      const response = await fetch('/api/session');
      return response.ok && (await response.json()).selected_patient_id === expected;
    }, patient.id);
    const archive = Object.entries(dataset.dataset.archives).find(([name]) => name.startsWith(key + '/report-'))[1];
    const document = await get(`/api/v2/archive-documents/${archive.document_id}`);
    assert.equal(document.patient_id, patient.id);
    await page.goto(values.origin + `/archives/grouping?document=${archive.document_id}&document_version=${archive.version_id}`);
    await page.locator('[data-screen-id="UI2-28"]').waitFor();
    await page.waitForFunction(organization => {
      function contains(root) {
        return [...root.querySelectorAll('input')].some(item => item.value === organization)
          || [...root.querySelectorAll('*')].some(item => item.shadowRoot && contains(item.shadowRoot));
      }
      return contains(document);
    }, document.organization);
    await page.getByRole('link', { name: '查看原文依据', exact: true }).click();
    await page.locator('[data-screen-id="UI2-33"]').waitFor();
    await page.getByRole('heading', { name: '已保存原始文书', exact: true }).waitFor();
    assert.deepEqual(await page.getByRole('alert').allTextContents(), []);
    await check('DOCUMENT_GROUPING_AND_SOURCE_LINK_READ_SAVED_VERSION', { child: patient.display_id });
  }
  assert.deepEqual(report.page_errors, []);
  report.result = 'PASS'; report.stage = 'COMPLETE';
} finally {
  if (report.result === 'IN_PROGRESS') report.result = 'FAIL';
  report.alerts = await page.getByRole('alert').allTextContents();
  report.finished_at = new Date().toISOString();
  await writeFile(output, JSON.stringify(report, null, 2));
  await native.close();
  console.log(JSON.stringify({ result: report.result, checks: report.checks.length, stage: report.stage }));
}
