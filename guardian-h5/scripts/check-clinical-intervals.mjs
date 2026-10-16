// Synthetic offline DOM and API contract checks. No server, browser, database or provider.
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
const fixtures = JSON.parse(execFileSync(`${backend}/.venv/bin/python`, ['-c', `
from copy import deepcopy
import json
from unittest.mock import patch
from tests.test_clinical_intervals import interval_fixture, metric_spec, protocol_payload
from tests.test_clinical_metrics_v2 import synthetic_capture_v2
from case_details.clinical_metrics import evaluate
from case_details.clinical_intervals import evaluate_intervals, add_interval_report
with patch('socket.socket', side_effect=AssertionError('No network')):
    plan,capture,protocol=interval_fixture()
    positive=evaluate(plan,capture,interval_protocol=protocol)
    missing=evaluate(plan,capture,interval_protocol=None)
    unsupported=evaluate(plan,capture,interval_protocol={**protocol,'method':'unimplemented-method'})
    unknown=evaluate(plan,capture,interval_protocol={'schema_version':'future-protocol','raw_decision':[1,2]})
    unknown_scalar=evaluate(plan,capture,interval_protocol='unrecognized original JSON')
    wrong_environment=evaluate(plan,capture,interval_protocol={**protocol,'numpy_version':'0.0'})
    resource=evaluate(plan,capture,interval_protocol={**protocol,'n_resamples':100000000})
    pending=add_interval_report(evaluate(plan,capture),evaluate_intervals(plan,capture,protocol,compute=False))
    partial_protocol=deepcopy(protocol)
    partial_protocol['metric_specs'].append(metric_spec('sensitivity'))
    partial=evaluate(plan,capture,interval_protocol=partial_protocol)
    optional_protocol=deepcopy(partial_protocol)
    optional_protocol['metric_specs'][1]['required']=False
    optional=evaluate(plan,capture,interval_protocol=optional_protocol)
    zero_protocol=deepcopy(protocol)
    zero_protocol['metric_specs']=[metric_spec('alerts_per_observation_person_hour',partition='validation')]
    all_null=evaluate(plan,capture,interval_protocol=zero_protocol)
    degenerate_capture=deepcopy(capture)
    for period in degenerate_capture['periods']:
        period['dynamic']['prompts']=[]
    degenerate=evaluate(plan,degenerate_capture,interval_protocol=protocol)
    plan2,capture2=synthetic_capture_v2()
    plan2['confidence_interval_protocol_ref']='synthetic:intervals'
    capture2['patients'][1]['partition']='development'
    positive2=evaluate(plan2,capture2,interval_protocol=protocol_payload(plan2['plan_ref']))
    legacy1=evaluate(plan,capture)
    legacy2=evaluate(plan2,capture2)
    print(json.dumps(dict(positive=positive,missing=missing,unsupported=unsupported,unknown=unknown,
        unknown_scalar=unknown_scalar,wrong_environment=wrong_environment,resource=resource,pending=pending,
        partial=partial,optional=optional,all_null=all_null,degenerate=degenerate,positive2=positive2,
        legacy1=legacy1,legacy2=legacy2),allow_nan=False))
`], { cwd: backend, encoding: 'utf8' }));
const reportRef = 'v2-protocol.schema.json#/$defs/ClinicalMetricsReport';
const clone = value => structuredClone(value);
// These qualification substitutions are a schema fixture only. No runtime
// evidence is independently checked by this test or by the presentation layer.
function runtimeFixture(report) {
  const value = clone(report);
  value.binding.dataset_version_id = '11111111-1111-4111-8111-111111111111';
  value.execution_steps?.forEach((step, index) => { step.task_id = `22222222-2222-4222-8222-${String(index).padStart(12, '0')}`; });
  value.status = 'CALCULATED_FROM_VERIFIED_REPLAYS';
  value.evidence_qualification = 'QUALIFIED_SAVED_REPLAY_BINDINGS';
  return value;
}
function render(report) {
  const document = new JSDOM(renderToStaticMarkup(React.createElement(ClinicalMetricsPanel, { report }))).window.document;
  assert.equal(document.querySelectorAll('button,input,form,a').length, 0);
  return { document, text: document.body.textContent };
}
const runtime = Object.fromEntries(Object.entries(fixtures).filter(([key]) => key !== 'legacy1').map(([key, report]) => [key, runtimeFixture(report)]));
for (const [key, report] of Object.entries(runtime)) {
  validateApiValue(reportRef, report);
  const { text } = render(report);
  assert.match(text, /不表示临床有效性已获批准/);
  assert.match(text, /合成软件测试资料/);
  if (key !== 'legacy2') assert.doesNotMatch(text, /置信区间未计算。需要独立批准/);
}
const positive = render(runtime.positive);
assert.match(positive.text, /全部区间已计算（COMPLETE）/);
assert.match(positive.text, /必需项全部完成/);
assert.match(positive.text, /患者整簇百分位重采样/);
assert.match(positive.text, /50% 双侧逐项区间/);
assert.doesNotMatch(positive.text, /95%/);
assert.match(positive.text, /开发集合；整个分区（不合并分区）/);
assert.match(positive.text, /实际分子 \/ 分母2 \/ 6/);
assert.match(positive.text, /患者簇 \/ 有贡献患者簇2 \/ 2/);
assert.match(positive.text, /重采样请求 \/ 有效 \/ 无效次数200 \/ 200 \/ 0/);
assert.match(positive.text, /\[0, 0.5\] 提示数 \/ 人时/);
assert.match(positive.text, /不是就医提前量/);
assert.match(positive.text, /未请求（NOT_REQUESTED）/);
assert.ok(positive.text.includes(runtime.positive.confidence_intervals.protocol_sha256));
assert.ok(positive.text.includes(runtime.positive.confidence_intervals.input_sha256));
assert.ok(positive.text.includes(runtime.positive.confidence_intervals.results[0].draw_sequence_sha256));
assert.ok(positive.text.includes('minimum_contributing_clusters'));
assert.equal(positive.document.querySelectorAll('[data-clinical-interval="calculated"]').length, 1);
assert.equal(positive.document.querySelectorAll('[role="status"]').length, 0);
assert.equal(runtime.positive.point_report_schema_version, 'clinical-metrics-report-1');
assert.equal(runtime.positive2.point_report_schema_version, 'clinical-metrics-report-2');
assert.match(render(runtime.positive2).text, /每个独立预定时点/);
assert.doesNotMatch(positive.text, /每个独立预定时点/);

