import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '../../node_modules/playwright/index.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..');
const origin = process.env.SJIA_H5_BASE_URL ?? 'http://127.0.0.1:5184';
const requestedPatientId = process.env.SJIA_T30_PATIENT_ID ?? null;
let patientId = requestedPatientId;
const username = process.env.SJIA_SIMULATION_USERNAME ?? 'simulation-family';
assert.ok(process.env.SJIA_SIMULATION_PASSWORD, '缺少模拟账号密码环境变量');

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const browserVersion = browser.version();
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN', hasTouch: true, isMobile: true });
const page = await context.newPage();
const runId = randomUUID();
let marker = `SIMULATION_ONLY-T30-T58-${runId}`;
const checks = [];
const pageErrors = [];
const writeIntents = [];
const writeIntentCsrfTokens = [];
const recordPosts = [];
const patientSelectionResponses = [];
const observationConfigRequests = [];
const observationConfigResponses = [];
page.on('pageerror', error => pageErrors.push(error.message));
page.on('request', request => {
  if (request.method() === 'POST' && request.url().endsWith('/api/write-intents')) {
    writeIntents.push(request.postDataJSON());
    writeIntentCsrfTokens.push(request.headers()['x-csrftoken']);
  }
  if (request.method() === 'POST' && request.url().endsWith(`/api/patients/${patientId}/records`)) recordPosts.push(request);
  if (request.url().includes('/field-config?kind=observation')) observationConfigRequests.push({ method: request.method(), url: request.url() });
});
page.on('response', response => {
  if (response.url().includes('/field-config?kind=observation')) observationConfigResponses.push({ status: response.status(), url: response.url() });
});

async function apiGet(path) {
  return page.evaluate(async requestPath => {
    const response = await fetch(requestPath, { credentials: 'same-origin', cache: 'no-store' });
    const text = await response.text();
    const body = response.headers.get('content-type')?.includes('application/json')
      ? JSON.parse(text) : { response_text: text.slice(0, 2000) };
    return { status: response.status, body };
  }, path);
}

async function saveEarlyReport(status, check) {
  checks.push(check);
  const runAt = new Date().toISOString();
  const passCount = checks.filter(value => value.status === 'PASS').length;
  const notRunCount = checks.filter(value => value.status === 'NOT_RUN').length;
  const failedChecks = checks.filter(value => value.status === 'FAIL').length;
  const report = { run_id: runId, mode: 'SIMULATION_ONLY', status, run_at: runAt,
    task_ids: ['T30', 'T58'], origin, patient_id: patientId, marker, browser: browserVersion,
    runtime: { node: process.version, playwright: '1.63.0', viewport: { width: 390, height: 844 } },
    totals: { pass: passCount, not_run: notRunCount, fail: failedChecks, page_errors: pageErrors.length }, checks,
    page_errors: pageErrors };
  const outputPath = resolve(root, '.planning-work/parallel/window2/evidence/business-state-device-check-' + runAt.replaceAll(':', '-') + '.json');
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  await browser.close();
  process.exit(status === 'FAIL' ? 1 : 2);
}

async function recordRead(recordId) {
  const response = await apiGet(`/api/records/${recordId}`);
  if (response.status !== 200) {
    await saveEarlyReport('FAIL', { id: 'RECORD_API_REREAD', status: 'FAIL', record_id: recordId,
      http_status: response.status, response_body: response.body,
      reason: '模拟记录已保存并完成原请求重试，但记录详情 API 回读没有返回 HTTP 200。' });
  }
  assert.equal(response.status, 200);
  assert.equal(response.body.record_id, recordId);
  assert.equal(response.body.patient_id, patientId);
  return response.body;
}

async function waitForDraftText(expectedValue) {
  await page.waitForFunction(value => document.querySelector('[data-screen-id]')?.shadowRoot
    ?.querySelector('textarea')?.value === value, expectedValue);
}

async function screenText() {
  return page.locator('[data-screen-id]').evaluate(element => element.shadowRoot?.textContent ?? '');
}

async function choosePatientThroughAssociations(relation) {
  await page.goto(`${origin}/associations`, { waitUntil: 'domcontentloaded' });
  const card = page.locator('section.card').filter({ hasText: relation.display_id });
  const selectionResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/patient-selection')
    && response.request().method() === 'POST');
  await card.getByRole('button', { name: '选择此患儿', exact: true }).click();
  const selectionResponse = await selectionResponsePromise;
  const selectionBody = await selectionResponse.json();
  if (selectionResponse.status() !== 200 || selectionBody.selected_patient_id !== relation.patient_id) {
    await saveEarlyReport('FAIL', { id: 'PATIENT_SELECTION_SESSION_SAVE', status: 'FAIL',
      expected_patient_id: relation.patient_id, http_status: selectionResponse.status(), response_body: selectionBody,
      reason: '关联页面选择按钮未取得准确的服务端 Session 保存回执；页面没有继续导航。' });
  }
  patientSelectionResponses.push({ control: 'associations.select_patient_button', patient_id: relation.patient_id,
    http_status: selectionResponse.status(), selected_patient_id: selectionBody.selected_patient_id });
  await page.waitForURL('**/home');
  const selector = page.getByLabel('患儿关联');
  await selector.waitFor({ state: 'visible' });
  assert.equal(await selector.inputValue(), relation.patient_id);
  assert.ok((await screenText()).includes(`${relation.display_id} · 查看本人获准的资料`));
}

