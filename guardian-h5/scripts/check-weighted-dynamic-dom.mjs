// Actual production schemas/transport/rendering; synthetic only, no browser.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/support/assessment' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { MemoryRouter } = await import('react-router');
const { DynamicSources, WeightedGroupDetails, dynamicSummary } = await import('../src/features/support/DynamicSources.tsx');
const { validateApiValue, checkDynamicSchemas } = await import('../src/api/schema.ts');
const { readDynamic } = await import('../src/api/dynamic.ts');
const root = createRoot(document.getElementById('root'));
const point = '2026-10-11T00:00:00Z', evaluation = randomUUID(), rule = randomUUID();
const weighted = { schema_version: 'weighted-combination-result-1', denominator: 'all_configured_weights',
  denominator_weight: '5', met_weight: '2', known_weight: '2', unknown_weight: '3', threshold_fraction: '0.6', minimum_known_fraction: '1',
  lower_fraction: { numerator: '2', denominator: '5' }, upper_fraction: { numerator: '1', denominator: '1' }, known_fraction: { numerator: '2', denominator: '5' },
  core_gate: { operator: 'all', minimum_met: null }, core_state: 'met', coverage_state: 'unknown', threshold_state: 'unknown',
  unknown_policy: 'bounded_no_imputation', reason_codes: ['DYNAMIC_WEIGHTED_KNOWN_WEIGHT_INSUFFICIENT'],
  members: [{ signal_ref: 'SYNTHETIC_CORE', classification: 'core', weight: '2', state: 'met' },
    { signal_ref: 'SYNTHETIC_GENERAL', classification: 'general', weight: '3', state: 'unknown' }] };
const logical = { state: 'not_met', groups: [{ path: [], operator: 'all', state: 'not_met', operands: ['not_met'], signal_refs: ['legacy'], minimum_met: null }] };
const group = { state: 'unknown', groups: [{ path: [], operator: 'weighted', state: 'unknown', operands: ['met', 'unknown'], signal_refs: ['SYNTHETIC_CORE', 'SYNTHETIC_GENERAL'], minimum_met: null, weighted }] };
const preset = { schema_version: 'dynamic-sensitivity-preset-1', preset_key: 'synthetic-only', display_name: 'SYNTHETIC APPROVED NAME',
  description: 'No clinical default or approval.', config_id: rule, config_checksum: 'a'.repeat(64) };
const view = { schema_version: 'dynamic-evaluation-view-2', evaluation_id: evaluation, patient_id: randomUUID(),
  task: { task_id: randomUUID(), task_type: 'alert_evaluation', request_id: null, accepted_at: point, status: 'completed', status_version: '1',
    poll_after_ms: null, allowed_actions: [], result_type: 'alert_evaluation', result_ref: evaluation, error_code: null },
  mode: 'current', status: 'insufficient', validity: 'valid', decision: 'unknown', assessment_at: point, known_at: point, computed_at: point,
  context_sequence: '1', policy_id: rule, window_policy_id: randomUUID(), input_sha256: 'a'.repeat(64), implementation_sha256: 'b'.repeat(64),
  missing_items: [], limitations: [], context_states: { population: 'met', therapy: 'met', clinical_finding: 'not_required_by_policy', late_result: 'not_required_by_policy' },
  applicability: 'applicable', signals: [], combinations: { trigger: group, reset: logical, retrigger: logical }, selected_refs: {},
  exclusions: [], clinical_selections: [], relation_refs: [], relation_proofs: [], automatic_trigger: null,
  sensitivity_preset: preset, lifecycle: { state: 'current', action: 'no_event', reason_codes: [], event_id: null, episode_id: null }, reason_code: null };