for (const [key, status] of Object.entries({ missing:'NOT_CONFIGURED', unsupported:'UNSUPPORTED_METHOD',
  unknown:'UNSUPPORTED_PROTOCOL', unknown_scalar:'UNSUPPORTED_PROTOCOL', wrong_environment:'UNSUPPORTED_PROTOCOL',
  pending:'PENDING', resource:'RESOURCE_LIMIT', degenerate:'INCOMPLETE', all_null:'INCOMPLETE' })) {
  const { text, document } = render(runtime[key]);
  assert.ok(text.includes(`（${status}）`), key);
  assert.equal(document.querySelectorAll('[data-clinical-interval="calculated"]').length, 0, key);
  assert.match(text, /区间结果未完整/);
  assert.match(text, /必需项尚未全部完成/);
  assert.ok(document.querySelector('[role="status"]'), key);
}
assert.match(render(runtime.degenerate).text, /重采样分布退化（DEGENERATE_DISTRIBUTION）/);
assert.match(render(runtime.all_null).text, /实际分母为零（ZERO_DENOMINATOR）/);
assert.equal(runtime.all_null.confidence_intervals.results[0].estimate, null);
assert.equal(runtime.all_null.confidence_intervals.results[0].lower, null);
assert.equal(runtime.all_null.confidence_intervals.results[0].upper, null);
assert.match(render(runtime.unknown).text, /raw_decision/);
assert.match(render(runtime.unknown_scalar).text, /unrecognized original JSON/);
const partial = render(runtime.partial);
assert.match(partial.text, /区间计算未完整（INCOMPLETE）/);
assert.match(partial.text, /有贡献患者簇不足（INSUFFICIENT_CLUSTERS）/);
assert.equal(partial.document.querySelectorAll('[data-clinical-interval="calculated"]').length, 1);
assert.equal(partial.document.querySelectorAll('[data-clinical-interval="unavailable"]').length, 1);
assert.match(render(runtime.optional).text, /必需项全部完成/);
assert.match(render(runtime.optional).text, /区间结果未完整/);