await page.goto(`${origin}/login`, { waitUntil: 'domcontentloaded' });
await page.getByRole('form', { name: '登录账号' }).waitFor({ state: 'visible' });
await page.locator('input[name="username"]').fill(username);
await page.locator('input[name="password"]').fill(process.env.SJIA_SIMULATION_PASSWORD);
const loginResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/auth/login')
  && response.request().method() === 'POST');
await page.getByRole('button', { name: '登录账号', exact: true }).click();
const loginResponse = await loginResponsePromise;
if (loginResponse.status() !== 200) {
  const loginFailure = await loginResponse.json();
  await browser.close();
  const runAt = new Date().toISOString();
  const report = { run_id: runId, mode: 'SIMULATION_ONLY', status: 'BLOCKED', run_at: runAt,
    task_ids: ['T30', 'T58'], origin, browser: browserVersion, runtime: { node: process.version, playwright: '1.63.0' },
    totals: { pass: 0, not_run: 1, fail: 0, blocked: 1, page_errors: pageErrors.length },
    checks: [{ id: 'SIMULATION_LOGIN_PRECONDITION', status: 'BLOCKED', http_status: loginResponse.status(),
      reason_code: loginFailure.code ?? loginFailure.error ?? 'LOGIN_REJECTED',
      reason: '模拟账号未能登录；本次没有访问业务字段配置或执行记录写入。' }], page_errors: pageErrors };
  const outputPath = resolve(root, '.planning-work/parallel/window2/evidence/business-state-device-check-' + runAt.replaceAll(':', '-') + '.json');
  await mkdir(dirname(outputPath), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
  process.exit(2);
}
await page.waitForURL('**/home');
const relationResponse = await apiGet('/api/relations');
assert.equal(relationResponse.status, 200);
const relations = relationResponse.body.relations;
const requiredActions = ['write_record', 'manage_draft', 'correct_record', 'read_history', 'view_request'];
const relation = requestedPatientId
  ? relations.find(value => value.patient_id === requestedPatientId)
  : relations.find(value => value.status === 'active' && requiredActions.every(action => value.actions.includes(action)));
assert.ok(relation);
patientId = relation.patient_id;
for (const action of requiredActions) {
  assert.ok(relation.actions.includes(action), `模拟关联缺少本流程所需权限：${action}`);
}
checks.push({ id: 'SIMULATION_LOGIN_AND_WRITE_RELATION', status: 'PASS', patient_id: patientId,
  relation_id: relation.relation_id, allowed_actions: relation.actions });

await page.goto(`${origin}/home`, { waitUntil: 'domcontentloaded' });
const patientSelector = page.getByLabel('患儿关联');
await patientSelector.waitFor({ state: 'visible' });
let selectedPatientId = await patientSelector.inputValue();
const otherRelation = relations.find(value => value.status === 'active' && value.patient_id !== patientId);
const selectionPath = [];
if (selectedPatientId === patientId && username === 'simulation-family-multi') {
  assert.ok(otherRelation, '双患儿模拟账号缺少另一项有效关联');
  await choosePatientThroughAssociations(otherRelation);
  selectedPatientId = otherRelation.patient_id;
  selectionPath.push(otherRelation.patient_id);
}
if (selectedPatientId !== patientId) {
  await choosePatientThroughAssociations(relation);
  selectionPath.push(patientId);
}
assert.equal(await page.getByLabel('患儿关联').inputValue(), patientId);
assert.ok((await screenText()).includes(`${relation.display_id} · 查看本人获准的资料`));
checks.push({ id: 'PATIENT_SELECTED_IN_H5_UI', status: 'PASS', patient_id: patientId,
  relation_id: relation.relation_id, display_id: relation.display_id,
  selection_control: 'associations.select_patient_button', selection_path: selectionPath });

const configResponsePromise = page.waitForResponse(response => response.url().includes(`/api/patients/${patientId}/field-config?kind=observation`));
await page.goto(`${origin}/register/observations`, { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => {
  const patientName = document.querySelector('[data-screen-id]')?.shadowRoot?.querySelector('.patient strong')?.textContent?.trim();
  return Boolean(patientName);
});
const patientDisplayAfterDocumentNavigation = await page.locator('[data-screen-id]').evaluate(element =>
  element.shadowRoot?.querySelector('.patient strong')?.textContent?.trim() ?? null);
if (patientDisplayAfterDocumentNavigation !== relation.display_id) {
  await saveEarlyReport('FAIL', { id: 'PATIENT_SELECTION_AFTER_FULL_PAGE_NAVIGATION', status: 'FAIL',
    expected_patient_id: patientId, expected_display_id: relation.display_id,
    visible_display_id: patientDisplayAfterDocumentNavigation, observation_config_requests: observationConfigRequests,
    observation_config_responses: observationConfigResponses,
    reason: '整页导航后家庭页面的当前患儿与刚才通过关联页面选择的患儿不同；本次没有填写或保存资料。' });
}
checks.push({ id: 'PATIENT_SELECTION_RESTORED_AFTER_FULL_PAGE_NAVIGATION', status: 'PASS',
  patient_id: patientId, display_id: relation.display_id,
  visible_display_id: patientDisplayAfterDocumentNavigation, selection_control: 'associations.select_patient_button' });
const configResponse = await configResponsePromise;
assert.equal(configResponse.status(), 200);
const fieldConfig = await configResponse.json();
const selectedSession = await apiGet('/api/session');
assert.equal(selectedSession.status, 200);
assert.equal(selectedSession.body.selected_patient_id, patientId);
assert.equal(fieldConfig.patient_id, patientId);
assert.equal(fieldConfig.kind, 'observation');
assert.equal(fieldConfig.state, 'available');
assert.ok(fieldConfig.fields.some(field => field.field_code === 'observation_note' && field.value_type === 'text' && field.writable),
  '模拟 A 关系尚未取得 observation_note 的精确写入授权');
checks.push({ id: 'PATIENT_SELECTION_MATCHES_FIELD_REQUEST_AND_WRITE_GRANT', status: 'PASS',
  patient_id: patientId, field_config_http_status: configResponse.status(), field_config_state: fieldConfig.state,
  visible_display_after_full_page_navigation: patientDisplayAfterDocumentNavigation,
  session_selected_patient_id: selectedSession.body.selected_patient_id,
  observation_config_requests: observationConfigRequests, observation_config_responses: observationConfigResponses,
  writable_field_codes: fieldConfig.fields.filter(field => field.writable).map(field => field.field_code) });
checks.push({ id: 'PATIENT_SELECTION_POST_AND_SESSION_READBACK', status: 'PASS', responses: patientSelectionResponses,
  session_selected_patient_id: selectedSession.body.selected_patient_id });
const noteInput = page.getByLabel('补充说明');
await noteInput.waitFor({ state: 'visible' });

await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
const darkMotionStyle = await page.evaluate(() => {
  const shadow = document.querySelector('[data-screen-id]')?.shadowRoot;
  const shell = shadow?.querySelector('.device');
  const action = shadow?.querySelector('.button');
  const input = shadow?.querySelector('textarea');
  if (!shell || !action || !input) throw new Error('观察页面缺少深色状态检查节点');
  const reducedMotionRule = [...shadow.styleSheets].flatMap(sheet => [...sheet.cssRules])
    .find(rule => rule instanceof CSSMediaRule && rule.conditionText.includes('prefers-reduced-motion: reduce'));
  const rgb = value => {
    const match = value.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/);
    if (!match) throw new Error(`无法计算颜色值：${value}`);
    return match.slice(1, 4).map(Number);
  };
  const luminance = value => rgb(value).map(channel => channel / 255).map(channel =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4)
    .reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
  const ratio = (foreground, background) => {
    const values = [luminance(foreground), luminance(background)].sort((left, right) => right - left);
    return Number(((values[0] + 0.05) / (values[1] + 0.05)).toFixed(2));
  };
  const shellStyle = getComputedStyle(shell);
  const inputStyle = getComputedStyle(input);
  const buttonStyle = getComputedStyle(action);
  const buttonBackgrounds = [...buttonStyle.backgroundImage.matchAll(/rgb\([^)]*\)|#([0-9a-f]{6})/gi)].map(match => {
    if (match[0].startsWith('rgb')) return match[0];
    const hex = match[1];
    return `rgb(${parseInt(hex.slice(0, 2), 16)}, ${parseInt(hex.slice(2, 4), 16)}, ${parseInt(hex.slice(4, 6), 16)})`;
  });
  const controls = [...shell.querySelectorAll('button, a, input, select, textarea')]
    .filter(element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
    }).map(element => ({ tag: element.tagName.toLowerCase(), accessible_label: element.getAttribute('aria-label')
      ?? element.innerText?.trim() ?? element.getAttribute('name'), width: element.getBoundingClientRect().width,
    height: element.getBoundingClientRect().height, disabled: Boolean(element.disabled) }));
  const values = { ink: shellStyle.getPropertyValue('--ink').trim(), canvas: shellStyle.getPropertyValue('--canvas').trim(),
    navy: shellStyle.getPropertyValue('--navy').trim(), blue: shellStyle.getPropertyValue('--blue').trim(),
    link: shellStyle.getPropertyValue('--link').trim() };
  const tokenColor = value => {
    const hex = value.match(/^#([0-9a-f]{6})$/i)?.[1];
    if (!hex) throw new Error(`语义 Token 颜色格式不可识别：${value}`);
    return `rgb(${parseInt(hex.slice(0, 2), 16)}, ${parseInt(hex.slice(2, 4), 16)}, ${parseInt(hex.slice(4, 6), 16)})`;
  };
  const surface = tokenColor(values.canvas);
  const white = 'rgb(255, 255, 255)';
  const ink = tokenColor(values.ink);
  const link = tokenColor(values.link);
  const buttonText = buttonStyle.color;
  return { page_background: shellStyle.backgroundColor, page_text: getComputedStyle(shell).color,
    input_background: inputStyle.backgroundColor, input_text: inputStyle.color,
    reduced_motion_matches: matchMedia('(prefers-reduced-motion: reduce)').matches,
    reduced_motion_rule_present: Boolean(reducedMotionRule), transition_duration: getComputedStyle(action).transitionDuration,
    animation_duration: getComputedStyle(action).animationDuration,
    semantic_tokens: values,
    visible_controls: controls,
    content_visible: shell.innerText.includes('补充说明') && shell.innerText.includes('保存为草稿'),
    contrast_ratios: { body_on_canvas: ratio(ink, surface), link_on_white: ratio(link, white),
      primary_button_text_on_gradient_stops: buttonBackgrounds.map(background => ratio(buttonText, background)),
      input_text_on_input_background: ratio(inputStyle.color, inputStyle.backgroundColor) } };
});
assert.equal(darkMotionStyle.semantic_tokens.ink.toLowerCase(), '#243b55');
assert.equal(darkMotionStyle.semantic_tokens.canvas.toLowerCase(), '#f1f5fa');
assert.equal(darkMotionStyle.semantic_tokens.navy.toLowerCase(), '#173f70');
assert.equal(darkMotionStyle.semantic_tokens.blue.toLowerCase(), '#2369ae');
assert.equal(darkMotionStyle.semantic_tokens.link.toLowerCase(), '#2e689e');
assert.ok(darkMotionStyle.content_visible);
assert.ok(darkMotionStyle.visible_controls.length > 0);
assert.ok(darkMotionStyle.visible_controls.every(control => control.width > 0 && control.height > 0));
assert.ok(darkMotionStyle.contrast_ratios.body_on_canvas >= 4.5);
assert.ok(darkMotionStyle.contrast_ratios.link_on_white >= 4.5);
assert.ok(darkMotionStyle.contrast_ratios.primary_button_text_on_gradient_stops.length > 0);
assert.ok(darkMotionStyle.contrast_ratios.primary_button_text_on_gradient_stops.every(value => value >= 4.5));
assert.ok(darkMotionStyle.contrast_ratios.input_text_on_input_background >= 4.5);
checks.push({ id: 'DARK_MODE_CONTENT_OPERABILITY_TOKENS_AND_CONTRAST', status: 'PASS', ...darkMotionStyle,
  token_source: 'ui-delivery/v2/tokens.json 与前端 LLD 第 3.1 节', contrast_threshold: 4.5 });
