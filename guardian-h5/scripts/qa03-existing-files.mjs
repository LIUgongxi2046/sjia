import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { request } from 'playwright';

const startedAt = new Date().toISOString();
const client = await request.newContext({ baseURL: 'http://127.0.0.1:8000' });
const bootstrap = await client.get('/api/auth/bootstrap');
const token = (await bootstrap.json()).csrf_token;
const login = await client.post('/api/auth/login', {
  data: { username: 'acceptance-family', password: 'Local-accept-2026' },
  headers: { 'X-CSRFToken': token },
});
assert.equal(login.status(), 200);
const session = await client.get('/api/auth/bootstrap');
const csrf = (await session.json()).csrf_token;
const relations = await client.get('/api/relations');
assert.equal(relations.status(), 200);
const patient = (await relations.json()).relations.find(item => item.display_id === '模拟患儿A');
assert.ok(patient);
const records = await client.post(`/api/patients/${patient.patient_id}/records/search`, {
  data: { limit: 100 }, headers: { 'X-CSRFToken': csrf },
});
assert.equal(records.status(), 200);
const attached = (await records.json()).records.find(item => item.detail.attachments.length > 0);
assert.ok(attached, '已有资料需要包含可读取的原图');
const fileId = attached.detail.attachments[0].file_id;
const path = `/api/files/${fileId}/content`;
const full = await client.get(path);
const bytes = await full.body();
const results = [{ id: 'existing-file-complete',
  status: full.status() === 200 && bytes.length > 0
    && full.headers()['content-type'].startsWith('image/')
    && full.headers()['cache-control'] === 'no-store, private' ? 'PASS' : 'FAIL',
  http_status: full.status(), bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') }];
assert.equal(full.status(), 200);
assert.ok(bytes.length > 32);
for (const [id, range, start, end] of [
  ['prefix', 'bytes=0-15', 0, 15],
  ['suffix', 'bytes=-16', bytes.length - 16, bytes.length - 1],
  ['open-end', `bytes=${bytes.length - 16}-`, bytes.length - 16, bytes.length - 1],
  ['oversize-suffix', `bytes=-${bytes.length + 16}`, 0, bytes.length - 1],
  ['oversize-end', `bytes=0-${bytes.length + 16}`, 0, bytes.length - 1],
]) {
  const response = await client.get(path, { headers: { Range: range } });
  const body = await response.body();
  const passed = response.status() === 206 && body.equals(bytes.subarray(start, end + 1))
    && response.headers()['content-range'] === `bytes ${start}-${end}/${bytes.length}`
    && response.headers()['accept-ranges'] === 'bytes'
    && response.headers()['cache-control'] === 'no-store, private';
  results.push({ id: `existing-file-${id}`, status: passed ? 'PASS' : 'FAIL', http_status: response.status(),
    range, content_range: response.headers()['content-range'], bytes: body.length });
}
const beyond = await client.get(path, { headers: { Range: `bytes=${bytes.length}-` } });
results.push({ id: 'existing-file-out-of-range', status: beyond.status() === 416
  && beyond.headers()['content-range'] === `bytes */${bytes.length}`
  && beyond.headers()['cache-control'] === 'no-store, private' ? 'PASS' : 'FAIL',
  http_status: beyond.status(), content_range: beyond.headers()['content-range'] });
for (const [id, range] of [
  ['invalid-digits', 'bytes=x-y'], ['missing-hyphen', 'bytes=1'], ['empty-suffix', 'bytes=-0'],
  ['reversed', 'bytes=15-0'], ['multiple', 'bytes=0-3,8-15'], ['unknown-unit', 'items=0-15'],
]) {
  const response = await client.get(path, { headers: { Range: range } });
  results.push({ id: `existing-file-${id}`, status: response.status() === 416
    && response.headers()['content-range'] === `bytes */${bytes.length}`
    && response.headers()['cache-control'] === 'no-store, private' ? 'PASS' : 'FAIL',
    http_status: response.status(), range, content_range: response.headers()['content-range'] });
}
await client.dispose();
await mkdir('../.qa-work/QA03', { recursive: true });
await writeFile('../.qa-work/QA03/existing-files.json', JSON.stringify({
  startedAt, finishedAt: new Date().toISOString(), evidenceType: 'API', existingDataOnly: true,
  inputSource: 'existing attachment from an authorized existing local record', fileId, results,
}, null, 2));
console.log(JSON.stringify({ checks: results.length, passed: results.filter(item => item.status === 'PASS').length,
  failures: results.filter(item => item.status !== 'PASS') }, null, 2));
assert.ok(results.every(item => item.status === 'PASS'), '已有原图读取存在未通过用例');