// Pure offline report-1/2 and report-3 remain inspectable, never promoted to verified.
for (const report of Object.values(fixtures)) {
  const { text } = render(report);
  assert.match(text, /声明资料离线计算指标/);
  assert.match(text, /未独立核验来源证据、批准或保存的回放任务/);
  assert.doesNotMatch(text, /已核验历史回放指标|已核对保存的回放、来源和固定方案/);
  assert.throws(() => validateApiValue(reportRef, report));
}
assert.match(render(runtime.legacy2).text, /置信区间未计算/);
assert.match(render(runtime.legacy2).text, /0.5（1\/2 个已知结局提示）/);
assert.match(render(fixtures.legacy1).text, /置信区间未计算/);
assert.equal(render(runtime.legacy2).document.querySelector('[aria-label="显式统计方案置信区间"]'), null);
assert.throws(() => validateApiValue('v2-protocol.schema.json#/$defs/ClinicalMetricsReportV2', runtime.positive2));

// Invalid numerical intervals and invented approval are rejected, including in
// otherwise real synthetic-calculated results. UI also hides invalid CI bodies.
function rejects(report, mutate) { const value = clone(report); mutate(value); assert.throws(() => validateApiValue(reportRef, value)); return value; }
for (const key of ['all_null','degenerate','partial','pending','resource','wrong_environment']) {
  const value = rejects(runtime[key], report => {
    const result = report.confidence_intervals.results.find(item => item.status !== 'CALCULATED');
    result.lower = 0; result.upper = 1;
  });
  assert.equal(render(value).document.querySelectorAll('[data-clinical-interval="calculated"]').length,
    key === 'partial' ? 1 : 0);
}
for (const field of ['lower','upper','estimate']) {
  const invalid = rejects(runtime.positive, report => { report.confidence_intervals.results[0][field] = null; });
  assert.equal(render(invalid).document.querySelectorAll('[data-clinical-interval="calculated"]').length, 0);
}
for (const mutate of [
  report => { report.clinical_validation = true; },
  report => { report.evidence_qualification = 'NOT_VERIFIED_BY_PURE_EVALUATOR'; },
  report => { report.status = 'APPROVED_FOR_CLINICAL_USE'; },
  report => { report.confidence_intervals.status = 'COMPLETE'; report.confidence_intervals.results[0].status = 'ZERO_DENOMINATOR'; },
  report => { report.confidence_intervals.required_complete = false; },
  report => { report.confidence_intervals.results[0].draws_invalid = 1; },
  report => { report.confidence_intervals.results[0].denominator = 0; },
  report => { report.confidence_intervals.unlisted_metrics_status = 'CALCULATED'; },
  report => { report.confidence_intervals.protocol.metric_specs[0].denominator_policy = 'unknown-policy'; },
  report => { delete report.confidence_intervals.protocol.seed; },
  report => { report.dynamic.confidence_interval = [0, 1]; },
  report => { report.sequence_policy = clone(runtime.positive2.sequence_policy); },
]) rejects(runtime.positive, mutate);
for (const invalid of [
  (() => { const value = clone(runtime.positive); value.confidence_intervals.results[0].lower = 2; return value; })(),
  (() => { const value = clone(runtime.positive); value.confidence_intervals.results[0].confidence_level = NaN; return value; })(),
  (() => { const value = clone(runtime.positive); value.confidence_intervals.results[0].denominator = Infinity; return value; })(),
  (() => { const value = clone(runtime.positive); value.confidence_intervals.status = 'UNSUPPORTED_PROTOCOL'; return value; })(),
]) assert.equal(render(invalid).document.querySelectorAll('[data-clinical-interval="calculated"]').length, 0);
for (const field of Object.keys(runtime.positive.confidence_intervals.protocol)) {
  rejects(runtime.positive, report => { delete report.confidence_intervals.protocol[field]; });
}
for (const field of Object.keys(runtime.positive.confidence_intervals.protocol.metric_specs[0])) {
  rejects(runtime.positive, report => { delete report.confidence_intervals.protocol.metric_specs[0][field]; });
}
rejects(runtime.unknown, report => { report.confidence_intervals.protocol_sha256 = null; });
for (const field of ['seed','n_resamples','minimum_clusters','minimum_contributing_clusters']) {
  rejects(runtime.positive, report => { report.confidence_intervals.protocol[field] = Number.MAX_SAFE_INTEGER + 1; });
}
rejects(runtime.positive2, report => { delete report.execution_steps; });
rejects(runtime.partial, report => { report.confidence_intervals.required_complete = true; });
rejects(runtime.pending, report => { report.confidence_intervals.required_complete = true; });
rejects(runtime.missing, report => { report.confidence_intervals.results = clone(runtime.positive.confidence_intervals.results); });
console.log('PASS: clinical interval DOM/API contract: calculated, missing, unsupported, pending, resource, degenerate, partial/optional, all-null, report-1/2 compatibility, explicit protocol and no invented CI/approval');
