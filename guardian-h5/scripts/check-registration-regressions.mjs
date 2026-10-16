// Isolated synthetic fixtures: no backend, model provider, login, or patient data.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { chromium, expect } from 'playwright/test';

const baseURL = process.env.SJIA_TEST_URL || 'http://127.0.0.1:5173';
const executablePath = process.env.SJIA_CHROMIUM_PATH || (existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : undefined);
const browser = await chromium.launch({ executablePath, headless: true });
const patientId = '00000000-0000-4000-8000-000000000001';
const photoId = '00000000-0000-4000-8000-000000000002';
const emptyTime = { precision: 'unknown', value: '', timezone: '' };
const details = ['dose', 'dose_unit', 'concentration_text', 'route_text', 'frequency_text', 'interval_value', 'interval_unit'];
const detailLabels = ['单次剂量', '剂量单位', '制剂浓度', '给药途径', '原文频次', '间隔数值', '间隔单位'];
const checks = [];
const evidenceDir = process.env.SJIA_TEST_EVIDENCE_DIR;
async function screenshot(page, name, locator) {
  if (!evidenceDir) return;
  await mkdir(evidenceDir, { recursive: true });
  await locator.scrollIntoViewIfNeeded();
  await page.screenshot({ path: join(evidenceDir, `${name}.png`), fullPage: true });
}
function pass(name) { checks.push(name); console.log(`PASS ${name}`); }

async function fixture({ draftPayload, kind = 'therapy', loseResponse = false, rejectFirst = false, delayDraft = false } = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.clock.install();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.dismiss());
  const requests = [], intents = new Map(), committed = new Map();
  let draftWrites = 0, lost = false, rejected = false, resolveDraft;
  const draftId = randomUUID();
  let draft = draftPayload ? { draft_id: draftId, patient_id: patientId, form_type: kind, version: '1', status: 'editing', saved_at: new Date().toISOString(), payload: draftPayload } : null;
  await context.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    const body = req.postData() ? req.postDataJSON() : null;
    if (path === '/api/auth/bootstrap') return json({ csrf_token: 'synthetic-csrf' });
    if (path === '/api/session') return json({ principal_id: patientId, account_status: 'active', csrf_token: 'synthetic-csrf', capabilities: [], selected_patient_id: patientId });
    if (path === '/api/relations') return json({ relations: [{ relation_id: patientId, patient_id: patientId, display_id: '合成测试资料', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'read_file', 'write_record', 'manage_draft'] }] });
    if (path.endsWith('/field-config')) {
      const type = url.searchParams.get('kind');
      const specs = type === 'therapy'
        ? [['medication_name', 'text'], ['therapy_note', 'text'], ['therapy_status', 'categorical'], ['therapy_start', 'time'], ['therapy_end', 'time'], ['therapy_source', 'text'], ...details.map(code => [code, ['dose', 'interval_value'].includes(code) ? 'decimal' : code === 'interval_unit' ? 'categorical' : 'text'])]
        : type === 'observation' ? [['temp', 'decimal'], ['observation_note', 'text']] : [['report_item', 'decimal']];
      return json({ patient_id: patientId, kind: type, state: 'available', config_version: 'synthetic-config-1', fields: specs.map(([field_code, value_type]) => ({ field_code, value_type, label: field_code, writable: true, allowed_values: field_code === 'interval_unit' ? ['hour', 'day'] : ['ongoing', 'ended', 'not_started'] })) });
    }
    if (path === '/api/write-intents') {
      const old = intents.get(body.request_id);
      if (old) assert.deepEqual(old.payload, body.payload, 'same logical request must retain its complete payload');
      else intents.set(body.request_id, { id: randomUUID(), payload: body.payload });
      return json({ intent_id: intents.get(body.request_id).id, generation: '1' }, 201);
    }
    if (path === `/api/patients/${patientId}/records` && req.method() === 'POST') {
      const key = req.headers()['idempotency-key'];
      requests.push({ key, body });
      assert.deepEqual(intents.get(key).payload, body);
      if (rejectFirst && !rejected) { rejected = true; return json({ code: 'RECORD_INPUT_INVALID', message: '合成字段校验失败' }, 422); }
      if (!committed.has(key)) committed.set(key, { record_id: randomUUID(), record_version_id: randomUUID(), version: '1' });
      if (loseResponse && !lost) { lost = true; return route.abort('failed'); }
      return json(committed.get(key), 201);
    }
    if (path.endsWith('/records/search')) return json({ records: [], next_cursor: null });
    if (path === `/api/drafts/${draftId}` && req.method() === 'GET') return json(draft);
    if ((path.endsWith('/drafts') || path === `/api/drafts/${draftId}`) && ['POST', 'PUT'].includes(req.method())) {
      draftWrites += 1;
      if (delayDraft) await new Promise(resolve => { resolveDraft = resolve; });
      draft = { draft_id: draftId, patient_id: patientId, form_type: kind, version: String(Number(draft?.version || 0) + 1), status: 'editing', saved_at: new Date().toISOString(), payload: body.payload };
      return json(draft);
    }
    // Irrelevant optional upload features stay unavailable in this synthetic run.
    return json({ code: 'SYNTHETIC_NOT_CONFIGURED', message: '合成测试未配置此可选功能' }, 404);
  });
  const path = kind === 'report' ? '/register/reports/confirm' : `/register/${kind === 'therapy' ? 'therapies' : 'observations'}`;
  await page.goto(`${baseURL}${path}${draftPayload ? '?draft=' + draftId : ''}`);
  const field = kind === 'observation' ? page.getByRole('textbox', { name: '补充说明', exact: true })
    : kind === 'therapy' ? page.getByRole('textbox', { name: '原药名', exact: true }) : page.getByRole('textbox', { name: '原项目名称', exact: true });
  await expect(field).toBeEnabled();
  return { page, context, errors, field, requests, intents, committed, draftId, get draftWrites() { return draftWrites; }, releaseDraft: () => resolveDraft?.() };
}