let checks = 0; const pass = name => { checks++; console.log(`PASS ${name}`); };
async function render(component) { await act(async () => root.render(h(MemoryRouter, null, component))); return document.body.textContent; }
checkDynamicSchemas();
validateApiValue('v2-protocol.schema.json#/$defs/DynamicView', view);
for (const mutate of [v => delete v.denominator_weight, v => { v.members[0].classification = 'important'; },
  v => { v.members[0].weight = 2; }, v => { v.lower_fraction.denominator = '0'; }, v => { v.unknown_policy = 'zero'; }]) {
  const bad = structuredClone(weighted); mutate(bad);
  assert.throws(() => validateApiValue('v2-protocol.schema.json#/$defs/DynamicWeightedResult', bad));
}
pass('actual schema rejects missing denominator, coercion and unknown imputation');
let text = await render(h(DynamicSources, { result: view }));
for (const phrase of ['SYNTHETIC_CORE · 核心指标', 'SYNTHETIC_GENERAL · 一般指标', '权重 3 · 未知',
  '完整权重分母5', '精确加权下界2 / 5', '未知指标保留在完整分母中', '未满足不能排除风险', 'SYNTHETIC APPROVED NAME', rule, 'a'.repeat(64)]) assert.ok(text.includes(phrase), phrase);
assert.equal(document.querySelectorAll('input, select, textarea').length, 0);
pass('support exposes preset identity, core/general weights, unknown bounds and no numerical editing');
const highPrecision = structuredClone(weighted);
highPrecision.lower_fraction = { numerator: '900719925474099312345678901', denominator: '999999999999999999999999999' };
text = await render(h(WeightedGroupDetails, { value: highPrecision }));
assert.ok(text.includes('900719925474099312345678901 / 999999999999999999999999999'));
pass('fraction rendering preserves integers beyond JavaScript numeric precision');
text = await render(h(WeightedGroupDetails, { value: { ...weighted, core_gate: { operator: 'at_least', minimum_met: 2 }, core_state: 'unknown' } }));
assert.ok(text.includes('至少 2 项核心指标满足 · 未知'));
pass('explicit at-least core gate and unknown state remain visible');
const legacy = { ...view, sensitivity_preset: null, decision: 'not_met', combinations: { trigger: logical, reset: logical, retrigger: logical } };
text = await render(h(DynamicSources, { result: legacy }));
assert.ok(text.includes('整体条件 · all · 未满足')); assert.equal(document.querySelector('[aria-label="批准加权规则解释"]'), null);
assert.equal(dynamicSummary(legacy)[2][0], '三值组合结果'); assert.equal(dynamicSummary(view)[2][0], '加权组合结果');
pass('legacy logical output and weighted summary coexist');
text = await render(h(DynamicSources, { result: { ...view, validity: 'invalid' } })); assert.equal(text, '');
assert.ok(dynamicSummary({ ...view, validity: 'invalid' }).every(([, value]) => value === '尚无有效评估'));
pass('invalid results display no stale weighted explanation or preset identity');
globalThis.fetch = async () => Response.json(view); assert.equal((await readDynamic(evaluation)).sensitivity_preset.config_id, rule);
globalThis.fetch = async () => Response.json({ ...view, sensitivity_preset: { ...preset, config_id: randomUUID() } });
await assert.rejects(readDynamic(evaluation), /方案名称与准确规则版本不一致/);
const invalid = { ...view, validity: 'invalid', signals: [], combinations: null, context_states: null, lifecycle: null,
  selected_refs: {}, exclusions: [], clinical_selections: [], relation_refs: [], relation_proofs: [], automatic_trigger: null,
  decision: 'unknown', reason_code: 'DYNAMIC_CONTEXT_INVALIDATED' };
globalThis.fetch = async () => Response.json(invalid); await assert.rejects(readDynamic(evaluation), /失效动态评估含有不能采用的内容/);
globalThis.fetch = async () => Response.json({ ...invalid, sensitivity_preset: null }); assert.equal((await readDynamic(evaluation)).validity, 'invalid');
pass('actual adapter rejects mismatched preset identity and leaked invalid metadata');
await act(async () => root.unmount());
console.log(`PASS ${checks} weighted schema, adapter and component checks; visual/device acceptance NOT_RUN`);
