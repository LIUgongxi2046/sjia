import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { validateRecordFactReferences } from '../src/api/records.ts';
import { validateAnalyteRead, validateCorrectionValue } from '../src/api/recordSearchSchema.ts';
import { correctionFields, correctionContentMatches } from '../src/features/records/correctionFields.ts';

const validator = new Ajv2020({ strict: true, allowUnionTypes: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(JSON.parse(await readFile('backend/records/search.schema.json', 'utf8')), 'record-search.schema.json');
const input = validator.getSchema('record-search.schema.json#/$defs/RecordSearchInput');
const page = validator.getSchema('record-search.schema.json#/$defs/RecordSearchPage');
assert.ok(input && page);
for (const name of ['RecordCorrectionField', 'RecordCorrectionInput', 'RecordCorrectionReceipt', 'CorrectionDraft']) {
  assert.ok(validator.getSchema(`record-search.schema.json#/$defs/${name}`));
}
const result = await promisify(execFile)('backend/.venv/bin/python', ['backend/tests/verify_record_search_runtime.py', '--frontend-json'],
  { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
const value = JSON.parse(result.stdout);
assert.equal(value.report.status, 'PASS');
assert.ok(value.pages.length > 0);
for (const current of value.pages) {
  assert.equal(input(current.input), true, '实际查询条件未通过前端 AJV 校验。');
  assert.equal(page(current.response), true, '实际查询结果未通过前端 AJV 校验。');
  for (const record of current.response.records) validateRecordFactReferences(record.detail);
}
for (const current of value.reads) {
  const check = validator.getSchema(`record-search.schema.json#/$defs/${current.schema}`);
  assert.ok(check);
  assert.equal(check(current.response), true, '实际记录详情或历史未通过前端 AJV 校验。');
  if (current.schema === 'RecordView') {
    validateRecordFactReferences(current.response);
    const projected = correctionFields(current.response.fields);
    for (const field of projected) assert.equal(validateCorrectionValue('RecordCorrectionField', field), field);
    assert.equal(correctionContentMatches(projected, current.response.fields), true, '已有更正字段的原值或医学时间发生变化。');
    assert.deepEqual(projected.map(field => field.sample_time_origin), current.response.fields.map(field => field.sample_time_origin));
  }
  if (current.schema === 'AnalyteTrendView') assert.equal(validateAnalyteRead(current.response), current.response);
}
const report = { ...value.report, frontend_ajv: 'PASS', frontend_fact_reference_validation: 'PASS', frontend_correction_projection_validation: 'PASS', actual_query_pages: value.pages.length, actual_read_projections: value.reads.length };
await writeFile('.planning-work/t54-record-search.json', JSON.stringify(report, null, 2) + '\n');
process.stdout.write(JSON.stringify(report) + '\n');
