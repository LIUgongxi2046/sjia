import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { request } from 'playwright';

const names = ['origin', 'credentials', 'plan', 'report'];
const { values } = parseArgs({ options: Object.fromEntries(names.map(name => [name, { type: 'string' }])) });
for (const name of names) assert.ok(values[name], `缺少参数：${name}`);
const origin = new URL(values.origin).origin;
assert.equal(new URL(origin).hostname, '127.0.0.1');
const credentials = JSON.parse(await readFile(values.credentials, 'utf8'));
const plan = JSON.parse(await readFile(values.plan, 'utf8'));
assert.equal(plan.result, 'READY');
assert.equal(plan.intent.status, 'committed');
assert.ok(Date.parse(plan.intent.expires_at) < Date.now());
assert.equal(plan.intent.request_id, plan.group_id);
assert.equal(plan.intent.target_key, `/api/uploads/${plan.group_id}/complete`);
assert.equal(plan.intent.payload.file_ids.length, 1);
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), origin,
  mode: 'REAL_COMMITTED_INTENT_REPLAY', checks: [], concurrent_requests: [],
  intent_id: plan.intent.id, task_id: plan.task_id, group_id: plan.group_id,
  new_uploads: 0, new_tasks: 0, new_model_calls: 0, full_v05: 'NOT_RUN' };
await writeFile(values.report, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const context = await request.newContext({ baseURL: origin, extraHTTPHeaders: { Origin: origin }, timeout: 60000 });
async function json(method, path, expected, data, headers) {
  const response = await context.fetch(path, { method, data, headers });
  assert.equal(response.status(), expected, `${method} ${path}`);
  return response.json();
}
function checkedView(view, task) {
  assert.equal(view.intent_id, plan.intent.id);
  assert.equal(view.request_id, plan.intent.request_id);
  assert.equal(view.operation_id, 'file_group_complete');
  assert.equal(view.patient_id, plan.patient_id);
  assert.equal(view.target_key, plan.intent.target_key);
  assert.equal(view.status, 'committed');
  assert.equal(view.generation, plan.intent.generation);
  assert.equal(view.may_retry, false);
  assert.deepEqual(view.file_group_input, plan.intent.payload);
  assert.deepEqual(view.task, task);
}
try {
  const bootstrap = await json('GET', '/api/auth/bootstrap', 200);
  const session = await json('POST', '/api/auth/login', 200,
    { username: credentials.username, password: credentials.password }, { 'X-CSRFToken': bootstrap.csrf_token });
  assert.equal(session.principal_id, plan.actor_id);
  const taskPath = `/api/tasks/${plan.task_id}`, intentPath = `/api/requests/${plan.intent.id}`;
  const before = await json('GET', taskPath, 200);
  assert.equal(before.task_id, plan.task_id);
  assert.equal(before.status, 'completed');
  assert.equal(before.result_ref, plan.group_id);
  const intentBefore = await json('GET', intentPath, 200);
  checkedView(intentBefore, before);
  report.checks.push('原提交意图查询返回准确上传组及原完成任务');
  const prepared = await json('POST', '/api/write-intents', 201,
    { operation_id: 'file_group_complete', target_key: plan.intent.target_key,
      request_id: plan.intent.request_id, schema_version: '1.0.0', payload: plan.intent.payload },
    { 'X-CSRFToken': session.csrf_token });
  assert.equal(prepared.intent_id, plan.intent.id);
  assert.equal(prepared.generation, plan.intent.generation);
  assert.equal(prepared.status, 'committed');
  assert.equal(Date.parse(prepared.expires_at), Date.parse(plan.intent.expires_at));
  report.checks.push('相同真实正文重新准备仍返回原意图及原代次');
  const headers = { 'X-CSRFToken': session.csrf_token, 'X-Write-Intent': plan.intent.id,
    'X-Intent-Generation': plan.intent.generation, 'Idempotency-Key': plan.intent.request_id };
  const started = performance.now();
  await Promise.all([0, 1].map(async index => {
    const observation = { index, started_ms: performance.now() - started };
    report.concurrent_requests.push(observation);
    const result = await json('POST', plan.intent.target_key, 202, plan.intent.payload, headers);
    observation.finished_ms = performance.now() - started;
    observation.status = 202;
    assert.deepEqual(result, before);
    observation.same_original_task = true;
  }));
  const observations = [...report.concurrent_requests].sort((a, b) => a.started_ms - b.started_ms);
  assert.equal(observations.length, 2);
  assert.ok(observations[1].started_ms < observations[0].finished_ms);
  report.checks.push('两个请求同时重放原完成正文，分别返回同一原任务');
  assert.deepEqual(await json('GET', taskPath, 200), before);
  const intentAfter = await json('GET', intentPath, 200);
  checkedView(intentAfter, before);
  assert.deepEqual(intentAfter, intentBefore);
  const group = await json('GET', `/api/uploads/${plan.group_id}`, 200);
  assert.equal(group.group_id, plan.group_id);
  assert.equal(group.patient_id, plan.patient_id);
  assert.equal(group.status, 'available');
  assert.deepEqual(group.files.map(file => file.file_id), plan.intent.payload.file_ids);
  report.checks.push('并发请求结束后原意图、原任务及可用上传组准确回读');
  report.result = 'PASS';
  process.stdout.write(JSON.stringify({ result: report.result, checks: report.checks.length,
    concurrent_requests: observations.length, new_tasks: 0, new_model_calls: 0 }) + '\n');
} finally {
  if (report.result !== 'PASS') report.result = 'FAIL';
  report.finished_at = new Date().toISOString();
  await writeFile(values.report, JSON.stringify(report, null, 2));
  await context.dispose();
}
