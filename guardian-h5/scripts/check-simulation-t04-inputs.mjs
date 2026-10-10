import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { request } from 'playwright';

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.planning-work/t04-field-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const api = await request.newContext({ baseURL: 'http://127.0.0.1:8001', timeout: 30000 });
const bootstrap = await api.get('/api/auth/bootstrap'); assert.equal(bootstrap.status(), 200);
const token = (await bootstrap.json()).csrf_token;
const login = await api.post('/api/auth/login', { headers: { 'X-CSRFToken': token }, data: {
  username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD } });
assert.equal(login.status(), 200); const csrf = (await login.json()).csrf_token;
const headers = { 'X-CSRFToken': csrf };
const configuration = await api.get(`/api/patients/${fixture.patient_id}/field-config?kind=observation`);
assert.equal(configuration.status(), 200); const config = await configuration.json();
assert.equal(config.state, 'available');
const field = { client_field_id: randomUUID(), field_code: 'temp', concept_code: null, dictionary_entry_id: null,
  original_name: 'SIMULATION体温', value_type: 'decimal', value_state: 'known', raw_text: '38.0', raw_numeric: '38.0',
  categorical_value: null, qualifier: 'eq', raw_unit: 'C', method: null, reference_interval: null, report_flag: null,
  medical_time: null, sample_time_origin: 'observation', anchor_id: null, candidate_field_id: null };
const baseline = { kind: 'observation', capture_method: 'manual', origin_type: 'self_report',
  field_config_version: config.config_version, fields: [field], file_ids: [] };
const target = `/api/patients/${fixture.patient_id}/records`;
const checks = [];
async function denied(name, payload, code) {
  const requestId = randomUUID();
  const prepared = await api.post('/api/write-intents', { headers, data: { operation_id: 'record_create',
    target_key: target, request_id: requestId, schema_version: 'back-json-1', payload } });
  assert.equal(prepared.status(), 201, await prepared.text()); const intent = await prepared.json();
  const result = await api.post(target, { headers: { ...headers, 'X-Write-Intent': intent.intent_id,
    'X-Intent-Generation': intent.generation, 'Idempotency-Key': requestId }, data: payload });
  assert.equal(result.status(), 422, await result.text()); assert.equal((await result.json()).code, code);
  checks.push(name); process.stdout.write('PASS ' + name + '\n');
}
try {
  await denied('缺少准确字段配置拒绝提交', { ...baseline, field_config_version: null }, 'FIELD_CONFIG_VERSION_REQUIRED');
  await denied('其他登记类别配置拒绝提交', { ...baseline, field_config_version: fixture.configs.report }, 'CONFIG_NOT_AVAILABLE');
  await denied('NULL已知数值拒绝提交', { ...baseline, fields: [{ ...field, raw_numeric: null }] }, 'CONFIG_INPUT_INVALID');
  await denied('Boolean数值拒绝提交', { ...baseline, fields: [{ ...field, raw_numeric: true }] }, 'CONFIG_INPUT_INVALID');
  await denied('非Decimal数值拒绝提交', { ...baseline, fields: [{ ...field, raw_numeric: 'NaN' }] }, 'CONFIG_INPUT_INVALID');
  await denied('没有获准的单位拒绝提交', { ...baseline, fields: [{ ...field, raw_unit: 'SIM/unit' }] }, 'CONFIG_FIELD_UNIT_INVALID');
  await denied('超出配置次数拒绝提交', { ...baseline, fields: [field, { ...field, client_field_id: randomUUID() }] }, 'CONFIG_FIELD_NOT_WRITABLE');
  await denied('未获准枚举拒绝提交', { ...baseline, fields: [{ ...field, field_code: 'rash', value_type: 'categorical',
    raw_numeric: null, raw_unit: null, categorical_value: 'SIM_INVALID' }] }, 'CONFIG_FIELD_VALUE_INVALID');
  const reportConfig = await api.get(`/api/patients/${fixture.patient_id}/field-config?kind=report`);
  assert.equal(reportConfig.status(), 200);
  await denied('项目代码需要本人准确字典选择', { ...baseline, kind: 'report', field_config_version: (await reportConfig.json()).config_version,
    fields: [{ ...field, field_code: 'report_item', concept_code: 'simulation_platelet', dictionary_entry_id: null }] }, 'CONFIG_DICTIONARY_SELECTION_REQUIRED');
  await writeFile('../.planning-work/t04-input-boundaries.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS',
    checked_at: new Date().toISOString(), checks, clinical_approval: 'NOT_RUN', visual: 'NOT_RUN' }, null, 2));
} finally { await api.dispose(); }
