import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect } from 'playwright/test';

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t55-voice-fixture.json', 'utf8'));
const origin = process.env.SJIA_T55_H5_ORIGIN ?? 'http://127.0.0.1:5187';
const recordId = process.env.SJIA_T55_CORRECTION_RECORD_ID;
const voiceInputId = process.env.SJIA_T55_CORRECTION_VOICE_INPUT_ID;
assert.ok(recordId, '需要准确的模拟 Record 标识');
assert.ok(voiceInputId, '需要准确的模拟 VoiceInput 标识');
assert.equal(fixture.mode, 'SIMULATION_TEXT_ONLY');
const outputPath = '../.planning-work/parallel/window3/T55-correction-browser-roundtrip.json';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
const browserErrors = [];
page.on('pageerror', error => browserErrors.push(error.message));

async function apiJson(method, path, data, csrfToken) {
  const response = await context.request.fetch(origin + path, {
    method,
    ...(data === undefined ? {} : { data }),
    ...(csrfToken ? { headers: { 'X-CSRFToken': csrfToken } } : {}),
  });
  const body = await response.text();
  if (response.status() !== 200) {
    const failure = {
      data_mode: 'SIMULATION', evidence_scope: 'SIMULATION_TEXT_ONLY',
      method, path, http_status: response.status(), response_body: body,
    };
    const failurePath = '../.planning-work/parallel/window3/T55-correction-api-failure.json';
    await writeFile(failurePath, `${JSON.stringify(failure, null, 2)}\n`, 'utf8');
    assert.deepEqual(JSON.parse(await readFile(failurePath, 'utf8')), failure);
    throw new Error(`${method} ${path} returned HTTP ${response.status()}; failure evidence saved and reread`);
  }
  return JSON.parse(body);
}

async function recordCount(patientId, csrfToken) {
  let cursor = null;
  let total = 0;
  do {
    const value = await apiJson('POST', `/api/patients/${patientId}/records/search`,
      { kind: null, limit: 100, cursor }, csrfToken);
    assert.ok(Array.isArray(value.records));
    total += value.records.length;
    cursor = value.next_cursor;
  } while (cursor);
  return total;
}

const checks = [];
const bootstrap = await apiJson('GET', '/api/auth/bootstrap');
const login = await context.request.post(origin + '/api/auth/login', {
  data: { username: fixture.family_username, password: process.env.SJIA_SIMULATION_PASSWORD },
  headers: { 'X-CSRFToken': bootstrap.csrf_token },
});
assert.equal(login.status(), 200);
const session = await apiJson('GET', '/api/session');
assert.equal(session.audience, 'family');
assert.equal(session.principal_id, fixture.owner_id);
const voice = await apiJson('GET', `/api/v2/patients/${fixture.patient_id}/voice-inputs/${voiceInputId}`);
assert.equal(voice.id, voiceInputId);
assert.equal(voice.patient_id, fixture.patient_id);
assert.equal(voice.input_mode, 'SIMULATION_TEXT_ONLY');
assert.equal(voice.original_file_id, fixture.original_file_id);
assert.equal(voice.transcript, fixture.confirmed_text);
assert.ok(['accepted', 'running', 'awaiting_confirmation'].includes(voice.status));
if (voice.status === 'awaiting_confirmation') assert.ok(voice.candidates);
const before = await apiJson('GET', `/api/records/${recordId}`);
assert.equal(before.patient_id, fixture.patient_id);
assert.equal(before.kind, 'observation');
assert.equal(before.version, 1, '模拟更正只运行一次，避免重复建立记录版本');
const noteBefore = before.fields.find(field => field.field_code === 'observation_note');
assert.ok(noteBefore);
const relationResponse = await apiJson('GET', '/api/relations');
const relation = relationResponse.relations.find(item => item.patient_id === fixture.patient_id);
assert.ok(relation, '模拟家庭会话必须读取到对应患儿关联');
const fieldConfig = await apiJson('GET', `/api/patients/${fixture.patient_id}/field-config?kind=observation`);
const correctionAllowedByApi = before.allowed_actions?.includes('correct_record') === true
  && relation.status === 'active' && relation.actions.includes('correct_record')
  && Date.parse(relation.valid_from) <= Date.now()
  && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now());
const permissionEvidence = {
  status: correctionAllowedByApi ? 'SIMULATION_CORRECTION_PERMISSION_AVAILABLE' : 'SIMULATION_CORRECTION_PERMISSION_NOT_GRANTED',
  data_mode: 'SIMULATION',
  evidence_scope: 'SIMULATION_TEXT_ONLY',
  patient_id: fixture.patient_id,
  principal_id: session.principal_id,
  record_id: recordId,
  record_allowed_actions: before.allowed_actions ?? [],
  relation_id: relation.relation_id,
  relation_status: relation.status,
  relation_actions: relation.actions,
  relation_valid_from: relation.valid_from,
  relation_valid_until: relation.valid_until,
  field_config_version: fieldConfig.config_version,
  field_config_checksum: fieldConfig.checksum,
  field_config_state: fieldConfig.state,
  field_config_reason_code: fieldConfig.reason_code ?? null,
  writable_field_codes: fieldConfig.fields.filter(field => field.writable).map(field => field.field_code),
  correction_allowed_by_api: correctionAllowedByApi,
};
const permissionOutput = '../.planning-work/parallel/window3/T55-correction-permission-readback.json';
await writeFile(permissionOutput, `${JSON.stringify(permissionEvidence, null, 2)}\n`, 'utf8');
assert.deepEqual(JSON.parse(await readFile(permissionOutput, 'utf8')), permissionEvidence);
assert.equal(permissionEvidence.correction_allowed_by_api, true,
  `当前模拟家庭没有准确更正权限：${JSON.stringify(permissionEvidence)}`);
