import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, statfsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, credentials: { type: 'string' },
  source: { type: 'string' }, staged: { type: 'string' }, dist: { type: 'string' },
  port: { type: 'string', default: '9236' } } });
for (const key of ['report', 'credentials', 'source', 'staged', 'dist']) assert.ok(values[key], key);
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const space = statfsSync(resolve(import.meta.dirname, '../..'));
assert.ok(space.bavail * space.bsize >= 1024 ** 3);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const source = JSON.parse(await readFile(resolve(values.source), 'utf8'));
const staged = JSON.parse(await readFile(resolve(values.staged), 'utf8'));
assert.equal(source.result, 'PASS');
assert.equal(staged.result, 'STAGED');
const origin = 'http://127.0.0.1:5178';
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), origin,
  test_sha256: createHash('sha256').update(await readFile(import.meta.filename)).digest('hex'),
  stage: 'START', checks: [], tasks: {}, page_errors: [], clinical_accuracy_validation: 'NOT_RUN',
  independent_professional_review: false, visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: Number(values.port), headless: true });
const { context, browser } = native;
const page = await context.newPage();
page.setDefaultTimeout(30000);
page.on('pageerror', failure => report.page_errors.push(failure.message));
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function record(name, details = {}) { report.checks.push({ name, result: 'PASS', ...details }); await save(); }
async function responseJson(path) {
  const response = await context.request.get(origin + path);
  assert.equal(response.status(), 200, path);
  return response.json();
}
async function taskResult(kind, created) {
  const endpoint = kind === 'classification' ? 'assessment' : 'alert-evaluation';
  const deadline = performance.now() + 120000;
  let task = created;
  while (!['completed', 'failed', 'cancelled', 'superseded'].includes(task.status)) {
    assert.ok(performance.now() < deadline, `TASK_COMPLETION_TIMEOUT:${task.task_id}:${task.status}`);
    assert.ok(Number.isSafeInteger(task.poll_after_ms) && task.poll_after_ms > 0);
    await new Promise(resolve => setTimeout(resolve, task.poll_after_ms));
    task = await responseJson(`/api/v2/support/${endpoint}-tasks/${created.task_id}`);
    assert.equal(task.task_id, created.task_id);
  }
  report.tasks[kind].completed = task; await save();
  assert.equal(task.status, 'completed', task.error_code ?? task.status);
  assert.ok(task.result_ref);
  return responseJson(`/api/v2/support/${endpoint}s/${task.result_ref}`);
}
class AssessmentForm {
  constructor(kind) {
    this.kind = kind;
    this.dialog = page.getByRole('dialog', { name: kind === 'classification' ? '创建静态分类' : '创建动态评估', exact: true });
  }
  select(title) {
    // 下拉框的 label 包含选项文字，使用标签内的准确标题限定控件。
    return this.dialog.locator('label.field').filter({ has: page.getByText(title, { exact: true }) }).locator('select');
  }
  async open(at) {
    await page.goto(origin + `/support/assessment?evaluation_kind=${this.kind}&patient_id=${source.patient_id}`);
    await page.getByRole('button', { name: '观察时段与来源', exact: true }).click();
    await this.dialog.waitFor();
    report.form_controls = await this.dialog.locator('label.field').evaluateAll(labels => labels.map(label => ({
      title: label.querySelector('span')?.textContent, tag: label.querySelector('input, select')?.tagName,
    })));
    await save();
    assert.ok(report.form_controls.some(item => item.title === '当前获准病例' && item.tag === 'SELECT'));
    assert.equal(await this.select('当前获准病例').count(), 1);
    await this.select('当前获准病例').selectOption(source.patient_id);
    await this.dialog.getByLabel('评估时点（含时区）', { exact: true }).fill(at);
    await this.dialog.getByLabel('资料截止时点（含时区）', { exact: true }).fill(at);
    const endpoint = this.kind === 'classification' ? 'assessment-options' : 'alert-evaluation-options';
    const response = page.waitForResponse(item => new URL(item.url()).pathname ===
      `/api/v2/support/patients/${source.patient_id}/${endpoint}` && item.request().method() === 'GET');
    await this.dialog.getByRole('button', { name: '读取准确批准版本', exact: true }).click();
    const received = await response;
    assert.equal(received.status(), 200);
    const options = await received.json();
    report.options = { kind: this.kind, patient_id: options.patient_id, plan_count: options.plans.length,
      known_at: options.known_at, target_rule: staged.configuration_ids[this.kind === 'classification' ? 'classification' : 'trigger'] };
    await save();
    assert.equal(options.patient_id, source.patient_id);
    const ruleId = report.options.target_rule;
    const matches = options.plans.filter(item => item.kind === this.kind && item.rule.config_id === ruleId);
    assert.equal(matches.length, 1, `QUALIFIED_PLAN_REQUIRED:${this.kind}`);
    const selected = matches[0];
    const key = [selected.rule.config_id,
      ...(this.kind === 'dynamic' ? [selected.input_rule.config_id] : []),
      selected.window.config_id, selected.verification.config_id].join(':');
    await this.select('获准规则与窗口').selectOption(key);
    if (this.kind === 'dynamic') {
      assert.ok(selected.use_authorization_ids.includes(source.versions[0].use_authorization_id));
      await this.select('准确资料用途授权').selectOption(source.versions[0].use_authorization_id);
    }
  }
  async submit() {
    const suffix = this.kind === 'classification' ? 'assessments' : 'alert-evaluations';
    const response = page.waitForResponse(item => new URL(item.url()).pathname ===
      `/api/v2/support/patients/${source.patient_id}/${suffix}` && item.request().method() === 'POST');
    await this.dialog.getByRole('button', { name: '创建评估任务', exact: true }).click();
    const received = await response;
    assert.equal(received.status(), 202);
    const task = await received.json();
    assert.equal(task.task_type, this.kind === 'classification' ? 'assessment' : 'alert_evaluation');
    report.tasks[this.kind] = { created: task }; await save();
    await page.waitForURL(url => url.searchParams.get('task_id') === task.task_id);
    return taskResult(this.kind, task);
  }
}
try {
  const response = await context.request.get(origin + '/home');
  assert.equal(response.status(), 200);
  const index = await response.body();
  assert.deepEqual(index, await readFile(resolve(values.dist, 'index.html')));
  report.index_sha256 = createHash('sha256').update(index).digest('hex');
  report.stage = 'SUPPORT_LOGIN'; await save();
  await page.goto(origin + '/support/login');
  await page.getByLabel('账号', { exact: true }).fill(credentials.username);
  await page.getByLabel('密码', { exact: true }).fill(credentials.password);
  const loginResponse = page.waitForResponse(item => new URL(item.url()).pathname === '/api/v2/support/session/login');
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  assert.equal((await loginResponse).status(), 200);
  await page.waitForURL('**/support/data-use');
  const session = await responseJson('/api/v2/support/session');
  assert.equal(session.principal_id, source.actor_id);
  for (const capability of ['execute_assessment', 'read_assessment'])
    assert.ok(session.capabilities.some(item => item.capability_id === capability && item.state === 'available'));
  const directory = await responseJson('/api/v2/support/clinical-scopes?action=execute_assessment');
  assert.deepEqual(directory.patients.map(item => item.patient_id), [source.patient_id]);
  await record('ACTUAL_SUPPORT_SESSION_AND_SINGLE_CASE_SCOPE');
  const at = new Date().toISOString(); report.assessment_at = at;
  report.stage = 'CLASSIFICATION_OPTIONS'; await save();
  const classificationForm = new AssessmentForm('classification');
  await classificationForm.open(at);
  report.stage = 'CLASSIFICATION_TASK'; await save();
  const classification = await classificationForm.submit();
  assert.equal(classification.patient_id, source.patient_id);
  assert.equal(classification.rule_id, staged.configuration_ids.classification);
  assert.equal(classification.computation_status, 'insufficient');
  assert.equal(classification.validity, 'valid');
  assert.equal(classification.output.classification_result, 'insufficient');
  assert.equal(classification.inputs.length, 0);
  for (const code of ['sjia', 'fever', 'ferritin', 'platelet_count', 'ast', 'triglycerides', 'fibrinogen'])
    assert.ok(classification.missing_items.some(item => item.variable_code === code));
  await page.getByRole('heading', { name: '评估范围与资料不足', exact: true }).waitFor();
  const staticUrl = page.url();
  await page.reload();
  await page.getByRole('heading', { name: '评估范围与资料不足', exact: true }).waitFor();
  assert.equal(page.url(), staticUrl);
  await record('REAL_CLASSIFICATION_TASK_PERSISTS_INSUFFICIENT_AND_RELOADS', { assessment_id: classification.assessment_id });

  report.stage = 'DYNAMIC_OPTIONS'; await save();
  const dynamicForm = new AssessmentForm('dynamic');
  await dynamicForm.open(at);
  report.stage = 'DYNAMIC_TASK'; await save();
  const dynamic = await dynamicForm.submit();
  assert.equal(dynamic.patient_id, source.patient_id);
  assert.equal(dynamic.policy_id, staged.configuration_ids.trigger);
  assert.equal(dynamic.window_policy_id, staged.configuration_ids.window);
  assert.equal(dynamic.validity, 'valid'); assert.equal(dynamic.status, 'insufficient'); assert.equal(dynamic.decision, 'unknown');
  const signal = dynamic.signals.find(item => item.signal_id === 'trigger:classification');
  assert.ok(signal); assert.equal(signal.state, 'unknown');
  assert.ok(signal.reason_codes.includes('DYNAMIC_COMPATIBLE_STATIC_RESULT_REQUIRED'));
  assert.equal(Object.hasOwn(signal, 'assessment_id'), false);
  assert.equal(dynamic.lifecycle.event_id, null);
  assert.ok(Object.values(dynamic.selected_refs).every(item => item === null));
  await page.getByRole('heading', { name: '评估范围与资料不足', exact: true }).waitFor();
  const dynamicUrl = page.url();
  await page.reload();
  await page.getByRole('heading', { name: '评估范围与资料不足', exact: true }).waitFor();
  assert.equal(page.url(), dynamicUrl);
  await record('REAL_DYNAMIC_TASK_PRESERVES_STATIC_QUALIFICATION_LIMIT_WITHOUT_EVENT', { evaluation_id: dynamic.evaluation_id });

  report.stage = 'FAMILY_ADOPTION'; await save();
  await page.goto(origin + '/support/data-use');
  await page.getByRole('button', { name: '退出受控会话', exact: true }).click();
  await page.waitForURL('**/support/login');
  await page.goto(origin + '/login');
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL('**/home');
  await page.goto(origin + '/associations');
  const card = page.locator('.rich-child-identity').filter({ has: page.getByRole('heading', {
    name: directory.patients[0].display_id, exact: true }) });
  await card.waitFor();
  const select = card.getByRole('button', { name: '选择此患儿', exact: true });
  if (await select.count()) { await select.click(); await page.waitForURL('**/home'); }
  else await page.goto(origin + '/home');
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt) await page.reload();
    await page.getByRole('heading', { name: '评估资料不足', exact: true }).waitFor();
    const home = await responseJson(`/api/patients/${source.patient_id}/home`);
    assert.equal(home.assessment_state, 'insufficient'); assert.equal(home.current_alert_id, null);
    assert.equal(Date.parse(home.assessed_at), Date.parse(at));
    report.home = home;
  }
  await record('FAMILY_HOME_USES_SAVED_INSUFFICIENT_RESULT_AFTER_RELOAD');
  await page.getByRole('button', { name: '查看服务状态与资料依据', exact: true }).click();
  await page.waitForURL('**/alerts/current');
  await page.getByRole('heading', { name: '评估资料不足', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('heading', { name: '评估资料不足', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '有当前提示', exact: true }).count(), 0);
  await record('FAMILY_DETAIL_PRESERVES_UNKNOWN_WITHOUT_DEFINITE_RISK');
  assert.deepEqual(report.page_errors, []);
  report.stage = 'COMPLETE'; report.result = 'PASS';
} finally {
  if (report.result === 'IN_PROGRESS') report.result = 'FAIL';
  report.finished_at = new Date().toISOString(); report.browser_version = browser.version();
  await save(); await native.close();
  console.log(JSON.stringify({ result: report.result, stage: report.stage, checks: report.checks.length,
    page_errors: report.page_errors.length, clinical_accuracy_validation: report.clinical_accuracy_validation }));
}
