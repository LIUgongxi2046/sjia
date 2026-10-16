// Local synthetic read-only report DOM/contract checks. No browser/server/provider.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const { ClinicalMetricsPanel } = await import('../src/features/support/ClinicalMetricsPanel.tsx');
const { validateApiValue } = await import('../src/api/schema.ts');
const backend = fileURLToPath(new URL('../../backend/', import.meta.url));
const report = JSON.parse(execFileSync(`${backend}/.venv/bin/python`, ['-c', `
import json
from tests.test_clinical_metrics_v2 import synthetic_capture_v2
from case_details.clinical_metrics import evaluate
print(json.dumps(evaluate(*synthetic_capture_v2())))
`], { cwd: backend, encoding: 'utf8' }));
// The fixture simulates an already validated runtime response. Qualification
// itself is exercised in the backend actual saved replay tests, never by UI.
report.binding.dataset_version_id = '11111111-1111-4111-8111-111111111111';
report.execution_steps.forEach((step, index) => { step.task_id = `22222222-2222-4222-8222-${String(index).padStart(12, '0')}`; });
report.status = 'CALCULATED_FROM_VERIFIED_REPLAYS';
report.evidence_qualification = 'QUALIFIED_SAVED_REPLAY_BINDINGS';
validateApiValue('v2-protocol.schema.json#/$defs/ClinicalMetricsReport', report);
const document = new JSDOM(renderToStaticMarkup(React.createElement(ClinicalMetricsPanel, { report }))).window.document;
const text = document.body.textContent;
assert.match(text, /合成软件测试资料/);
assert.match(text, /不表示临床有效性已获批准/);
assert.match(text, /0.5（1\/2 个已知结局提示）/);
assert.match(text, /1（1\/1 个可评估阳性事件）/);
assert.match(text, /首次 \/ 重复提示1 \/ 1/);
assert.match(text, /-600 秒（负值表示更早）/);
assert.match(text, /无法计算（分母为空）/);
assert.match(text, /置信区间未计算/);
assert.match(text, /独立验证集合：1 人 \/ 1 区间/);
assert.match(text, /亚组 treatment/);
assert.match(text, /任务 22222222-2222-4222-8222-000000000000/);
assert.match(text, new RegExp(`执行摘要 ${'a'.repeat(64)}`));
assert.match(text, new RegExp(`执行摘要 ${'b'.repeat(64)}`));
assert.ok(text.includes(`资料摘要 ${report.execution_steps[0].corpus_sha256}`));
assert.equal(document.querySelectorAll('button,input,form,a').length, 0);
for (const change of [{ clinical_validation: true }, { evidence_qualification: 'DECLARED_ONLY' },
  { status: 'APPROVED_FOR_CLINICAL_USE' }]) {
  assert.throws(() => validateApiValue('v2-protocol.schema.json#/$defs/ClinicalMetricsReport', { ...report, ...change }));
}
assert.throws(() => validateApiValue('v2-protocol.schema.json#/$defs/ClinicalMetricsReport', {
  ...report, dynamic: { ...report.dynamic, confidence_interval: [0.1, 0.9] },
}));
console.log('PASS: 6 clinical report DOM/contract groups: denominators, missingness, partitions, distinct hashes, read-only output and no clinical/CI approval claim');