checks.push({ id: 'DARK_MODE_VISUAL_THEME', status: 'NOT_RUN',
  reason: '前端 LLD 要求完整继承 UI2 原稿颜色模式且不新增主题；现有设计未提供深色 Token，桌面模拟只核验 DOM、操作、当前 Token 与对比度数值。' });
assert.ok(darkMotionStyle.reduced_motion_matches);
assert.ok(darkMotionStyle.reduced_motion_rule_present);
checks.push({ id: 'REDUCED_MOTION', status: 'PASS', media_query_matches: darkMotionStyle.reduced_motion_matches,
  application_rule_present: darkMotionStyle.reduced_motion_rule_present,
  transition_duration: darkMotionStyle.transition_duration, animation_duration: darkMotionStyle.animation_duration });
await page.emulateMedia({ colorScheme: 'light', reducedMotion: 'no-preference' });
if (fieldConfig.state !== 'available' || !fieldConfig.fields.some(field => field.field_code === 'observation_note' && field.writable)) {
  await saveEarlyReport('BLOCKED', { id: 'OBSERVATION_WRITE_CONFIGURATION', status: 'BLOCKED',
    http_status: configResponse.status(), config_version: fieldConfig.config_version ?? null,
    state: fieldConfig.state, reason_code: fieldConfig.reason_code ?? null,
    writable_fields: fieldConfig.fields.filter(field => field.writable).map(field => field.field_code),
    reason: '模拟账号当前无法写入观察字段；本次未新建草稿或正式记录。' });
}

