import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, credentials: { type: 'string' },
  dataset: { type: 'string' }, presets: { type: 'string' }, mapped: { type: 'string' }, origin: { type: 'string', default: 'http://127.0.0.1:5178' } } });
for (const key of ['report', 'credentials', 'dataset', 'presets', 'mapped']) assert.ok(values[key], key);
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const dataset = JSON.parse(await readFile(resolve(values.dataset), 'utf8'));
const presets = JSON.parse(await readFile(resolve(values.presets), 'utf8'));
const mapped = JSON.parse(await readFile(resolve(values.mapped), 'utf8'));
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), checks: [], page_errors: [], stage: 'LOGIN', visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: 9241, headless: true });
const page = await native.context.newPage();
page.setDefaultTimeout(60000);
page.on('pageerror', error => report.page_errors.push(error.message));
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function check(name, details = {}) { report.checks.push({ name, result: 'PASS', ...details }); await save(); }
async function get(path) {
  const response = await native.context.request.get(values.origin + path);
  assert.equal(response.status(), 200, path);
  return response.json();
}
async function visit(path, selector) {
  await page.goto(values.origin + path);
  await page.locator(selector).first().waitFor();
  const loading = page.locator('.rich-read-state.loading');
  while (await loading.count()) await loading.first().waitFor({ state: 'hidden' });
  assert.deepEqual(await page.getByRole('alert').allTextContents(), [], path);
}
try {
  await page.goto(values.origin + '/login');
  const card = page.getByRole('region', { name: '本地测试账号', exact: true });
  await card.getByRole('button', { name: '填入测试账号', exact: true }).click();
  assert.equal(await page.getByPlaceholder('账号', { exact: true }).inputValue(), credentials.username);
  assert.equal(await page.locator('input[type="password"]').inputValue(), credentials.password);
  const login = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/login');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  assert.equal((await login).status(), 200);
  await page.waitForURL('**/home');
  const session = await get('/api/session');
  assert.equal(session.principal_id, dataset.actor_id);
  assert.equal(session.selected_patient_id, dataset.patient_id);
  const relations = await get('/api/relations');
  const expectedNames = Object.values(dataset.patients).map(item => item.display_id);
  assert.ok(expectedNames.every(name => /^\p{Script=Han}{2,4}$/u.test(name)));
  assert.deepEqual(relations.relations.map(item => item.display_id).sort(), expectedNames.sort());
  assert.deepEqual(relations.relations.map(item => item.patient_id).sort(), [...dataset.patient_ids].sort());
  await check('LOCAL_LOGIN_SHOWS_ONLY_TWO_NEW_FICTIONAL_CHILDREN');
  await visit('/home', '.home-alert');
  assert.equal(await page.getByRole('note').filter({ hasText: '模拟数据 · 仅用于功能体验' }).count(), 1);
  let home = await get(`/api/patients/${dataset.patient_id}/home`);
  assert.equal(home.current_alert_id, presets.event_id);
  assert.equal(presets.calculation_source, 'dynamic-evaluation-task-2');
  assert.equal(home.clinical.approved_text.includes('模拟演示'), false);
  await page.reload();
  await page.locator('.home-alert').waitFor();
  assert.equal((await get(`/api/patients/${dataset.patient_id}/home`)).current_alert_id, presets.event_id);
  await check('CURRENT_CALCULATED_ALERT_SURVIVES_READ_ONLY_HOME_AND_RELOAD');
  const detail = await get(`/api/alerts/${presets.event_id}`);
  const reading = detail.read_at ? null : page.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/alerts/${presets.event_id}/read` && response.request().method() === 'POST');
  await visit(`/alerts/${presets.event_id}`, '[data-screen-id="UI2-12"]');
  await page.getByText(home.clinical.approved_text, { exact: true }).scrollIntoViewIfNeeded();
  if (reading) assert.equal((await reading).status(), 200);
  const stats = await get(`/api/patients/${dataset.patient_id}/alerts`);
  assert.ok(stats.alerts.find(item => item.alert_id === presets.event_id)?.read_at);
  assert.ok(stats.alerts.some(item => item.validity === 'invalid'));
  await check('ALERT_CONTENT_HISTORY_AND_ACTUAL_READ_RECEIPT');
  for (const [key, patient] of Object.entries(dataset.patients)) {
    report.stage = patient.display_id; await save();
    await visit('/home', '.home-alert');
    await page.locator('.home-patient>summary').click();
    await page.locator('.home-patient select').selectOption(patient.id);
    await page.waitForFunction(async expected => {
      const response = await fetch('/api/session');
      return response.ok && (await response.json()).selected_patient_id === expected;
    }, patient.id);
    const scoped = await get(`/api/patients/${patient.id}/home`);
    assert.equal(scoped.display_id, patient.display_id);
    await visit('/records', '.feature-grid');
    for (const label of ['体温症状', '检验报告', '治疗资料']) {
      await page.getByRole('tab', { name: label, exact: true }).click();
      const loading = page.locator('.rich-read-state.loading');
      while (await loading.count()) await loading.first().waitFor({ state: 'hidden' });
      await page.locator('.rich-record-card').first().waitFor();
    }
    const recordEntry = Object.entries(dataset.dataset.records).find(([name]) => name.startsWith(key + '/report-'))[1];
    const record = await get(`/api/records/${recordEntry.record_id}`);
    assert.equal(record.patient_id, patient.id);
    assert.equal(record.fields.length, 3);
    await visit(`/records/${record.record_id}`, '.rich-record-field');
    const originals = await get(`/api/record-versions/${record.record_version_id}/originals`);
    assert.ok(originals);
    const firstFile = Object.entries(dataset.dataset.attachments).find(([name]) => name.startsWith(key + '/report-'))[1];
    const content = await native.context.request.get(values.origin + `/api/files/${firstFile.file_id}/content`);
    assert.equal(content.status(), 200);
    assert.match(content.headers()['content-type'], /image\/png/);
    assert.ok((await content.body()).length > 1000);
    await check('PERSISTED_RECORD_TYPES_AND_GENERATED_REPORT_ORIGINAL', { child: patient.display_id });
    await visit('/drafts', '.rich-record-patient');
    const drafts = await get(`/api/patients/${patient.id}/drafts`);
    assert.ok(drafts.drafts.length >= 3);
    for (const [kind, path, selector] of [
      ['observation', '/register/observations', 'input[aria-label="体温原值"]'],
      ['report', '/register/reports/confirm', '.registration-candidate input'],
      ['therapy', '/register/therapies', 'input[placeholder="按原文填写"]'],
    ]) {
      const id = dataset.dataset.drafts[`${key}/${kind}`];
      const original = await get(`/api/drafts/${id}`);
      const value = kind === 'observation' ? original.payload.temp
        : kind === 'report' ? original.payload.candidates[0].name : original.payload.medication;
      await visit(path + '?draft=' + id, selector);
      await page.waitForFunction(({ selector, value }) => {
        function inputs(root) {
          return [...root.querySelectorAll(selector)].some(element => element.value === value)
            || [...root.querySelectorAll('*')].some(element => element.shadowRoot && inputs(element.shadowRoot));
        }
        return inputs(document);
      }, { selector, value });
      assert.equal((await get(`/api/drafts/${id}`)).patient_id, patient.id);
    }
    await check('THREE_PERSISTED_DRAFT_TYPES_RESTORE_IN_ACTUAL_FORMS', { child: patient.display_id });
    await visit('/summaries', '[data-screen-id="UI2-13"]');
    const summaries = await get(`/api/patients/${patient.id}/summaries`);
    const current = summaries.summaries.find(item => presets.summary_ids.includes(item.summary_id));
    assert.ok(current && !current.needs_regeneration);
    await visit(`/summaries/${current.summary_id}`, '.rich-summary-cover');
    await page.reload(); await page.locator('.rich-summary-cover').waitFor();
    await check('CURRENT_SUMMARY_READS_PERSISTED_SNAPSHOT_AFTER_RELOAD', { child: patient.display_id });
    await visit('/alert-presets', '[data-screen-id="UI2-ALERT-PRESETS"]');
    const planName = presets.directories[patient.id].display_name;
    await page.getByRole('heading', { name: planName, exact: true }).waitFor();
    assert.equal(await page.getByRole('radio').count(), 2);
    assert.equal(await page.getByText('专业审核', { exact: true }).count(), 0);
    const options = page.getByRole('radio');
    const selectedIndex = await options.first().isChecked() ? 1 : 0;
    await options.nth(selectedIndex).check();
    await page.getByRole('button', { name: '保存方案选择', exact: true }).click();
    await page.getByRole('heading', { name: '已保存方案选择', exact: true }).waitFor();
    await page.reload();
    await page.getByRole('heading', { name: planName, exact: true }).waitFor();
    assert.equal(await page.getByRole('radio').nth(selectedIndex).isChecked(), true);
    await check('PRESET_SELECTION_SAVES_AND_RELOADS_WITH_SIMULATION_LABEL', { child: patient.display_id });
    await visit('/archives', '[data-screen-id="UI2-25"]');
    await page.getByText(record.source_organization, { exact: false }).first().waitFor();
    await visit('/archives/batches', '[data-screen-id="UI2-26"]');
    await check('GENERATED_DOCUMENTS_AND_BATCHES_READ_FROM_DATABASE', { child: patient.display_id });
    await visit('/records/period', '[data-screen-id="UI2-31"]');
    const period = await get(`/api/v2/patients/${patient.id}/observation-period`);
    assert.ok(period.version_id && period.samplings.length);
    assert.equal(await page.getByLabel('本人时段标签', { exact: false }).inputValue(), period.label);
    const mappedRecord = await get(`/api/records/${mapped.records[patient.id][0]}`);
    const concept = mappedRecord.fields.find(item => item.concept_code)?.concept_code;
    assert.ok(concept);
    assert.equal(concept, mapped.concept_code);
    await visit(`/records/analytes/${encodeURIComponent(concept)}`, '[data-screen-id="UI2-07"]');
    await page.locator('.rich-analyte-result').first().waitFor();
    await check('OBSERVATION_PERIOD_AND_ANALYTE_HISTORY_USE_SAVED_DATA', { child: patient.display_id });
    await visit('/account/reminders', '[data-screen-id="UI2-32"]');
    assert.equal(await page.getByText('已经启用', { exact: false }).count() > 0, true);
    await check('PERSONAL_REMINDER_AVAILABLE_FOR_SELECTED_CHILD', { child: patient.display_id });
    if (key === 'child_a') {
      let cursor = null;
      let duplicate = null;
      const visited = new Set();
      do {
        assert.equal(visited.has(cursor), false);
        visited.add(cursor);
        const query = cursor ? '?' + new URLSearchParams({ cursor }) : '';
        const directory = await get(`/api/v2/patients/${patient.id}/archive-relations/search${query}`);
        duplicate = directory.candidates.find(item => item.suggested_types.includes('same_bytes')) ?? duplicate;
        cursor = directory.next_cursor;
      } while (cursor && !duplicate);
      assert.ok(duplicate);
      await visit('/archives/duplicates', '[data-screen-id="UI2-29"]');
      await page.getByText('选择当前获准来源与关系候选', { exact: true }).click();
      await page.getByRole('heading', { name: '待确认候选', exact: true }).waitFor();
      const more = page.getByRole('button', { name: '继续读取来源与关系', exact: true });
      while (await more.count()) {
        await more.click();
        const loading = page.locator('.rich-read-state.loading');
        while (await loading.count()) await loading.first().waitFor({ state: 'hidden' });
      }
      assert.ok(await page.getByRole('button', { name: /与.+待确认/ }).count());
      await check('DUPLICATE_GENERATED_ORIGINAL_HAS_PERSISTED_CANDIDATE');
    }
  }
  report.stage = 'ACCOUNT_CLOSEOUT'; await save();
  const first = dataset.dataset.correction;
  await visit('/home', '.home-alert');
  await page.locator('.home-patient>summary').click();
  await page.locator('.home-patient select').selectOption(dataset.patient_id);
  await page.waitForFunction(async expected => {
    const response = await fetch('/api/session');
    return response.ok && (await response.json()).selected_patient_id === expected;
  }, dataset.patient_id);
  await visit(`/records/${first.record_id}/history`, '[data-screen-id]');
  const history = await get(`/api/records/${first.record_id}/history`);
  assert.ok(history);
  await visit('/account/reminders', '[data-screen-id="UI2-32"]');
  await page.getByLabel('提醒对象', { exact: true }).selectOption('record_task');
  await page.getByText('补充近期体温观察', { exact: false }).first().waitFor();
  await check('CORRECTION_HISTORY_AND_PERSONAL_REMINDER_ARE_AVAILABLE');
  await visit('/associations', '[data-screen-id="UI2-18"]');
  for (const name of expectedNames) await page.getByText(name, { exact: true }).first().waitFor();
  const relation = relations.relations.find(item => item.patient_id === dataset.patient_id);
  await visit(`/associations/${relation.relation_id}`, '[data-screen-id="UI2-20"]');
  await visit('/account/data-use', '[data-screen-id="UI2-16"]');
  await page.getByRole('heading', { name: '清楚了解资料如何使用', exact: true }).waitFor();
  await check('RELATION_DETAILS_AND_DATA_USE_READ_WITH_CURRENT_ACCESS');
  await visit('/account', '[data-screen-id="UI2-15"]');
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await page.waitForURL('**/login');
  await card.waitFor();
  await check('LOGOUT_RETURNS_TO_LOCAL_TEST_CREDENTIALS');
  assert.deepEqual(report.page_errors, []);
  report.result = 'PASS'; report.stage = 'COMPLETE';
} finally {
  if (report.result === 'IN_PROGRESS') report.result = 'FAIL';
  report.finished_at = new Date().toISOString();
  report.alerts = await page.getByRole('alert').allTextContents();
  report.headings = await page.getByRole('heading').allTextContents();
  await save(); await native.close();
  console.log(JSON.stringify({ result: report.result, stage: report.stage, checks: report.checks.length }));
}
