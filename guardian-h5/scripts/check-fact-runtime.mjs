import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const validator = new Ajv2020({ strict: true, strictTypes: false, strictRequired: false,
  allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
for (const [name, path] of [['dataset.schema.json', 'data-design/dataset.schema.json'],
  ['case-extension.schema.json', 'data-design/case-extension.schema.json'],
  ['protocol.schema.json', 'backend-design/protocol.schema.json'],
  ['v2-protocol.schema.json', 'backend-design/v2-protocol.schema.json']]) {
  validator.addSchema(JSON.parse(await readFile(path, 'utf8')), name);
}
validator.addSchema(JSON.parse(await readFile('data-design/dataset.schema.json', 'utf8')), 'data-design/dataset.schema.json');
validator.addSchema(JSON.parse(await readFile('data-design/case-extension.schema.json', 'utf8')), 'data-design/case-extension.schema.json');
const result = await promisify(execFile)('backend/.venv/bin/python', ['backend/tests/verify_fact_runtime.py', '--frontend-json'],
  { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
const value = JSON.parse(result.stdout);
assert.equal(value.report.status, 'PASS');
assert.equal(value.responses.length, value.report.actual_projections);
for (const response of value.responses) {
  const check = validator.getSchema(`v2-protocol.schema.json#/$defs/${response.schema}`);
  assert.ok(check, '正式事实读取 Schema 缺少定义。');
  assert.equal(check(response.response), true, '实际事实投影未通过前端 AJV。');
}
const report = { ...value.report, frontend_ajv: 'PASS' };
await writeFile('.planning-work/t41-fact-read.json', JSON.stringify(report, null, 2) + '\n');
process.stdout.write(JSON.stringify(report) + '\n');
