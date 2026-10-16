// Actual home wire validator/read hook and field renderer, synthetic data only.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>');
for (const key of ['window', 'document', 'navigator', 'HTMLElement']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { validateFamilyRead } = await import('../src/api/familyReadSchema.ts');
const { validateApiValue } = await import('../src/api/schema.ts');
const { useFamilyResource } = await import('../src/api/useFamilyResource.ts');
const { RecordFieldPrimary } = await import('../src/components/RecordFieldPrimary.tsx');
const patient = randomUUID(), record = randomUUID(), version = randomUUID(), at = '2026-10-11T00:00:00Z';
function snapshot(code, overrides = {}, state = 'not_recorded') {
  const field_id = randomUUID();
  return { field: { field_id, ordinal: 1, field_code: code, concept_code: null, dictionary_entry_id: null,
    original_name: null, value_type: 'categorical', value_state: 'unknown', raw_text: null, raw_numeric: null,
    categorical_value: null, qualifier: null, raw_unit: null, method: null, reference_interval: null,
    report_flag: null, medical_time: null, sample_time_origin: 'unknown', quality_reasons: [],
    source_reference: { source_revision_id: randomUUID(), record_version_id: version, field_id, file_id: null,
      page_id: null, anchor_id: null, verification_ids: [], mapping_id: null }, ...overrides },
    verification_status: 'unverified', verification_purpose: null, quality_reasons_snapshot_state: state };
}
const unknown = snapshot('therapy_status');
const known = snapshot('medication_name', { value_type: 'text', value_state: 'known', raw_text: 'SYNTHETIC NOT A MEDICINE' }, 'recorded');
const recordedUnknown = snapshot('rash', { quality_reasons: ['SYNTHETIC_RECORDED_UNKNOWN_REASON'] }, 'recorded');
const home = { patient_id: patient, display_id: 'SYNTHETIC ONLY', service_state: 'disabled', data_state: 'incomplete',
  processing_state: 'idle', alert_state: 'none', read_state: 'not_applicable', capabilities: [],
  assessment_state: 'configuration_pending', assessed_at: null, current_alert_id: null,
  recent_records: [{ record_id: record, record_version_id: version, version: '1', patient_id: patient,
    display_id: 'SYNTHETIC ONLY', kind: 'therapy', origin_type: 'self_report', capture_method: 'manual',
    recorded_at: at, quality_status: 'pending', fields: [unknown, known, recordedUnknown], report: null,
    editable_field_ids: [], verification_coverage: 'none', correction_reason: null,
    author: { display_id: 'SYNTHETIC', role: 'family', is_self: true }, original_files: [], original_files_complete: true }] };
const before = JSON.stringify(home);
assert.deepEqual(validateFamilyRead('HomeView', home), home);
assert.equal(JSON.stringify(home), before);
assert.equal(home.recent_records[0].fields[0].field.medical_time, null);
const legacyKnown = structuredClone(known); delete legacyKnown.quality_reasons_snapshot_state;
validateApiValue('protocol.schema.json#/$defs/FamilyField', legacyKnown);
for (const mutate of [
  value => { delete value.quality_reasons_snapshot_state; },
  value => { value.quality_reasons_snapshot_state = 'recorded'; },
  value => { value.field.value_state = 'known'; value.field.categorical_value = 'yes'; },
  value => { value.field.raw_numeric = '0'; },
  value => { value.field.quality_reasons = ['SYNTHETIC_UNDECLARED_REASON']; },
  value => { value.unregistered = true; },
]) { const value = structuredClone(unknown); mutate(value); assert.throws(() => validateApiValue('protocol.schema.json#/$defs/FamilyField', value)); }
assert.throws(() => validateApiValue('dataset.schema.json#/$defs/clinicalField', unknown.field));
validateApiValue('dataset.schema.json#/$defs/clinicalField', recordedUnknown.field);
let result, response = structuredClone(home), calls = 0;
delete response.recent_records[0].fields[0].quality_reasons_snapshot_state;
globalThis.fetch = async path => { assert.equal(path, `/api/patients/${patient}/home`); calls++; return Response.json(response); };
function Probe() {
  result = useFamilyResource(`/api/patients/${patient}/home`, 'HomeView');
  return result.error ? h('p', { role: 'alert' }, result.error.message) : result.data
    ? h('div', null, ...result.data.recent_records[0].fields.map(value => h(RecordFieldPrimary, { key: value.field.field_id, field: value.field }))) : null;
}
const root = createRoot(document.getElementById('root'));
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 20)); });
await act(async () => root.render(h(Probe))); await flush();
assert.equal(result.data, null); assert.ok(result.error); assert.equal(document.querySelectorAll('.rich-field-primary').length, 0);
response = home; await act(async () => result.reload()); await flush();
assert.equal(result.error, null); assert.equal(result.data.data_state, 'incomplete'); assert.equal(calls, 2);
assert.deepEqual([...document.querySelectorAll('.rich-field-primary strong')].map(node => node.textContent),
  ['无法确定', 'SYNTHETIC NOT A MEDICINE', '无法确定']);
assert.equal(JSON.stringify(home), before);
await act(async () => root.unmount()); dom.window.close();
console.log('PASS home snapshot wire/hook/render: explicit absent reasons, recorded unknown/known values, strict fresh fields, legacy known compatibility, error/reload, unchanged unknown time and no fabricated reason');