let draftId = process.env.SJIA_T30_RESUME_DRAFT_ID ?? null;
if (draftId) {
  const existingDraft = await apiGet(`/api/drafts/${draftId}`);
  if (existingDraft.status !== 200) {
    await saveEarlyReport('BLOCKED', { id: 'DRAFT_EXISTING_API_REREAD', status: 'BLOCKED', draft_id: draftId,
      http_status: existingDraft.status, response_body: existingDraft.body,
      reason: '已保存模拟草稿的 API 回读仍然失败；程序已保存准确 HTTP 状态和响应正文。' });
  }
  assert.equal(existingDraft.body.patient_id, patientId);
  assert.equal(existingDraft.body.form_type, 'observation');
  assert.equal(existingDraft.body.status, 'editing');
  assert.ok(existingDraft.body.payload.description.startsWith('SIMULATION_ONLY-T30-T58-'));
  marker = existingDraft.body.payload.description;
  await page.goto(`${origin}/register/observations?draft=${encodeURIComponent(draftId)}`, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('补充说明').waitFor({ state: 'visible' });
  await waitForDraftText(marker);
  assert.equal(await page.getByLabel('补充说明').inputValue(), marker);
} else {
  await noteInput.fill(marker);
  await page.getByRole('button', { name: '保存为草稿', exact: true }).click();
  await page.getByText(/草稿保存时间：/).waitFor({ state: 'visible' });
  const draftUrl = new URL(page.url());
  draftId = draftUrl.searchParams.get('draft');
  assert.ok(draftId);
}
const savedDraft = await apiGet(`/api/drafts/${draftId}`);
if (savedDraft.status !== 200) {
  await saveEarlyReport('BLOCKED', { id: 'DRAFT_SAVE_API_REREAD', status: 'BLOCKED', draft_id: draftId,
    http_status: savedDraft.status, response_body: savedDraft.body,
    reason: '草稿已通过家庭页面写入，但 API 回读失败；已保存准确 HTTP 状态和响应正文。' });
}
assert.equal(savedDraft.body.patient_id, patientId);
assert.equal(savedDraft.body.form_type, 'observation');
assert.equal(savedDraft.body.status, 'editing');
assert.equal(savedDraft.body.payload.description, marker);
const draftVersion = savedDraft.body.version;
const draftReadCountBeforeRefresh = pageErrors.length;
await page.reload({ waitUntil: 'domcontentloaded' });
await page.getByLabel('补充说明').waitFor({ state: 'visible' });
await waitForDraftText(marker);
assert.equal(await page.getByLabel('补充说明').inputValue(), marker);
const restoredDraft = await apiGet(`/api/drafts/${draftId}`);
assert.equal(restoredDraft.status, 200);
assert.equal(restoredDraft.body.version, draftVersion);
checks.push({ id: 'DRAFT_SAVE_REFRESH_RESTORE', status: 'PASS', draft_id: draftId,
  draft_version: draftVersion, read_after_refresh_status: restoredDraft.status,
  restored_value_matches: restoredDraft.body.payload.description === marker, page_errors_before_refresh: draftReadCountBeforeRefresh });

const unsavedValue = `${marker}-UNSAVED`;
await page.getByLabel('补充说明').fill(unsavedValue);
await page.getByText(/当前修改尚未保存/).waitFor({ state: 'visible' });
let navigationDialog = null;
page.once('dialog', async dialog => {
  navigationDialog = { type: dialog.type(), message: dialog.message() };
  await dialog.dismiss();
});
await page.getByRole('button', { name: '返回上级页面' }).click();
if (navigationDialog) {
  assert.equal(navigationDialog.type, 'confirm');
  assert.match(navigationDialog.message, /当前内容尚未全部保存/);
  assert.equal(new URL(page.url()).pathname, '/register/observations');
  assert.equal(await page.getByLabel('补充说明').inputValue(), unsavedValue);
  checks.push({ id: 'UNSAVED_INPUT_NAVIGATION_GUARD', status: 'PASS', dialog: navigationDialog,
    unsaved_input_retained_after_dismiss: true });
} else {
  checks.push({ id: 'UNSAVED_INPUT_NAVIGATION_GUARD', status: 'FAIL', dialog: null,
    url_after_navigation: page.url(), unsaved_input_retained: new URL(page.url()).pathname === '/register/observations'
      ? await page.getByLabel('补充说明').inputValue() === unsavedValue : false,
    affected_file: 'guardian-h5/src/state/useFormDraft.tsx',
    issue: '草稿显示存在未保存修改时，返回上级页面没有出现预期的离页确认。' });
}
if (new URL(page.url()).pathname === '/register/observations') {
  await page.getByLabel('补充说明').fill(marker);
  await page.getByText(/当前修改尚未保存/).waitFor({ state: 'detached' });
}

await page.goto(`${origin}/register/observations?draft=${encodeURIComponent(draftId)}`, { waitUntil: 'domcontentloaded' });
await page.getByLabel('补充说明').waitFor({ state: 'visible' });
await waitForDraftText(marker);
assert.equal(await page.getByLabel('补充说明').inputValue(), marker);
let firstCommitReceipt = null;
let firstPostIntentId = null;
let releaseFirstResponse;
const firstResponseGate = new Promise(resolve => { releaseFirstResponse = resolve; });
let announceServerCommit;
const serverCommitReached = new Promise(resolve => { announceServerCommit = resolve; });
await page.route(`${origin}/api/patients/${patientId}/records`, async route => {
  const response = await route.fetch();
  firstCommitReceipt = await response.json();
  firstPostIntentId = route.request().headers()['x-write-intent'];
  assert.equal(response.status(), 201);
  announceServerCommit();
  await firstResponseGate;
  await route.abort('failed');
});
const initialWriteIntentCount = writeIntents.length;
const firstSaveClick = page.getByRole('button', { name: '确认并保存', exact: true }).click();
await serverCommitReached;
const processingLabel = await page.getByRole('button', { name: '正在保存', exact: true }).innerText();
assert.equal(processingLabel, '正在保存');
releaseFirstResponse();
await firstSaveClick;
await page.getByRole('alert').waitFor({ state: 'visible' });
assert.ok(firstCommitReceipt?.record_id);
assert.ok(firstPostIntentId);
assert.equal(new URL(page.url()).pathname, '/register/observations');
assert.equal(await page.getByLabel('补充说明').inputValue(), marker);
await page.unroute(`${origin}/api/patients/${patientId}/records`);
const firstIntent = writeIntents[initialWriteIntentCount];
assert.ok(firstIntent?.request_id);
assert.equal(firstIntent.request_id, recordPosts[0].headers()['idempotency-key']);
assert.equal((await recordRead(firstCommitReceipt.record_id)).version, 1);
const unknownRequest = await apiGet(`/api/requests/${firstPostIntentId}`);
assert.equal(unknownRequest.status, 200);
assert.equal(unknownRequest.body.status, 'committed');
assert.equal(unknownRequest.body.resource_id, firstCommitReceipt.record_id);
checks.push({ id: 'COMMIT_WITH_LOST_CLIENT_RESPONSE_AND_REQUEST_QUERY', status: 'PASS',
  request_id: firstIntent.request_id, intent_id: firstPostIntentId, simulated_client_response: 'aborted_after_actual_http_201',
  processing_label_while_actual_commit_response_was_held: processingLabel,
  actual_server_status: unknownRequest.body.status, record_id: firstCommitReceipt.record_id,
  record_version_id: firstCommitReceipt.record_version_id, version: 1 });

const retryIntentStart = writeIntents.length;
const retryRecordStart = recordPosts.length;
const retryReceiptPromise = page.waitForResponse(response => response.url().endsWith(`/api/patients/${patientId}/records`)
  && response.request().method() === 'POST');
await page.getByRole('button', { name: '确认并保存', exact: true }).click();
await page.waitForURL('**/records');
const retryReceiptResponse = await retryReceiptPromise;
assert.ok([200, 201].includes(retryReceiptResponse.status()));
const retryReceipt = await retryReceiptResponse.json();
assert.equal(retryReceipt.record_id, firstCommitReceipt.record_id);
assert.equal(retryReceipt.record_version_id, firstCommitReceipt.record_version_id);
assert.equal(writeIntents.length - retryIntentStart, 1);
assert.equal(writeIntents[retryIntentStart].request_id, firstIntent.request_id);
assert.equal(recordPosts.length - retryRecordStart, 1);
const committedRecord = await recordRead(firstCommitReceipt.record_id);
assert.equal(committedRecord.version, 1);
assert.equal(committedRecord.fields.filter(field => field.raw_text === marker).length, 1);
assert.ok(writeIntentCsrfTokens[0]);
const duplicateSearch = await page.evaluate(async ({ patientId: selectedPatientId, csrfToken }) => {
  const response = await fetch(`/api/patients/${selectedPatientId}/records/search`, { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
    body: JSON.stringify({ kind: 'observation', limit: 100 }) });
  return { status: response.status, body: await response.json() };
}, { patientId, csrfToken: writeIntentCsrfTokens[0] });
assert.equal(duplicateSearch.status, 200);
const markerMatches = duplicateSearch.body.records.filter(item => item.detail.fields.some(field => field.raw_text === marker));
assert.equal(markerMatches.length, 1);
assert.equal(markerMatches[0].record_id, firstCommitReceipt.record_id);
checks.push({ id: 'ACTUAL_RETRY_REUSES_REQUEST_AND_SINGLE_RECORD', status: 'PASS', request_id: firstIntent.request_id,
  first_actual_record_status: 201, retry_actual_record_status: retryReceiptResponse.status(),
  first_record_id: firstCommitReceipt.record_id, retry_record_id: retryReceipt.record_id,
  resulting_record_version: committedRecord.version, exact_marker_record_count: markerMatches.length });

await page.goto(`${origin}/records/${firstCommitReceipt.record_id}`, { waitUntil: 'domcontentloaded' });
await page.getByRole('heading', { name: '体温症状' }).waitFor({ state: 'visible' });
assert.ok((await screenText()).includes(marker));
const beforeCorrection = await recordRead(firstCommitReceipt.record_id);
assert.equal(beforeCorrection.version, 1);
await page.getByRole('link', { name: '更正记录' }).click();
await page.waitForURL(`**/records/${firstCommitReceipt.record_id}/correct`);
await page.getByLabel('更正原因').fill(`${marker}-CORRECTION`);
await page.getByText('当前记录全部字段与独立时间', { exact: true }).click();
const correctionInput = page.locator('fieldset[aria-label="观察说明"]').getByLabel('原始内容');
await correctionInput.fill(`${marker}-CORRECTED`);
const correctionResponsePromise = page.waitForResponse(response => response.url().endsWith(`/api/records/${firstCommitReceipt.record_id}/corrections`)
  && response.request().method() === 'POST');
await page.getByRole('button', { name: '保存更正', exact: true }).click();
await page.waitForURL(`**/records/${firstCommitReceipt.record_id}`);
const correctionResponse = await correctionResponsePromise;
assert.equal(correctionResponse.status(), 201);
const correctionReceipt = await correctionResponse.json();
assert.equal(correctionReceipt.record_id, firstCommitReceipt.record_id);
assert.equal(correctionReceipt.version, 2);
const correctedRecord = await recordRead(firstCommitReceipt.record_id);
assert.equal(correctedRecord.version, 2);
assert.ok(correctedRecord.fields.some(field => field.raw_text === `${marker}-CORRECTED`));
const historyResult = await apiGet(`/api/records/${firstCommitReceipt.record_id}/history`);
assert.equal(historyResult.status, 200);
assert.equal(historyResult.body.versions.length, 2);
checks.push({ id: 'CORRECTION_VERSION_AND_HISTORY_REREAD', status: 'PASS', record_id: firstCommitReceipt.record_id,
  previous_version: 1, current_version: correctedRecord.version, correction_version_id: correctionReceipt.record_version_id,
  history_versions: historyResult.body.versions.map(version => version.version), corrected_value_reread: true });

const relationPatients = relations.filter(value => value.status === 'active' && value.patient_id !== patientId
  && value.actions.includes('read_file'));
const secondRelation = relationPatients[0];
if (secondRelation) {
  await page.goto(`${origin}/home`, { waitUntil: 'domcontentloaded' });
  const patientDropdown = page.getByLabel('患儿关联');
  await patientDropdown.waitFor({ state: 'visible' });
  const homeSelectionResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/patient-selection')
    && response.request().method() === 'POST');
  await patientDropdown.selectOption(secondRelation.patient_id);
  const homeSelectionResponse = await homeSelectionResponsePromise;
  const homeSelectionBody = await homeSelectionResponse.json();
  if (homeSelectionResponse.status() !== 200 || homeSelectionBody.selected_patient_id !== secondRelation.patient_id) {
    await saveEarlyReport('FAIL', { id: 'HOME_PATIENT_SELECTION_SESSION_SAVE', status: 'FAIL',
      expected_patient_id: secondRelation.patient_id, http_status: homeSelectionResponse.status(), response_body: homeSelectionBody,
      reason: '首页下拉框未取得准确的服务端 Session 保存回执。' });
  }
  await page.waitForFunction(expected => document.querySelector('[data-screen-id]')?.shadowRoot
    ?.querySelector('select')?.value === expected, secondRelation.patient_id);
  const homeSelectedSession = await apiGet('/api/session');
  assert.equal(homeSelectedSession.status, 200);
  assert.equal(homeSelectedSession.body.selected_patient_id, secondRelation.patient_id);
  patientSelectionResponses.push({ control: 'home.patient_selector', patient_id: secondRelation.patient_id,
    http_status: homeSelectionResponse.status(), selected_patient_id: homeSelectionBody.selected_patient_id });
  checks.push({ id: 'HOME_PATIENT_SELECTION_POST_AND_SESSION_READBACK', status: 'PASS',
    http_status: homeSelectionResponse.status(), session_selected_patient_id: homeSelectedSession.body.selected_patient_id });
  await page.goto(`${origin}/records/${firstCommitReceipt.record_id}`, { waitUntil: 'domcontentloaded' });
  await page.getByText(/记录所属患儿与当前选择不一致/).waitFor({ state: 'visible' });
  const crossPatientBody = await screenText();
  assert.equal(crossPatientBody.includes(`${marker}-CORRECTED`), false);
  const originalPatientRecord = await apiGet(`/api/records/${firstCommitReceipt.record_id}`);
  assert.equal(originalPatientRecord.status, 200);
  assert.equal(originalPatientRecord.body.patient_id, patientId);
  checks.push({ id: 'CROSS_PATIENT_PAGE_CONTENT_CLEANUP', status: 'PASS', selected_patient_id: secondRelation.patient_id,
    original_record_server_read_status: originalPatientRecord.status, record_patient_id: originalPatientRecord.body.patient_id,
    mismatched_record_content_visible: false, marker_visible: false });
  const returnAssociationsPromise = page.waitForResponse(response => response.url().endsWith('/api/relations'));
  await page.goto(`${origin}/associations`, { waitUntil: 'domcontentloaded' });
  await returnAssociationsPromise;
  const originalRelationCard = page.locator('section.card').filter({ hasText: relation.display_id });
  const originalSelectionResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/patient-selection')
    && response.request().method() === 'POST');
  await originalRelationCard.getByRole('button', { name: '选择此患儿' }).click();
  const originalSelectionResponse = await originalSelectionResponsePromise;
  const originalSelectionBody = await originalSelectionResponse.json();
  assert.equal(originalSelectionResponse.status(), 200);
  assert.equal(originalSelectionBody.selected_patient_id, patientId);
  patientSelectionResponses.push({ control: 'associations.select_patient_button', patient_id: patientId,
    http_status: originalSelectionResponse.status(), selected_patient_id: originalSelectionBody.selected_patient_id });
  await page.waitForURL('**/home');
  assert.equal((await apiGet('/api/session')).body.selected_patient_id, patientId);
  assert.equal((await apiGet(`/api/patients/${patientId}/field-config?kind=observation`)).body.patient_id, patientId);
} else {
  checks.push({ id: 'CROSS_PATIENT_PAGE_CONTENT_CLEANUP', status: 'NOT_RUN',
    reason: '模拟账号没有第二个有效且获准读取资料的患儿关联；未修改任何关联或权限。' });
}