assert.equal(fieldConfig.state, 'available', `更正字段配置不可用：${fieldConfig.reason_code ?? '未知原因'}`);
const recordsBefore = await recordCount(fixture.patient_id, session.csrf_token);
checks.push('模拟家庭会话、VoiceInput 候选和现有 Record 均由实际 API 读取');

const textHash = voice.transcript_sha256;
await page.addInitScript(({ key, value }) => sessionStorage.setItem(key, JSON.stringify(value)), {
  key: `sjia.voice-input.${fixture.patient_id}.${textHash}`,
  value: { requestId: voice.request_id, originalFileId: voice.original_file_id },
});
await page.goto(`${origin}/records/${recordId}/correct`);
await expect(page.getByRole('button', { name: '说一说观察到的情况' })).toBeEnabled();
await page.getByRole('button', { name: '说一说观察到的情况' }).click();
await page.getByLabel('语音用途授权来源', { exact: true }).selectOption(fixture.source_revision_id);
await page.getByRole('button', { name: 'SIMULATION_TEXT_ONLY 文字输入', exact: true }).click();
await page.getByLabel('语音转写与文字描述', { exact: true }).fill(fixture.confirmed_text);
await page.getByRole('button', { name: '提取候选并核对', exact: true }).click();
await page.getByRole('button', { name: '提取候选并保存任务', exact: true }).click();
await expect(page.getByRole('combobox', { name: '症状决定', exact: true })).toBeVisible();
await page.getByRole('combobox', { name: '症状决定', exact: true }).selectOption('unknown');
await page.getByRole('combobox', { name: '发生时间决定', exact: true }).selectOption('excluded');
await page.getByRole('combobox', { name: '补充说明决定', exact: true }).selectOption('excluded');
await page.getByRole('button', { name: '确认候选并加入当前表单', exact: true }).click();
await expect(page.getByRole('heading', { name: '语音记录与逐项核对', exact: true })).toHaveCount(0);
const cancelled = await apiJson('GET', `/api/v2/patients/${fixture.patient_id}/voice-inputs/${voiceInputId}`);
assert.equal(cancelled.status, 'cancelled');
assert.equal(cancelled.saved_record_version_id, null);
const unchangedRecord = await apiJson('GET', `/api/records/${recordId}`);
assert.equal(unchangedRecord.record_version_id, before.record_version_id);
assert.equal(unchangedRecord.version, before.version);
assert.equal(await recordCount(fixture.patient_id, session.csrf_token), recordsBefore);
await page.getByText('当前记录全部字段与独立时间', { exact: true }).click();
const noteFieldset = page.locator('fieldset').filter({ has: page.getByRole('heading', {
  name: noteBefore.original_name ?? '观察说明', exact: true,
}) });
const noteInput = noteFieldset.getByLabel('原始内容', { exact: true });
assert.equal(await noteInput.inputValue(), noteBefore.raw_text ?? '',
  'unknown/excluded 候选不得改变 observation_note 表单值');
checks.push('unknown/excluded 候选没有写入 Record 表单，VoiceInput 取消状态已由 API 重读');

await page.getByLabel('更正原因', { exact: true }).fill('模拟语音候选确认后更正既有观察记录');
await page.getByRole('button', { name: '保存更正', exact: true }).click();
await page.waitForURL(new RegExp(`/records/${recordId}$`));
const saved = await apiJson('GET', `/api/records/${recordId}`);
const previousVersion = await apiJson('GET', `/api/record-versions/${before.record_version_id}`);
assert.equal(saved.record_id, recordId);
assert.equal(saved.version, before.version + 1);
assert.equal(saved.correction_reason, '模拟语音候选确认后更正既有观察记录');
assert.equal(previousVersion.record_version_id, before.record_version_id);
assert.deepEqual(previousVersion.fields, before.fields);
assert.equal(saved.fields.find(field => field.field_code === 'observation_note').raw_text, noteBefore.raw_text);
assert.equal(await recordCount(fixture.patient_id, session.csrf_token), recordsBefore);
assert.deepEqual(browserErrors, []);
checks.push('更正保存为原 Record 的新版本，旧版本和全部 Record 数量均已重新读取核对');

const report = {
  data_mode: 'SIMULATION',
  evidence_scope: 'SIMULATION_TEXT_ONLY',
  status: 'SIMULATION_VOICE_CORRECTION_ROUNDTRIP_PASS',
  checked_at: new Date().toISOString(),
  patient_id: fixture.patient_id,
  record_id: recordId,
  previous_record_version_id: before.record_version_id,
  previous_version: before.version,
  new_record_version_id: saved.record_version_id,
  new_version: saved.version,
  voice_input_id: voiceInputId,
  voice_request_id: voice.request_id,
  voice_status_after_form_adoption: cancelled.status,
  record_count_before: recordsBefore,
  record_count_after: await recordCount(fixture.patient_id, session.csrf_token),
  old_version_readable: previousVersion.record_version_id === before.record_version_id,
  unknown_and_excluded_fields_preserved: true,
  browser_errors: browserErrors,
  checks,
  real_audio: 'NOT_RUN',
  clinical_validation: 'NOT_RUN',
  family_comprehension: 'NOT_RUN',
  visual_review: 'NOT_RUN',
};
await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
assert.deepEqual(JSON.parse(await readFile(outputPath, 'utf8')), report);
process.stdout.write(`PASS ${JSON.stringify({ status: report.status, record_id: recordId,
  new_record_version_id: saved.record_version_id, voice_input_id: voiceInputId, outputPath })}\n`);
await context.close();
await browser.close();
