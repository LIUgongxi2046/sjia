import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { setTimeout as wait } from 'node:timers/promises';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const names = ['mode', 'origin', 'readiness-origin', 'credentials', 'source', 'module', 'report'];
const { values } = parseArgs({ options: Object.fromEntries([...names, 'resume'].map(name => [name, { type: 'string' }])) });
for (const name of names) assert.ok(values[name], `缺少参数：${name}`);
assert.ok(['create', 'resume'].includes(values.mode));
if (values.mode === 'resume') assert.ok(values.resume); else assert.equal(values.resume, undefined);
const resumeBytes = values.mode === 'resume' ? await readFile(values.resume) : null;
const resumed = resumeBytes ? JSON.parse(resumeBytes) : null;
const scriptPath = fileURLToPath(import.meta.url);
const scriptSha256 = createHash('sha256').update(await readFile(scriptPath)).digest('hex');
const origin = new URL(values.origin).origin;
assert.equal(new URL(origin).hostname, '127.0.0.1');
const readinessOrigin = new URL(values['readiness-origin']).origin;
assert.equal(new URL(readinessOrigin).hostname, '127.0.0.1');
const source = JSON.parse(await readFile(values.source, 'utf8'));
const credentials = JSON.parse(await readFile(values.credentials, 'utf8'));
const compiled = await readFile(values.module, 'utf8');
assert.equal(source.result, 'PASS');
assert.equal(source.source_kind, 'USER_APPROVED_ORIGINALS');
assert.equal(source.originals.length, 2);
assert.equal(createHash('sha256').update(compiled).digest('hex'), source.module_sha256);
const originals = [];
for (const item of source.originals) {
  const bytes = await readFile(resolve(item.path));
  assert.equal(bytes.length, item.size_bytes);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), item.sha256);
  originals.push({ ...item, bytes });
}
if (resumed) {
  assert.equal(resumed.mode, 'REAL_INTENT_EXPIRY_AND_RESPONSE_INTERRUPTION');
  assert.equal(resumed.source_manifest_sha256, source.source_manifest_sha256);
  assert.equal(resumed.uploaded_files.length, 2);
  assert.equal(resumed.first_intent.status, 'prepared');
  assert.equal(resumed.task_id, undefined);
}
const groupId = resumed ? resumed.group_id : randomUUID(), target = `/api/uploads/${groupId}/complete`;
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(),
  mode: 'REAL_INTENT_EXPIRY_AND_RESPONSE_INTERRUPTION', origin, group_id: groupId, checks: [],
  execution_mode: values.mode, script_sha256: scriptSha256,
  source_manifest_sha256: source.source_manifest_sha256,
  module_sha256: createHash('sha256').update(compiled).digest('hex'),
  uploaded_files: [], wait_observations: [], network_requests: [], new_model_calls: 0,
  new_upload_groups: 0, reused_upload_groups: 0,
  api_calls: [], readiness_origin: readinessOrigin,
  visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' };
