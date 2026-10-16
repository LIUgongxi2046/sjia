// Synthetic nonvisual regression of the production read-only coverage panel.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Event']) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { DispositionCoveragePanel } = await import('../src/features/support/DispositionCoveragePanel.tsx');
const { validateDispositionCoverage } = await import('../src/api/dispositionCoverage.ts');
const id = '00000000-0000-4000-8000-000000000001';
const other = '00000000-0000-4000-8000-000000000002';
const value = { schema_version: 'disposition-coverage-1', patient_id: id, purpose: 'synthetic_review', observed_at: '2026-10-11T00:00:00Z', mode: 'read_only_inventory', whole_data_disposal_supported: false, execution_authorized: false, scope_expansion_authorized: false, snapshot_consistency: 'bounded_live_metadata_observation', inventory_truncated: false,
  catalog: { registered_target_types: ['file_object', 'purpose_copy', 'provider_copy'], executors: [{ target_type: 'file_object', availability: 'local_bytes_executor', coverage: 'exact_file_bytes_only' }], unsupported_categories: [{ category: 'record_versions_fields', executor_registered: false, required_contract: 'TYPED_POLICY_REQUIRED' }], execution_prerequisites: ['CURRENT_SOURCE_AUTHORITY'] },
  inventory: [], objects: [], policies: [], dependencies: { coverage: 'registered_edges_only', total_patient_nodes: 0, truncated: false, cross_patient_edge_rejected: false, nodes: [], edges: [] }, unresolved_coverage: ['BACKUP_CATALOG_UNAVAILABLE'], unresolved_policy_ids: [], manifest_sha256: 'a'.repeat(64) };
validateDispositionCoverage(value);
assert.throws(() => validateDispositionCoverage({ ...value, execution_authorized: true }));
assert.throws(() => validateDispositionCoverage({ ...value, clinical_payload: 'SYNTHETIC PRIVATE' }));
let requests = [], mode = 'ok', permission = 0, delayed;
globalThis.fetch = async (path, options = {}) => {
  requests.push({ path, options }); assert.equal(options.method ?? 'GET', 'GET');
  if (mode === 'delay') return new Promise(resolve => { delayed = resolve; });
  if (mode === 'denied') return Response.json({ code: 'CASE_ACTION_DENIED', message: 'Synthetic denied' }, { status: 403 });
  return Response.json(mode === 'wrong' ? { ...value, patient_id: other } : value);
};
const root = createRoot(document.getElementById('root'));
const refresh = () => { permission++; };
const flush = () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
const render = active => act(async () => root.render(h(DispositionCoveragePanel, { active, onPermission: refresh })));
const change = (index, text) => act(async () => Simulate.change(document.querySelectorAll('input')[index], { target: { value: text } }));
const fill = async () => { await change(0, id); await change(1, 'synthetic_review'); };
const click = async () => { await act(async () => document.querySelector('button').click()); await flush(); };
await render(true); assert.equal(requests.length, 0); await fill(); assert.equal(requests.length, 0);
await click(); assert.equal(requests.length, 1); assert.ok(document.body.textContent.includes(value.manifest_sha256));
await change(0, other); assert.ok(!document.body.textContent.includes(value.manifest_sha256));
await fill(); mode = 'wrong'; await click(); assert.ok(document.querySelector('[role=alert]').textContent.includes('不一致')); assert.ok(!document.body.textContent.includes(value.manifest_sha256));
mode = 'delay'; await click(); const pending = requests.at(-1); await change(1, 'another_purpose'); assert.equal(pending.options.signal.aborted, true);
await act(async () => delayed(Response.json(value))); await flush(); assert.ok(!document.body.textContent.includes(value.manifest_sha256));
mode = 'denied'; await fill(); await click(); assert.equal(permission, 1); assert.ok([...document.querySelectorAll('input')].every(node => node.value === ''));
mode = 'ok'; await fill(); await click(); assert.ok(document.body.textContent.includes(value.manifest_sha256));
await act(async () => window.dispatchEvent(new Event('pagehide'))); assert.ok(!document.body.textContent.includes(value.manifest_sha256)); assert.ok([...document.querySelectorAll('input')].every(node => node.value === ''));
await fill(); await click(); await render(false); assert.equal(document.getElementById('root').textContent, ''); await render(true); assert.ok([...document.querySelectorAll('input')].every(node => node.value === ''));
await act(async () => root.unmount());
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks: ['strict read-only schema', 'explicit query only', 'GET only', 'scope change clears results', 'wrong patient rejected', 'stale response aborted and ignored', 'permission failure clears private state', 'pagehide and inactive clear inputs/results'] }, null, 2));