try {
  // Directly exercise immutable payloads, concurrent clicks, repeat submissions,
  // and identity invalidation using the actual production submission controller.
  const page = await browser.newPage();
  await page.route('**/__registration_test__', route => route.fulfill({ contentType: 'text/html', body: '<html></html>' }));
  await page.goto(baseURL + '/__registration_test__');
  const api = await page.evaluate(async () => {
    const { createRecordSubmission, clearPendingRecordRequests } = await import('/src/api/recordSubmission.ts');
    const originalFetch = window.fetch;
    const attempts = [], intentPayloads = [], commits = new Map();
    let loseResponse = true;
    window.fetch = async (path, options) => {
      const body = JSON.parse(options.body);
      if (path === '/api/write-intents') { intentPayloads.push(body); return Response.json({ intent_id: 'i', generation: '1' }); }
      const key = options.headers['Idempotency-Key'];
      attempts.push({ key, body });
      if (!commits.has(key)) commits.set(key, { record_id: crypto.randomUUID() });
      if (loseResponse) { loseResponse = false; throw new TypeError('Synthetic lost response after commit'); }
      return Response.json(commits.get(key));
    };
    try {
      const payload = { kind: 'therapy', fields: [{ raw_text: '合成A' }], draft_id: 'd1', draft_version: '1' };
      const submission = createRecordSubmission('synthetic', payload);
      const first = submission.submit(), duplicate = submission.submit();
      const samePromise = first === duplicate;
      await Promise.allSettled([first, duplicate]);
      payload.fields[0].raw_text = '合成B'; payload.draft_version = '2';
      const recovered = await submission.submit();
      await submission.submit(); // Success remains cached for this action.
      const again = createRecordSubmission('synthetic', { kind: 'therapy', fields: [{ raw_text: '合成A' }], draft_id: 'd1', draft_version: '1' });
      await again.submit(); // Intentional identical record, new action.
      clearPendingRecordRequests();
      let invalidated = false;
      try { await submission.submit(); } catch { invalidated = true; }
      return { attempts, intentPayloads, samePromise, count: commits.size, recovered, invalidated };
    } finally { window.fetch = originalFetch; }
  });
  assert.equal(api.samePromise, true);
  assert.equal(api.attempts.length, 3);
  assert.equal(api.attempts[0].key, api.attempts[1].key);
  assert.deepEqual(api.attempts[0].body, api.attempts[1].body);
  assert.notEqual(api.attempts[1].key, api.attempts[2].key);
  assert.equal(api.count, 2);
  assert.equal(api.invalidated, true);
  await page.close();
  pass('one frozen intent across duplicate clicks, lost response, nested mutation, and retry; deliberate identical new action remains possible');

  const therapyPayload = { medication: '合成药物A', status: '无法确定', note: '合成A原文', itemDetails: Object.fromEntries(details.map((code, i) => [code, ['5', 'mg', '10mg/ml', 'oral', 'daily', '12', 'hour'][i]])), startTime: emptyTime, endTime: emptyTime, photos: [{ file_id: photoId, name: 'synthetic.png' }], source: 'report_transcription', sourceReference: 'synthetic.png', candidates: [{ name: '合成药物A', source_text: '合成A原文', photo_id: photoId }, { name: '合成药物B', source_text: '合成B原文', photo_id: photoId }], selectedSource: { photo_id: photoId, source_text: '合成A原文' } };
  const therapy = await fixture({ draftPayload: therapyPayload });
  await therapy.page.getByRole('region', { name: '治疗候选 2', exact: true }).getByRole('button').click();
  await expect(therapy.field).toHaveValue('合成药物B');
  for (const label of detailLabels) await expect(therapy.page.getByLabel(label, { exact: true })).toHaveValue('');
  await screenshot(therapy.page, 'therapy-candidate-b-cleared-details', therapy.page.getByLabel('单次剂量', { exact: true }));
  await therapy.page.getByLabel('原文频次', { exact: true }).fill('合成B稀疏频次');
  await therapy.page.getByRole('region', { name: '治疗候选 1', exact: true }).getByRole('button').click();
  await expect(therapy.field).toHaveValue('合成药物A');
  for (const label of detailLabels) await expect(therapy.page.getByLabel(label, { exact: true })).toHaveValue('');
  await therapy.page.getByText('资料出处与确认', { exact: true }).click();
  await therapy.page.getByRole('checkbox').check();
  await therapy.page.getByRole('button', { name: '确认并保存资料', exact: true }).click();
  await therapy.page.waitForURL('**/records');
  const sent = therapy.requests[0].body.fields;
  for (const code of details) assert.equal(sent.find(field => field.field_code === code).value_state, 'unknown');
  assert.equal(sent.find(field => field.field_code === 'medication_name').raw_text, '合成药物A');
  assert.deepEqual(therapy.errors, []);
  await therapy.context.close();
  pass('therapy A→B→A clears all seven details, including sparse B edits, and submits unknown fields');

  for (const kind of ['observation', 'therapy', 'report']) {
    const draftPayload = kind === 'therapy' ? therapyPayload : kind === 'report'
      ? { candidates: [{ name: '合成项目', value: '1', unit: 'x', comparator: '=', reference: null, sampleTime: emptyTime }], photos: [], organization: '', reportRef: '', sampleTime: emptyTime, resultTime: emptyTime, reportStatus: 'unknown' } : undefined;
    const test = await fixture({ kind, draftPayload, loseResponse: true });
    if (kind === 'observation') await test.field.fill('合成观察内容');
    else if (kind === 'therapy') { await test.page.getByText('资料出处与确认', { exact: true }).click(); await test.page.getByRole('checkbox').check(); }
    else await test.page.getByRole('checkbox').check();
    const save = test.page.getByRole('button', { name: kind === 'therapy' ? '确认并保存资料' : '确认并保存', exact: true });
    await save.click();
    await expect(test.page.getByText('尚未确认是否保存成功。已暂停编辑和草稿保存，请重试本次保存以核对结果。', { exact: true })).toBeVisible();
    await expect(test.field).toBeDisabled();
    await screenshot(test.page, `${kind}-lost-response-retry`, test.page.getByRole('button', { name: '重试本次保存', exact: true }));
    const before = test.draftWrites;
    await test.page.clock.fastForward(11000);
    // The real 10-second autosave callback has now had an opportunity to fire.
    await test.page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => resolve())));
    assert.equal(test.draftWrites, before, `${kind}: uncertain submit must pause autosave`);
    if (kind === 'observation') assert.equal(before, 0, 'fresh form must not gain draft metadata after a lost response');
    await test.page.getByRole('button', { name: '重试本次保存', exact: true }).click();
    await test.page.waitForURL('**/records');
    assert.equal(test.requests.length, 2);
    assert.deepEqual(test.requests[0], test.requests[1]);
    assert.equal(test.committed.size, 1);
    assert.deepEqual(test.errors, []);
    await test.context.close();
    pass(`${kind}: lost committed response + 11-second autosave delay + explicit retry produces one record`);
  }

  const rejected = await fixture({ kind: 'observation', rejectFirst: true });
  await rejected.field.fill('合成输入一');
  await rejected.page.getByRole('button', { name: '确认并保存', exact: true }).click();
  await expect(rejected.page.getByText('合成字段校验失败', { exact: true })).toBeVisible();
  await expect(rejected.field).toBeEnabled();
  await rejected.field.fill('合成输入二');
  await rejected.page.getByRole('button', { name: '确认并保存', exact: true }).click();
  await rejected.page.waitForURL('**/records');
  assert.notEqual(rejected.requests[0].key, rejected.requests[1].key);
  assert.equal(rejected.committed.size, 1);
  assert.deepEqual(rejected.errors, []);
  await rejected.context.close();
  pass('definitive rejection unlocks editing and allows a corrected new submission');

  const savingDraft = await fixture({ kind: 'observation', delayDraft: true });
  await savingDraft.field.fill('合成自动草稿');
  await savingDraft.page.clock.fastForward(11000);
  await expect(savingDraft.page.getByRole('button', { name: '正在保存草稿', exact: true })).toBeVisible();
  await expect(savingDraft.page.getByRole('button', { name: '确认并保存', exact: true })).toBeDisabled();
  savingDraft.releaseDraft();
  await expect(savingDraft.page.getByRole('button', { name: '确认并保存', exact: true })).toBeEnabled();
  await savingDraft.page.getByRole('button', { name: '确认并保存', exact: true }).click();
  await savingDraft.page.waitForURL('**/records');
  assert.equal(savingDraft.requests[0].body.draft_id, savingDraft.draftId);
  assert.equal(savingDraft.requests[0].body.draft_version, '1');
  assert.equal(savingDraft.draftWrites, 1);
  assert.deepEqual(savingDraft.errors, []);
  await savingDraft.context.close();
  pass('in-flight autosave blocks submit until its exact saved draft version is available');
  console.log(JSON.stringify({ status: 'PASS', mode: 'MOCKED_SYNTHETIC_BROWSER', checks }, null, 2));
} finally {
  await browser.close();
}
