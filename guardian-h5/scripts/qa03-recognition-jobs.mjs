import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { request } from 'playwright';

const client = await request.newContext({ baseURL: 'http://127.0.0.1:8001' });
let csrf = (await (await client.get('/api/auth/bootstrap')).json()).csrf_token;
const login = await client.post('/api/auth/login', { data: { username: 'acceptance-family', password: 'Local-accept-2026' }, headers: { 'X-CSRFToken': csrf } });
assert.equal(login.status(), 200);
csrf = (await (await client.get('/api/auth/bootstrap')).json()).csrf_token;
async function api(path, data, expected = 200) {
  const response = data === undefined ? await client.get(path) : await client.post(path, { data, headers: { 'X-CSRFToken': csrf } });
  const body = await response.json();
  assert.equal(response.status(), expected, JSON.stringify(body));
  return body;
}
const patient = (await api('/api/relations')).relations.find(item => item.display_id === '模拟患儿A').patient_id;
const image = await readFile('../.qa-work/simulated-report.png');
async function group(pages = 1) {
  const created = await api(`/api/patients/${patient}/uploads`, {}, 201);
  const files = [];
  for (let ordinal = 1; ordinal <= pages; ordinal++) {
  const query = new URLSearchParams({ checksum: createHash('sha256').update(image).digest('hex'), size: String(image.length), media_type: 'image/png', ordinal: String(ordinal) });
  const uploaded = await client.put(`/api/uploads/${created.group_id}/files/${randomUUID()}?${query}`, {
    data: image, headers: { 'Content-Type': 'application/octet-stream', 'X-CSRFToken': csrf },
  });
  assert.equal(uploaded.status(), 201, await uploaded.text());
  files.push(await uploaded.json());
  }
  await api(`/api/uploads/${created.group_id}/complete`, {});
  return { ...created, files };
}
async function waitTask(id) {
  const states = [];
  for (let attempt = 0; attempt < 180; attempt++) {
    const task = await api(`/api/tasks/${id}`);
    states.push(task.status);
    if (['awaiting_confirmation', 'completed', 'failed', 'cancelled', 'superseded'].includes(task.status)) return { task, states };
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error('识别任务没有在限定时间内完成');
}
const results = [];
const original = await group(2);
const input = { file_group_id: original.group_id, file_group_version: original.version };
const created = await api('/api/recognition-jobs', input, 202);
assert.equal(created.status, 'accepted');
assert.equal((await api('/api/recognition-jobs', input, 202)).task_id, created.task_id);
const { task, states } = await waitTask(created.task_id);
assert.equal(task.status, 'awaiting_confirmation', JSON.stringify(task));
assert.equal(task.result_type, 'candidate_set');
const candidates = await api(`/api/candidate-sets/${task.result_ref}`);
assert.equal(candidates.candidates.length, 4);
assert.ok(candidates.candidates.some(item => item.proposed_field.raw_unit === 'ng/mL'));
assert.ok(candidates.candidates.every(item => item.raw_text && item.proposed_field && item.decision === null));
results.push({ id: 'actual-queue-ark-candidates-idempotency', status: 'PASS', task, states, candidates });
const decisions = candidates.candidates.map((item, index) => ({ candidate_field_id: item.candidate_field_id, decision: ['modified', 'accepted', 'unknown', 'excluded'][index] }));
const fields = candidates.candidates.slice(0, 3).map((item, index) => ({ ...item.proposed_field,
  ...(index === 0 ? { raw_numeric: '151', raw_text: '151', value_type: 'decimal', value_state: 'known' } : {}),
  candidate_field_id: item.candidate_field_id,
}));
const target = `/api/patients/${patient}/records`;
const payload = { kind: 'report', capture_method: 'ocr_confirmed', candidate_set_id: task.result_ref, decisions,
  fields, file_ids: original.files.map(item => item.file_id) };
const requestId = randomUUID();
const intent = await api('/api/write-intents', { operation_id: 'record_create', target_key: target, request_id: requestId, payload, schema_version: 'back-json-1' }, 201);
const saved = await client.post(target, { data: payload, headers: { 'X-CSRFToken': csrf, 'X-Write-Intent': intent.intent_id, 'X-Intent-Generation': intent.generation, 'Idempotency-Key': requestId } });
assert.equal(saved.status(), 201, await saved.text());
const record = await api(`/api/records/${(await saved.json()).record_id}`);
assert.equal(record.fields.length, 3);
assert.equal(record.fields[0].raw_numeric, '151');
assert.equal(record.fields[2].value_state, 'unknown');
assert.equal(record.fields[2].raw_numeric, null);
assert.ok(record.fields.every(item => item.source_anchor && item.confirmation_id));
assert.equal((await api(`/api/tasks/${task.task_id}`)).status, 'completed');
results.push({ id: 'all-four-decisions-save-and-read-completes-task', status: 'PASS', record });
await api(`/api/uploads/${original.group_id}/revisions`, {}, 201);
assert.equal((await api(`/api/candidate-sets/${task.result_ref}`)).status, 'superseded');
results.push({ id: 'file-group-revision-invalidates-candidates', status: 'PASS' });
const pendingGroup = await group();
const pending = await api('/api/recognition-jobs', { file_group_id: pendingGroup.group_id, file_group_version: pendingGroup.version }, 202);
await api(`/api/tasks/${pending.task_id}/cancel`, {});
const cancelled = await waitTask(pending.task_id);
assert.equal(cancelled.task.status, 'cancelled');
assert.equal(cancelled.task.result_ref, null);
results.push({ id: 'cancel-pending-recognition-no-candidates', status: 'PASS', task: cancelled.task });
const retry = await api(`/api/tasks/${pending.task_id}/retry`, {});
assert.notEqual(retry.task_id, pending.task_id);
assert.equal((await api(`/api/tasks/${pending.task_id}/retry`, {})).task_id, retry.task_id);
const retried = await waitTask(retry.task_id);
assert.equal(retried.task.status, 'awaiting_confirmation', JSON.stringify(retried));
await api(`/api/tasks/${retry.task_id}/cancel`, {});
assert.equal((await api(`/api/candidate-sets/${retried.task.result_ref}`)).status, 'cancelled');
assert.equal((await api(`/api/tasks/${pending.task_id}`)).status, 'cancelled');
results.push({ id: 'explicit-retry-new-task-cancel-candidates', status: 'PASS', task: retried.task });
await writeFile('../.qa-work/QA03/recognition-jobs.json', JSON.stringify({
  testedAt: new Date().toISOString(), database: 'sjia_qa03', provider: 'actual Ark', results,
}, null, 2));
await client.dispose();
console.log(JSON.stringify({ checks: results.length, passed: results.length }));