const logoutRelationsPromise = page.waitForResponse(response => response.url().endsWith('/api/relations'));
await page.goto(`${origin}/account`, { waitUntil: 'domcontentloaded' });
await logoutRelationsPromise;
const logoutResponsePromise = page.waitForResponse(response => response.url().endsWith('/api/session/logout'));
await page.getByRole('button', { name: '退出登录', exact: true }).click();
const logoutResponse = await logoutResponsePromise;
assert.ok([200, 204].includes(logoutResponse.status()));
await page.waitForURL('**/home');
await page.goto(`${origin}/records/${firstCommitReceipt.record_id}`, { waitUntil: 'domcontentloaded' });
const afterLogoutRecord = await apiGet(`/api/records/${firstCommitReceipt.record_id}`);
assert.equal(afterLogoutRecord.status, 401);
assert.equal((await screenText()).includes(marker), false);
checks.push({ id: 'LOGOUT_DEEP_LINK_AND_SESSION_CONTENT_CLEANUP', status: 'PASS', logout_status: logoutResponse.status(),
  record_read_after_logout_status: afterLogoutRecord.status, simulation_marker_visible_after_logout: false });

checks.push({ id: 'REAL_DEVICE_WECHAT_HOME_OBSERVATION_VOICE_VISUAL', status: 'NOT_RUN',
  reason: '当前运行条件是桌面 Chrome 模拟浏览器；真实设备、微信、家庭观察、麦克风与视觉验收未执行。' });
assert.deepEqual(pageErrors, []);
await browser.close();

const runAt = new Date().toISOString();
const passCount = checks.filter(check => check.status === 'PASS').length;
const notRunCount = checks.filter(check => check.status === 'NOT_RUN').length;
const failedChecks = checks.filter(check => check.status === 'FAIL').length;
const report = { run_id: runId, mode: 'SIMULATION_ONLY', status: failedChecks ? 'FAIL' : 'PASS', run_at: runAt,
  task_ids: ['T30', 'T58'], acceptance_ids: ['V30', 'V58', 'AC-H5-01', 'AC-H5-02', 'AC-H5-06', 'AC-UX-01'],
  origin, patient_id: patientId, marker, browser: browserVersion,
  runtime: { node: process.version, playwright: '1.63.0', viewport: { width: 390, height: 844 } },
  totals: { pass: passCount, not_run: notRunCount, fail: failedChecks, page_errors: pageErrors.length }, checks, page_errors: pageErrors };
const outputPath = resolve(root, '.planning-work/parallel/window2/evidence/business-state-device-check-' + runAt.replaceAll(':', '-') + '.json');
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
if (failedChecks) process.exit(1);
