import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { request } from 'playwright/test';

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.planning-work/t04-field-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const context = await request.newContext({ baseURL: 'http://127.0.0.1:5183' });
const bootstrap = await (await context.get('/api/auth/bootstrap')).json();
const login = await context.post('/api/auth/login', { data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, headers: { 'X-CSRFToken': bootstrap.csrf_token } });
assert.equal(login.status(), 200);
const results = [];
for (const [kind, codes] of [['observation', ['temp', 'rash', 'joint_discomfort', 'observation_note']],
  ['therapy', ['medication_name', 'therapy_note', 'therapy_status', 'therapy_start', 'therapy_end', 'therapy_source',
    'dose', 'dose_unit', 'concentration_text', 'route_text', 'frequency_text', 'interval_value', 'interval_unit']]]) {
  const response = await context.get(`/api/patients/${fixture.patient_id}/field-config?kind=${kind}`);
  assert.equal(response.status(), 200);
  const result = await response.json();
  results.push({ kind, status: response.status(), result });
}
await writeFile('../.planning-work/t04-field-config-http.json', JSON.stringify({ mode: 'SIMULATION', checked_at: new Date().toISOString(), results }, null, 2));
await context.dispose();
for (const [index, codes] of [[0, ['temp', 'rash', 'joint_discomfort', 'observation_note']],
  [1, ['medication_name', 'therapy_note', 'therapy_status', 'therapy_start', 'therapy_end', 'therapy_source',
    'dose', 'dose_unit', 'concentration_text', 'route_text', 'frequency_text', 'interval_value', 'interval_unit']]]) {
  assert.equal(results[index].result.state, 'available', JSON.stringify(results[index]));
  assert.deepEqual(results[index].result.fields.map(field => field.field_code), codes);
  assert.ok(results[index].result.fields.every(field => field.writable));
}
process.stdout.write('PASS 本人观察与治疗字段通过实际会话、准确字段权限和当前配置读取\n');