if (resumeBytes) report.resume_evidence_sha256 = createHash('sha256').update(resumeBytes).digest('hex');
await writeFile(values.report, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const cdp = await context.newCDPSession(page);
let csrf, fetchEnabled = false, pausedTimer, failureTimer, resolveNativeFailure;
const network = new Map();
async function save() { await writeFile(values.report, JSON.stringify(report, null, 2)); }
async function api(method, path, expected, data, headers = {}) {
  const targetOrigin = path === '/internal/readiness' ? readinessOrigin : origin;
  const response = await context.request.fetch(targetOrigin + path, { method, data, timeout: 60000,
    headers: { Origin: targetOrigin, ...(csrf ? { 'X-CSRFToken': csrf } : {}), ...headers } });
  report.api_calls.push({ method, path, status: response.status(), content_type: response.headers()['content-type'] });
  assert.equal(response.status(), expected, `${method} ${path}`);
  assert.ok(response.headers()['content-type']?.startsWith('application/json'), `${method} ${path}`);
  return response.json();
}
async function rejection(path, code, data, headers, status = 409) {
  const body = await api('POST', path, status, data, headers);
  assert.equal(body.code, code);
  return { status, code: body.code };
}
cdp.on('Network.requestWillBeSent', event => {
  if (event.request.url !== origin + target) return;
  const item = { request_id: event.requestId, method: event.request.method };
  network.set(event.requestId, item); report.network_requests.push(item);
});
cdp.on('Network.responseReceived', event => {
  const item = network.get(event.requestId);
  if (item) item.status = event.response.status;
});
cdp.on('Network.loadingFailed', event => {
  const item = network.get(event.requestId);
  if (item) {
    item.cancelled = event.canceled === true; item.error = event.errorText;
    if (resolveNativeFailure) resolveNativeFailure(event);
  }
});
try {
  csrf = (await api('GET', '/api/auth/bootstrap', 200)).csrf_token;
  const session = await api('POST', '/api/auth/login', 200,
    { username: credentials.username, password: credentials.password });
  csrf = session.csrf_token;
  assert.equal(session.principal_id, source.actor_id);
  assert.ok(Date.parse(session.expires_at) - Date.now() > 60000);
  const runtime = await api('GET', '/internal/readiness', 200);
  assert.equal(runtime.status, 'ready');
  assert.equal(runtime.runtime_admission, 'admitted_current');
  report.loaded_config_version = runtime.loaded_config_version;
  const options = await api('GET', `/api/v2/patients/${source.patient_id}/archive-upload-options`, 200);
  assert.ok(options.authorizations.some(item => item.authorization_id === source.authorization_id
    && item.source_revision_id === source.source_revision_id));
  if (values.mode === 'create') {
    const created = await api('POST', `/api/patients/${source.patient_id}/uploads`, 201,
      { authorization_id: source.authorization_id, client_group_id: groupId });
    assert.equal(created.group_id, groupId);
    report.new_upload_groups = 1;
    await save();
    for (const [index, item] of originals.entries()) {
      const uploadId = randomUUID();
      const query = new URLSearchParams({ checksum: item.sha256, size: String(item.size_bytes),
        media_type: item.media_type, ordinal: String(index + 1) });
      const response = await context.request.put(`${origin}/api/uploads/${groupId}/files/${uploadId}?${query}`, {
        data: item.bytes, timeout: 60000,
        headers: { Origin: origin, 'Content-Type': 'application/octet-stream', 'X-CSRFToken': csrf } });
      assert.equal(response.status(), 201);
      const file = await response.json();
      assert.equal(file.checksum, item.sha256);
      assert.equal(file.size_bytes, item.size_bytes);
      report.uploaded_files.push({ file_id: file.file_id, source_file_id: item.source_file_id,
        sha256: file.checksum, size_bytes: file.size_bytes, ordinal: index + 1 });
      await save();
    }
  } else {
    report.new_upload_groups = 0;
    for (const [index, item] of originals.entries()) {
      const saved = resumed.uploaded_files[index];
      assert.equal(saved.source_file_id, item.source_file_id);
      assert.equal(saved.sha256, item.sha256);
      assert.equal(saved.size_bytes, item.size_bytes);
      const response = await context.request.get(`${origin}/api/files/${saved.file_id}/content`, { timeout: 60000 });
      assert.equal(response.status(), 200);
      const bytes = await response.body();
      assert.equal(bytes.length, item.size_bytes);
      assert.equal(createHash('sha256').update(bytes).digest('hex'), item.sha256);
      report.uploaded_files.push(saved);
    }
    report.reused_upload_groups = 1;
    await save();
  }
  const group = await api('GET', `/api/uploads/${groupId}`, 200);
  assert.equal(group.patient_id, source.patient_id);
  assert.equal(group.files.length, 2);
  const payload = { expected_version: group.version,
    file_ids: [...group.files].sort((a, b) => a.ordinal - b.ordinal).map(item => item.file_id) };
  assert.deepEqual(payload.file_ids, report.uploaded_files.map(item => item.file_id));
  const input = { operation_id: 'file_group_complete', target_key: target,
    request_id: groupId, schema_version: '1.0.0', payload };
  const first = await api('POST', '/api/write-intents', 201, input);
  assert.equal(first.status, 'prepared');
  if (resumed) {
    assert.deepEqual(payload, resumed.payload);
    assert.deepEqual(first, resumed.first_intent);
  }
  const same = await api('POST', '/api/write-intents', 201, input);
  assert.deepEqual(same, first);
  report.first_intent = first; report.payload = payload;
  report.checks.push('获准原始字节与准确上传组一致，相同正文准备继续使用同一原意图');
  await save();
  const deadline = Date.parse(first.expires_at), waitStarted = performance.now();
  assert.ok(deadline - Date.now() <= source.intent_ttl_seconds * 1000);
  while (Date.now() <= deadline) {
    await wait(Math.min(45000, deadline - Date.now() + 25));
    const item = { elapsed_ms: performance.now() - waitStarted, remaining_ms: Math.max(0, deadline - Date.now()) };
    report.wait_observations.push(item);
    process.stdout.write(JSON.stringify({ phase: 'intent_expiry_wait', ...item }) + '\n');
    await save();
  }
  report.expiry_wait_ms = performance.now() - waitStarted;
  csrf = (await api('GET', '/api/auth/bootstrap', 200)).csrf_token;
  const resumedSession = await api('POST', '/api/auth/login', 200,
    { username: credentials.username, password: credentials.password });
  assert.equal(resumedSession.principal_id, source.actor_id);
  csrf = resumedSession.csrf_token;
  assert.deepEqual((await api('GET', '/internal/readiness', 200)).loaded_config_version, runtime.loaded_config_version);
  const originalHeaders = { 'X-Write-Intent': first.intent_id, 'X-Intent-Generation': first.generation,
    'Idempotency-Key': groupId };
  const intentPath = `/api/requests/${first.intent_id}`;
  const expired = await api('GET', intentPath, 200);
  assert.equal(expired.intent_id, first.intent_id);
  assert.equal(expired.status, 'prepared');
  assert.equal(expired.generation, first.generation);
  assert.equal(expired.may_retry, true);
  assert.equal(expired.task, null);
  report.expired_view = expired;
  report.checks.push('真实准备期限届满后原意图查询证明没有任务且允许恢复');
  await save();
  await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' });
  await page.evaluate(async code => {
    window.qa56ModuleUrl = URL.createObjectURL(new Blob([code], { type: 'text/javascript' }));
    window.qa56Validation = await import(window.qa56ModuleUrl);
  }, compiled);
  await cdp.send('Network.enable');
  const nextPausedResponse = () => new Promise((resolve, reject) => {
    pausedTimer = setTimeout(() => reject(new Error('实际提交响应尚未取得')), 60000);
    cdp.once('Fetch.requestPaused', event => { clearTimeout(pausedTimer); resolve(event); });
  });
  const expiredResponse = nextPausedResponse();
  await cdp.send('Fetch.enable', { patterns: [{ urlPattern: origin + target, requestStage: 'Response' }] });
  fetchEnabled = true;
  const failedRead = page.waitForEvent('requestfailed', { predicate: request => request.url() === origin + target,
    timeout: 30000 });
  const nativeFailedRead = new Promise((resolve, reject) => {
    failureTimer = setTimeout(() => reject(new Error('实际网络中断事件尚未取得')), 30000);
    resolveNativeFailure = event => { clearTimeout(failureTimer); resolve(event); };
  });
  const renewalResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api/write-intents/${first.intent_id}/retry`
    && response.request().method() === 'POST' && response.status() === 200, { timeout: 60000 });
  const interrupted = page.evaluate(async ({ groupId, csrf, patientId }) =>
    window.qa56Validation.completeValidatedFileGroup(groupId, csrf, undefined, patientId),
  { groupId, csrf, patientId: source.patient_id });
  const expectedAbort = assert.rejects(interrupted, error => {
    report.interruption_message = error.message;
    return /Failed to fetch|AbortError|aborted/.test(error.message);
  });
  const expiredPause = await expiredResponse;
  assert.equal(expiredPause.responseStatusCode, 409);
  const expiredBody = await cdp.send('Fetch.getResponseBody', { requestId: expiredPause.requestId });
  const expiredError = JSON.parse(expiredBody.base64Encoded ? Buffer.from(expiredBody.body, 'base64').toString('utf8') : expiredBody.body);
  assert.equal(expiredError.code, 'FILE_VALIDATION_INTENT_EXPIRED');
  report.expired_submission = { status: expiredPause.responseStatusCode, code: expiredError.code };
  const acceptedResponse = nextPausedResponse();
  await cdp.send('Fetch.continueRequest', { requestId: expiredPause.requestId });
  const renewed = await (await renewalResponse).json();
  assert.equal(renewed.intent_id, first.intent_id);
  assert.equal(BigInt(renewed.generation), BigInt(first.generation) + 1n);
  assert.equal(renewed.status, 'prepared');
  report.renewed_intent = renewed;
  report.checks.push('当前家庭模块接收真实过期响应，查询原意图并恢复准确新代次');
  report.late_generation = await rejection(target, 'FILE_VALIDATION_INTENT_MISMATCH', payload, originalHeaders);
  const paused = await acceptedResponse;
  assert.equal(paused.request.url, origin + target);
  assert.equal(paused.request.method, 'POST');
  assert.equal(paused.responseStatusCode, 202);
  assert.equal(new Headers(paused.request.headers).get('X-Intent-Generation'), renewed.generation);
  const received = await cdp.send('Fetch.getResponseBody', { requestId: paused.requestId });
  const responseTask = JSON.parse(received.base64Encoded ? Buffer.from(received.body, 'base64').toString('utf8') : received.body);
  assert.equal(responseTask.request_id, groupId);
  assert.equal(responseTask.task_type, 'file_validation');
  report.paused_response = { status: paused.responseStatusCode, task: responseTask };
  const committed = await api('GET', intentPath, 200);
  assert.equal(committed.status, 'committed');
  assert.equal(committed.generation, renewed.generation);
  assert.equal(committed.task.task_id, responseTask.task_id);
  assert.equal(committed.intent_id, first.intent_id);
  assert.equal(committed.request_id, groupId);
  assert.equal(committed.task.request_id, groupId);
  report.committed_view = committed; report.task_id = committed.task.task_id;
  await save();
  await cdp.send('Fetch.failRequest', { requestId: paused.requestId, errorReason: 'Aborted' });
  await expectedAbort;
  const failedRequest = await failedRead;
  const nativeFailure = await nativeFailedRead;
  assert.equal(failedRequest.method(), 'POST');
  assert.deepEqual(failedRequest.postDataJSON(), payload);
  assert.equal(new Headers(failedRequest.headers()).get('X-Intent-Generation'), renewed.generation);
  assert.equal(failedRequest.failure().errorText, 'net::ERR_ABORTED');
  const interruptedRequest = report.network_requests.find(item => item.request_id === nativeFailure.requestId);
  assert.equal(interruptedRequest.method, 'POST');
  assert.equal(interruptedRequest.error, 'net::ERR_ABORTED');
  report.interrupted_network_id = nativeFailure.requestId;
  report.renderer_submission_result = 'REJECTED';
  await cdp.send('Fetch.disable');
  fetchEnabled = false;
  report.checks.push('新代次实际提交响应中断，原意图查询取得准确任务，迟到旧代次被拒绝');
  await save();
  const renewedHeaders = { ...originalHeaders, 'X-Intent-Generation': renewed.generation };
  const repeated = await Promise.all([0, 1].map(() => api('POST', target, 202, payload, renewedHeaders)));
  for (const task of repeated) assert.equal(task.task_id, committed.task.task_id);
  report.replay_tasks = repeated;
  report.different_body = await rejection(target, 'FILE_VALIDATION_GROUP_INCOMPLETE',
    { ...payload, file_ids: [...payload.file_ids].reverse() }, renewedHeaders, 422);
  report.checks.push('同一新代次重放返回原任务，改变真实文件顺序的正文被拒绝');
  const adopted = await page.evaluate(async ({ task, groupId, patientId, csrf }) => {
    const completed = await window.qa56Validation.waitForFileValidationTask(task, groupId);
    const group = await window.qa56Validation.completeValidatedFileGroup(groupId, csrf, undefined, patientId);
    return { completed, group };
  }, { task: committed.task, groupId, patientId: source.patient_id, csrf });
  assert.equal(adopted.completed.status, 'completed');
  assert.equal(adopted.completed.task_id, committed.task.task_id);
  assert.deepEqual(adopted.group, { group_id: groupId, status: 'available' });
  const finalTask = await api('GET', `/api/tasks/${committed.task.task_id}`, 200);
  assert.deepEqual(finalTask, adopted.completed);
  const finalIntent = await api('GET', intentPath, 200);
  assert.equal(finalIntent.status, 'committed');
  assert.equal(finalIntent.generation, renewed.generation);
  assert.equal(finalIntent.may_retry, false);
  assert.deepEqual(finalIntent.task, finalTask);
  const finalGroup = await api('GET', `/api/uploads/${groupId}`, 200);
  assert.equal(finalGroup.status, 'available');
  assert.deepEqual(finalGroup.files.map(item => item.file_id), payload.file_ids);
  report.completed_task = finalTask;
  report.committed_retry = await rejection(`/api/write-intents/${first.intent_id}/retry`,
    'FILE_VALIDATION_RETRY_UNAVAILABLE', { expected_generation: renewed.generation });
  report.checks.push('当前家庭模块采用原任务并读取可用原组，已提交意图禁止再次恢复代次');
  report.browser_version = browser.version();
  assert.equal(createHash('sha256').update(await readFile(scriptPath)).digest('hex'), scriptSha256);
  report.result = 'PASS';
  process.stdout.write(JSON.stringify({ result: 'PASS', checks: report.checks.length,
    uploaded_files: report.uploaded_files.length, new_upload_groups: report.new_upload_groups,
    reused_upload_groups: report.reused_upload_groups, new_tasks: 1, new_model_calls: 0 }) + '\n');
} finally {
  clearTimeout(pausedTimer);
  clearTimeout(failureTimer);
  if (fetchEnabled) await cdp.send('Fetch.disable');
  if (report.result !== 'PASS') report.result = 'FAIL';
  report.finished_at = new Date().toISOString();
  await save();
  await browser.close();
}
