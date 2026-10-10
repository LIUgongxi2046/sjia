import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';

const validator = new Ajv2020({ strict: true, allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(JSON.parse(await readFile('backend/records/search.schema.json', 'utf8')), 'record-search.schema.json');
validator.addSchema(JSON.parse(await readFile('backend/summaries/read.schema.json', 'utf8')), 'summary-read.schema.json');
for (const name of ['SummaryInput', 'SummaryList', 'SummaryView', 'SummaryTaskView', 'SummaryRequestView', 'SummaryRequestList', 'SummaryReceipt']) {
  assert.ok(validator.getSchema(`summary-read.schema.json#/$defs/${name}`));
}
const result = await promisify(execFile)('backend/.venv/bin/python', ['backend/tests/verify_summary_runtime.py', '--frontend-json'],
  { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
const value = JSON.parse(result.stdout);
assert.equal(value.report.status, 'PASS');
assert.ok(value.responses.length > 0);
for (const response of value.responses) {
  const check = validator.getSchema(`summary-read.schema.json#/$defs/${response.schema}`);
  assert.equal(check(response.response), true, '实际摘要返回资料未通过前端 AJV 校验。');
}
const report = { ...value.report, frontend_ajv: 'PASS' };
await writeFile('.planning-work/t54-summary-read.json', JSON.stringify(report, null, 2) + '\n');
process.stdout.write(JSON.stringify(report) + '\n');
