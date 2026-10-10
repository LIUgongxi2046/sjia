import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { request } from 'playwright';

const output = '../.qa-work/QA03/recognition-inputs.json';
const startedAt = new Date().toISOString();
const client = await request.newContext({ baseURL: 'http://127.0.0.1:8000' });
const bootstrap = await client.get('/api/auth/bootstrap');
assert.equal(bootstrap.status(), 200);
const token = (await bootstrap.json()).csrf_token;
const login = await client.post('/api/auth/login', {
  data: { username: 'acceptance-family', password: 'Local-accept-2026' },
  headers: { 'X-CSRFToken': token },
});
assert.equal(login.status(), 200);
const session = await client.get('/api/auth/bootstrap');
const csrf = (await session.json()).csrf_token;

// 全部请求在输入校验阶段拒绝，不包含病例内容或有效识别输入。
const inputs = [
  ['voice-null-body', 'voice-structure', null],
  ['voice-array-body', 'voice-structure', []],
  ['voice-string-body', 'voice-structure', ''],
  ['voice-number-body', 'voice-structure', 0],
  ['voice-missing-text', 'voice-structure', {}],
  ['voice-null-text', 'voice-structure', { text: null }],
  ['voice-empty-text', 'voice-structure', { text: '' }],
  ['voice-whitespace', 'voice-structure', { text: ' \t\r\n' }],
  ['voice-unicode-whitespace', 'voice-structure', { text: '\u3000\u00a0' }],
  ['voice-number-text', 'voice-structure', { text: 0 }],
  ['voice-boolean-text', 'voice-structure', { text: true }],
  ['voice-array-text', 'voice-structure', { text: [] }],
  ['voice-object-text', 'voice-structure', { text: {} }],
  ['voice-extra-field', 'voice-structure', { text: '', provider: '' }],
  ['voice-over-limit', 'voice-structure', { text: ' '.repeat(8001) }],
  ['report-null-body', 'report-ocr', null],
  ['report-array-body', 'report-ocr', []],
  ['report-string-body', 'report-ocr', ''],
  ['report-number-body', 'report-ocr', 0],
  ['report-missing-id', 'report-ocr', {}],
  ['report-null-id', 'report-ocr', { file_id: null }],
  ['report-empty-id', 'report-ocr', { file_id: '' }],
  ['report-whitespace-id', 'report-ocr', { file_id: ' \t' }],
  ['report-invalid-uuid', 'report-ocr', { file_id: 'invalid' }],
  ['report-number-id', 'report-ocr', { file_id: 0 }],
  ['report-boolean-id', 'report-ocr', { file_id: true }],
  ['report-array-id', 'report-ocr', { file_id: [] }],
  ['report-object-id', 'report-ocr', { file_id: {} }],
  ['report-extra-field', 'report-ocr', { file_id: '', provider: '' }],
];
const results = [];
for (const [id, endpoint, input] of inputs) {
  const response = await client.post(`/api/recognition/${endpoint}`, {
    data: JSON.stringify(input),
    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
  });
  const responseText = await response.text();
  const body = JSON.parse(responseText);
  const errors = body.field_errors;
  const passed = response.status() === 422 && body.code === 'RECOGNITION_INPUT_INVALID'
    && body.retryable === false && body.recovery === 'review_input'
    && response.headers()['cache-control'] === 'no-store, private'
    && !Object.hasOwn(body, 'candidates') && Array.isArray(errors) && errors.length > 0
    && errors.every(error => Object.keys(error).sort().join(',') === 'code,path'
      && typeof error.path === 'string' && typeof error.code === 'string');
  results.push({ id, status: passed ? 'PASS' : 'FAIL', http_status: response.status(),
    code: body.code, field_errors: errors, response_sha256: createHash('sha256').update(responseText).digest('hex') });
}
for (const endpoint of ['voice-structure', 'report-ocr']) {
  for (const [name, input] of [['incomplete-json', '{'], ['trailing-comma', '{"text": "",}'], ['invalid-unicode', '{"text":"\\uZZZZ"}']]) {
    const response = await client.post(`/api/recognition/${endpoint}`, {
      data: Buffer.from(input), headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
    });
    const responseText = await response.text();
    const body = JSON.parse(responseText);
    const passed = response.status() === 400 && body.code === 'REQUEST_BODY_INVALID'
      && body.retryable === false && body.recovery === 'review_input'
      && response.headers()['cache-control'] === 'no-store, private'
      && Array.isArray(body.field_errors) && body.field_errors.length === 0
      && !Object.hasOwn(body, 'candidates');
    results.push({ id: `${endpoint}-${name}`, status: passed ? 'PASS' : 'FAIL',
      http_status: response.status(), code: body.code,
      response_sha256: createHash('sha256').update(responseText).digest('hex') });
  }
}
await client.dispose();
await mkdir('../.qa-work/QA03', { recursive: true });
await writeFile(output, JSON.stringify({
  startedAt, finishedAt: new Date().toISOString(), evidenceType: 'API',
  inputSource: 'invalid nonclinical request types, empty values and invalid identifiers; existing local account',
  clinicalWrites: false, providerCalls: false,
  scope: 'request validation and HTTP errors; Provider behavior and model quality remain unverified',
  results,
}, null, 2));
console.log(JSON.stringify({ checks: results.length, passed: results.filter(item => item.status === 'PASS').length,
  failures: results.filter(item => item.status !== 'PASS') }, null, 2));
assert.ok(results.every(item => item.status === 'PASS'), '识别请求校验存在未通过用例');
