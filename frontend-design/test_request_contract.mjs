// Synthetic CONTRACT-01 regression; no network, service, DB, browser, or patient data.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import Ajv2020 from '../guardian-h5/node_modules/ajv/dist/2020.js';
import addFormats from '../guardian-h5/node_modules/ajv-formats/dist/index.js';
const root = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
const primary = pathToFileURL(path.join(root, 'backend-design/v2-protocol.schema.json')).href;
const ajv = new Ajv2020({ strict: true, strictTypes: false, strictRequired: false, allowUnionTypes: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false, allErrors: true });
addFormats(ajv);
const loaded = new Map();
function load(url) {
  url = url.split('#')[0];
  if (loaded.has(url)) return;
  const filename = fileURLToPath(url);
  assert.ok(filename.startsWith(root + path.sep), 'schema refs must stay inside isolated copy');
  const document = JSON.parse(fs.readFileSync(filename, 'utf8'));
  loaded.set(url, document);
  ajv.addSchema(document, url);
  function visit(node) {
    if (!node || typeof node !== 'object') return;
    if (typeof node.$ref === 'string' && !node.$ref.startsWith('#')) {
      const resolved = new URL(node.$ref, url);
      assert.equal(resolved.protocol, 'file:', 'no network schema retrieval');
      load(resolved.href);
    }
    for (const value of Object.values(node)) visit(value);
  }
  visit(document);
}
load(primary);
const check = ajv.getSchema(primary + '#/$defs/RequestView');
assert.ok(check);
const base = {
  intent_id: '00000000-0000-4000-8000-000000000001',
  request_id: '00000000-0000-4000-8000-000000000002',
  operation_id: 'disposition_create',
  target_key: '/patients/00000000-0000-4000-8000-000000000003/dispositions',
  prepared_at: '2026-10-13T00:00:00Z', state: 'pending', may_retry: false, receipt: null, error_code: null,
  disposition_input: {
    targets: [{ target_type: 'file_object', target_id: '00000000-0000-4000-8000-000000000004', target_version: '1' }],
    policy_id: '00000000-0000-4000-8000-000000000005', evidence_ref: 'fixture-only',
  },
};
const results = [];
function test(name, expected, mutate = () => {}) {
  const value = structuredClone(base); mutate(value);
  const before = JSON.stringify(value);
  const actual = check(value);
  assert.equal(JSON.stringify(value), before, name + ': validator must not mutate value');
  assert.equal(actual, expected, name + ': ' + JSON.stringify(check.errors));
  results.push({ name, expected, actual, errors: structuredClone(check.errors) });
}
test('valid disposition input', true);
test('omitted disposition input remains valid', true, value => { delete value.disposition_input; });
test('unrelated operation without disposition input', true, value => { value.operation_id = 'archive_document_create'; delete value.disposition_input; });
test('explicit null disposition input rejected', false, value => { value.disposition_input = null; });
test('non-object disposition input rejected', false, value => { value.disposition_input = 'fixture'; });
test('empty targets rejected', false, value => { value.disposition_input.targets = []; });
test('duplicate targets rejected', false, value => { value.disposition_input.targets.push(structuredClone(value.disposition_input.targets[0])); });
test('different targets allowed', true, value => { value.disposition_input.targets.push({ target_type: 'purpose_copy', target_id: '00000000-0000-4000-8000-000000000006', target_version: '2' }); });
test('malformed policy UUID rejected', false, value => { value.disposition_input.policy_id = 'not-a-uuid'; });
test('null policy rejected', false, value => { value.disposition_input.policy_id = null; });
test('empty evidence rejected', false, value => { value.disposition_input.evidence_ref = ''; });
test('null evidence rejected', false, value => { value.disposition_input.evidence_ref = null; });
for (const name of ['targets', 'policy_id', 'evidence_ref']) test('missing input field ' + name, false, value => { delete value.disposition_input[name]; });
for (const name of ['target_type', 'target_id', 'target_version']) {
  test('missing target field ' + name, false, value => { delete value.disposition_input.targets[0][name]; });
  test('null target field ' + name, false, value => { value.disposition_input.targets[0][name] = null; });
  test('empty target field ' + name, false, value => { value.disposition_input.targets[0][name] = ''; });
}
test('extra input field rejected', false, value => { value.disposition_input.extra = true; });
test('extra target field rejected', false, value => { value.disposition_input.targets[0].extra = true; });
test('extra request field rejected', false, value => { value.extra = true; });
for (const name of loaded.get(primary).$defs.RequestView.required) test('request required unchanged ' + name, false, value => { delete value[name]; });
for (const state of ['pending', 'processing', 'committed', 'failed', 'unknown', 'not_found']) test('generic state unchanged ' + state, true, value => { value.state = state; });
test('invalid state rejected', false, value => { value.state = 'prepared'; });
test('nullable error code accepted', true, value => { value.error_code = null; });
test('string error code accepted', true, value => { value.error_code = 'FIXTURE_FAILURE'; });
test('invalid may_retry type rejected without coercion', false, value => { value.may_retry = 'false'; });
test('positive generation string accepted', true, value => { value.generation = '1'; });
test('zero generation rejected', false, value => { value.generation = '0'; });
test('valid payload digest accepted', true, value => { value.payload_sha256 = '0'.repeat(64); });
test('malformed payload digest rejected', false, value => { value.payload_sha256 = 'invalid'; });
const result = { status: 'PASS', scope: 'pure JSON Schema fixture validation; no API, DB, service, browser or provider', cases: results.length,
  positive: results.filter(result => result.expected).length, negative: results.filter(result => !result.expected).length,
  loadedSchemaFiles: [...loaded.keys()].map(url => path.relative(root, fileURLToPath(url))), results };
console.log(JSON.stringify({ ...result, results: results.map(({ name, expected, actual }) => ({ name, expected, actual })) }, null, 2));
