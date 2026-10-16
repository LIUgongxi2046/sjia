// Pure response bindings; no browser, network, live patient or provider.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
const { verifySummaryTrendBinding } = await import('../src/api/summaries.ts');
const { validateSummary } = await import('../src/api/summarySchema.ts');
const first = '11111111-1111-4111-8111-111111111111';
const second = '22222222-2222-4222-8222-222222222222';
const source = '33333333-3333-4333-8333-333333333333';
const field = { field_id: first, field_code: 'synthetic_temp', concept_code: 'synthetic:temp',
  raw_numeric: '36.8', raw_unit: 'Cel', value_state: 'known', qualifier: 'eq',
  medical_time: { precision: 'second', resolution_status: 'resolved', instant: '2026-10-10T01:00:00Z' } };
const projection = { schema_version: 'summary-raw-trend-1', snapshot_sha256: 'a'.repeat(64),
  clinical_interpretation: false, series: [{ field_code: 'synthetic_temp', concept_code: 'synthetic:temp',
    points: [{ field_id: first, record_version_id: second, source_definition_revision_id: source,
      raw_value: '36.8', raw_unit: 'Cel', plot_at: '2026-10-10T01:00:00+00:00', plottable: true, reason_codes: [] }], segments: [] }] };
const summary = { validity: 'valid', needs_regeneration: false, items: [{ field_id: first, record_version_id: second,
  snapshot: { source_definition_revision_id: source, field } }], trend_projection: projection };
validateSummary('SummaryTrendProjection', projection);
verifySummaryTrendBinding(summary);
verifySummaryTrendBinding({ ...summary, trend_projection: undefined });
for (const mutate of [
  value => value.trend_projection.series[0].points[0].record_version_id = first,
  value => value.trend_projection.series[0].points[0].raw_value = '99',
  value => value.trend_projection.series[0].points[0].raw_unit = 'F',
  value => value.trend_projection.series[0].points[0].source_definition_revision_id = first,
  value => value.trend_projection.series[0].points[0].plot_at = '2026-10-11T01:00:00Z',
  value => value.items[0].snapshot.field.medical_time.resolution_status = 'unknown',
  value => value.items[0].snapshot.field.value_state = 'unknown',
  value => value.needs_regeneration = true,
  value => value.trend_projection.series[0].points.push(value.trend_projection.series[0].points[0]),
  value => value.trend_projection.series[0].segments.push({ from_field_id: first, to_field_id: second, connect: true, reason_codes: [] }),
]) {
  const copy = structuredClone(summary); mutate(copy);
  assert.throws(() => verifySummaryTrendBinding(copy), /摘要趋势|失效摘要/);
}
assert.throws(() => validateSummary('SummaryTrendProjection', { ...projection, clinical_interpretation: true }), /Schema/);
console.log('PASS: 13 summary raw-trend schema and immutable source/time/value/permission binding checks');
