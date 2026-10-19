import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';

const names = ['origin', 'credentials', 'evidence', 'index', 'report'];
const { values } = parseArgs({ options: Object.fromEntries(names.map(name => [name, { type: 'string' }])) });
for (const name of names) assert.ok(values[name], `缺少参数：${name}`);
const origin = new URL(values.origin).origin;
assert.equal(new URL(origin).hostname, '127.0.0.1');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const credentials = JSON.parse(await readFile(values.credentials, 'utf8'));
const source = JSON.parse(await readFile(resolve(values.evidence, 'source.json'), 'utf8'));
const initial = JSON.parse(await readFile(resolve(values.evidence, 'browser.json'), 'utf8'));
const previous = JSON.parse(await readFile(resolve(values.evidence, 'current-browser.json'), 'utf8'));
const policy = JSON.parse(await readFile(resolve(values.evidence, 'policy-verified.json'), 'utf8'));
const compiled = await readFile(resolve(values.evidence, 'bundle-verified/file-validation.mjs'), 'utf8');
assert.equal(source.result, 'PASS');
assert.equal(source.source_kind, 'USER_APPROVED_ORIGINAL');
assert.equal(initial.accepted_task.status, 'accepted');
assert.equal(initial.accepted_task.task_id, previous.task_id);
assert.equal(initial.group_id, previous.group_id);
assert.equal(createHash('sha256').update(compiled).digest('hex'), previous.bundle_sha256);
assert.equal(createHash('sha256').update(await readFile(resolve(root, 'guardian-h5/src/api/fileValidation.ts'))).digest('hex'), policy.source_sha256);
const report = { status: 'IN_PROGRESS', started_at: new Date().toISOString(), origin,
  task_id: previous.task_id, group_id: previous.group_id, code_sha256: policy.source_sha256,
  bundle_sha256: previous.bundle_sha256, checks: [], requests: [], conditions: [], wait_observations: [],
  mode: 'REAL_TASK_WITH_BROWSER_NETWORK_LATENCY', new_uploads: 0, new_tasks: 0, new_model_calls: 0,
  visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' };
await writeFile(values.report, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
const taskPath = `/api/tasks/${previous.task_id}`;
const requestIds = new Map();
let phase = 'setup', activeConditions = false, heartbeat;
async function save() { await writeFile(values.report, JSON.stringify(report, null, 2)); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + path, { method, data,
    headers: { Origin: origin, ...(csrf ? { 'X-CSRFToken': csrf } : {}) }, timeout: 60000 });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()}`);
  return response.json();
}
cdp.on('Network.requestWillBeSent', event => {
  if (event.request.url !== origin + taskPath) return;
  const item = { phase, request_id: event.requestId, method: event.request.method };
  requestIds.set(event.requestId, item); report.requests.push(item);
});
cdp.on('Network.requestWillBeSentExtraInfo', event => {
  const item = requestIds.get(event.requestId);
  if (item) item.condition_id = event.appliedNetworkConditionsId ?? null;
});
cdp.on('Network.responseReceived', event => {
  const item = requestIds.get(event.requestId);
  if (item) item.status = event.response.status;
});
cdp.on('Network.loadingFailed', event => {
  const item = requestIds.get(event.requestId);
  if (item) { item.cancelled = event.canceled === true; item.error = event.errorText; }
});
async function latency(milliseconds) {
  const result = await cdp.send('Network.emulateNetworkConditionsByRule', { matchedNetworkConditions: [{
    urlPattern: origin + taskPath, latency: milliseconds, downloadThroughput: -1, uploadThroughput: -1,
  }] });
  assert.equal(result.ruleIds.length, 1);
  activeConditions = true;
  report.conditions.push({ phase, latency_ms: milliseconds, rule_id: result.ruleIds[0] });
  return result.ruleIds[0];
}
try {
  const bootstrap = await api('GET', '/api/auth/bootstrap');
  const session = await api('POST', '/api/auth/login', { username: credentials.username, password: credentials.password }, bootstrap.csrf_token);
  assert.equal(session.principal_id, source.actor_id);
  assert.ok(Date.parse(session.expires_at) - Date.now() > 360000);
  const before = await api('GET', taskPath);
  assert.equal(before.status, 'completed');
  assert.equal(before.request_id, previous.group_id);
  assert.equal(before.result_ref, previous.group_id);
  const index = await context.request.get(origin + '/login');
  assert.deepEqual(await index.body(), await readFile(values.index));
  await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' });
  await page.evaluate(async code => {
    window.qa54ModuleUrl = URL.createObjectURL(new Blob([code], { type: 'text/javascript' }));
    window.qa54Validation = await import(window.qa54ModuleUrl);
  }, compiled);
  await cdp.send('Network.enable');
  phase = 'calibration';
  const calibrationRule = await latency(2000);
  const calibrationStart = performance.now();
  const calibration = await page.evaluate(async path => {
    const response = await fetch(path, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`实际任务读取失败：${response.status}`);
    return response.json();
  }, taskPath);
  report.calibration_ms = performance.now() - calibrationStart;
  assert.deepEqual(calibration, before);
  assert.ok(report.calibration_ms >= 2000);
  assert.ok(report.requests.some(item => item.phase === phase && item.condition_id === calibrationRule && item.status === 200));
  report.checks.push('实际任务响应和Chrome按URL限制的延迟条件完成校准');
  phase = 'deadline';
  const rule = await latency(315000);
  const failedRead = page.waitForEvent('requestfailed', { predicate: request => request.url() === origin + taskPath,
    timeout: 310000 });
  const started = performance.now();
  heartbeat = setInterval(() => {
    const observation = { elapsed_ms: performance.now() - started,
      task_requests: report.requests.filter(item => item.phase === 'deadline').length };
    report.wait_observations.push(observation);
    process.stdout.write(JSON.stringify({ phase: 'deadline_wait', ...observation }) + '\n');
  }, 45000);
  process.stdout.write(JSON.stringify({ phase: 'deadline_started', started_at: new Date().toISOString(), budget_ms: 300000 }) + '\n');
  await save();
  await assert.rejects(page.evaluate(async ({ task, group }) => {
    window.qa54Started = performance.now();
    return window.qa54Validation.waitForFileValidationTask(task, group);
  }, { task: initial.accepted_task, group: previous.group_id }), error => {
    report.expiry_message = error.message;
    return /原件校验自动查询已经结束/.test(error.message);
  });
  clearInterval(heartbeat); heartbeat = undefined;
  report.node_elapsed_ms = performance.now() - started;
  report.browser_elapsed_ms = await page.evaluate(() => performance.now() - window.qa54Started);
  assert.ok(report.browser_elapsed_ms >= 300000 && report.browser_elapsed_ms < 305000);
  const failed = await failedRead;
  report.failed_read = failed.failure();
  const timedReads = report.requests.filter(item => item.phase === 'deadline');
  assert.equal(timedReads.length, 1);
  assert.equal(timedReads[0].condition_id, rule);
  assert.equal(timedReads[0].method, 'GET');
  assert.equal(timedReads[0].cancelled, true);
  assert.equal(timedReads[0].status, undefined);
  report.checks.push('真实浏览器持续五分钟后中止延迟读取，没有采用迟到响应');
  await cdp.send('Network.emulateNetworkConditionsByRule', { matchedNetworkConditions: [] });
  activeConditions = false;
  phase = 'recovery';
  const recovered = await page.evaluate(async ({ task, group }) =>
    window.qa54Validation.waitForFileValidationTask(task, group),
  { task: initial.accepted_task, group: previous.group_id });
  assert.deepEqual(recovered, before);
  report.checks.push('网络限制解除后按原间隔读取同一任务并采用准确完成版本');
  const group = await page.evaluate(async ({ id, patientId }) =>
    window.qa54Validation.completeValidatedFileGroup(id, undefined, undefined, patientId),
  { id: previous.group_id, patientId: source.patient_id });
  assert.deepEqual(group, { group_id: previous.group_id, status: 'available' });
  assert.deepEqual(await api('GET', taskPath), before);
  report.checks.push('原上传组继续可用，服务端任务身份和完成版本保持');
  report.status = 'PASS';
  report.five_minute_browser_wait = 'PASS';
  report.browser_version = browser.version();
  process.stdout.write(JSON.stringify({ status: report.status, checks: report.checks.length,
    elapsed_ms: report.browser_elapsed_ms, new_tasks: 0, new_model_calls: 0 }) + '\n');
} finally {
  clearInterval(heartbeat);
  if (activeConditions) await cdp.send('Network.emulateNetworkConditionsByRule', { matchedNetworkConditions: [] });
  if (report.status !== 'PASS') report.status = 'FAIL';
  report.finished_at = new Date().toISOString();
  await save();
  await browser.close();
}
