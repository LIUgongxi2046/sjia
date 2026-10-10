import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const validator = new Ajv2020({ strict: true, allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(JSON.parse(await readFile('backend/api/family-read.schema.json', 'utf8')), 'family-read.schema.json');
validator.addSchema(JSON.parse(await readFile('backend/relations/read.schema.json', 'utf8')), 'relation-read.schema.json');
for (const name of ['HomeView', 'AlertList', 'AlertDetailView', 'AlertReadReceipt', 'DraftView', 'DraftList']) assert.ok(validator.getSchema(`family-read.schema.json#/$defs/${name}`));
for (const name of ['RelationList', 'RelationView', 'ApplicationList', 'ApplicationView', 'ApplicationRequestView', 'ApplicationRequestList', 'ApplicationInput', 'ApplicationAction', 'WriteReceipt', 'IntentView']) assert.ok(validator.getSchema(`relation-read.schema.json#/$defs/${name}`));
const result = await promisify(execFile)('backend/.venv/bin/python', ['backend/tests/verify_family_read_runtime.py', '--frontend-json'],
  { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
const value = JSON.parse(result.stdout);
assert.equal(value.report.status, 'PASS');
assert.ok(value.responses.length > 0);
for (const response of value.responses) {
  const schemaName = ['RelationList', 'RelationView', 'ApplicationList', 'ApplicationView', 'ApplicationRequestView', 'ApplicationRequestList'].includes(response.schema) ? 'relation-read.schema.json' : 'family-read.schema.json';
  const check = validator.getSchema(`${schemaName}#/$defs/${response.schema}`);
  assert.equal(check(response.response), true, '实际家庭读取结果未通过前端 AJV 校验。');
}
const report = { ...value.report, frontend_ajv: 'PASS', actual_projections: value.responses.length };
await writeFile('.planning-work/t54-family-read.json', JSON.stringify(report, null, 2) + '\n');
process.stdout.write(JSON.stringify(report) + '\n');
