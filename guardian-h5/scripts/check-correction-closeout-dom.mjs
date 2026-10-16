// Synthetic nonvisual DOM tests of production controlled-correction recovery.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLButtonElement', 'Event']) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { useCorrectionSubmission } = await import('../src/state/useCorrectionSubmission.ts');
const { CorrectionRecovery } = await import('../src/components/CorrectionRecovery.tsx');
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const identity = { operation_id: 'correction_request_create', target_key: `/api/records/${uuid(1)}/correction-requests`, request_id: uuid(2), schema_version: '1.0.0',
  payload: { record_version_id: uuid(3), field_ids: [uuid(4)], reason: 'synthetic request', proposed_text: null } };
const intent = { intent_id: uuid(5), request_id: identity.request_id, generation: '1', status: 'prepared', expires_at: '2099-01-01T00:00:00Z', payload_sha256: 'a'.repeat(64) };
const receipt = { resource_type: 'correction_request', resource_id: uuid(6), version: '1', saved_at: '2026-10-11T00:00:00Z', request_id: identity.request_id };
let posted = 0, prepared = 0, mode = 'lost-commit', committed = false, saved = 0, available = true, hook, root, router;
globalThis.fetch = async (path, options = {}) => {
  if (path === '/api/write-intents') { prepared++; if (mode === 'lost-prepare') throw new TypeError('synthetic connection lost'); return Response.json(intent); }
  if (path === identity.target_key) { posted++; committed = true; if (mode === 'lost-commit') throw new TypeError('synthetic lost reply'); return Response.json(receipt); }
  if (path.startsWith('/api/requests/')) {
    if (mode === 'lost-prepare') return Response.json({ code: 'INTENT_NOT_FOUND', message: 'synthetic missing' }, { status: 404 });
    return Response.json({ intent_id: intent.intent_id, request_id: identity.request_id, operation_id: identity.operation_id, target_key: identity.target_key,
      prepared_at: '2026-10-11T00:00:00Z', state: committed ? 'committed' : 'pending', may_retry: false, receipt: committed ? receipt : null, error_code: null,
      generation: '1', expires_at: intent.expires_at, payload_sha256: intent.payload_sha256, correction_input: identity.payload });
  }
  throw new Error(`Unexpected request ${path}`);
};
function Harness() { hook = useCorrectionSubmission(available, identity.target_key, async () => { saved++; });
  return h('main', null, h('button', { disabled: hook.locked || !available, onClick: () => hook.start(identity) }, 'create'), h(CorrectionRecovery, { submission: hook })); }
async function flush() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); }); }
async function click(label) { const button = [...document.querySelectorAll('button')].find(node => node.textContent === label); assert.ok(button, label); await act(async () => button.click()); await flush(); }
async function mount(entry = '/form') { root = createRoot(document.getElementById('root')); router = createMemoryRouter([{ path: '/form', element: h(Harness) }], { initialEntries: [entry] }); await act(async () => root.render(h(RouterProvider, { router }))); await flush(); }
async function unmount() { await act(async () => root.unmount()); document.getElementById('root').replaceChildren(); }
await mount(); await click('create'); assert.equal(hook.phase, 'unknown'); assert.equal(document.querySelector('button').disabled, true); assert.equal(posted, 1);
await click('create'); assert.equal(posted, 1); await click('查询原请求'); assert.equal(hook.phase, 'saved'); assert.equal(saved, 1); assert.equal(posted, 1);
const stored = router.state.location.pathname + router.state.location.search; await unmount(); await mount(stored); assert.equal(hook.phase, 'unknown'); await click('查询原请求'); assert.equal(hook.phase, 'saved'); assert.equal(posted, 1); await unmount();
mode = 'lost-prepare'; committed = false; await mount(); await click('create'); assert.equal(hook.phase, 'unknown'); await click('查询原请求'); assert.equal(hook.notFound, true);
await click('原请求未登记，返回编辑'); assert.equal(hook.phase, 'idle'); assert.equal(router.state.location.search, ''); assert.equal(posted, 1); await unmount();
available = false; await mount(); await click('create'); assert.equal(posted, 1); await unmount();
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks: ['unknown commit locks duplicate submission', 'original receipt recovers without repost', 'reload retains request identity and recovers', 'unregistered prepare returns to editing only after lookup', 'unavailable scope cannot submit'], requests: { prepared, posted } }, null, 2));

// The publication editor binds every group explicitly and deduplicates shared references.
for (const name of ['HTMLInputElement', 'HTMLSelectElement', 'HTMLFormElement', 'KeyboardEvent', 'FormData']) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
const { Simulate } = await import('react-dom/test-utils');
const { PublicationEditor } = await import('../src/features/support/PublicationEditor.tsx');
const groupKinds = ['rule', 'window', 'verification', 'mapping', 'content', 'validation'];
const configurations = groupKinds.flatMap((kind, index) => [0, 1].map(group => ({ id: uuid(100 + index * 2 + group), kind, title: `synthetic ${kind} ${group}`, version: '1', status: 'enabled', schema_version: 'synthetic', checksum: 'a'.repeat(64) })))
  .concat(['visibility', 'notification'].map((kind, index) => ({ id: uuid(130 + index), kind, title: `synthetic ${kind}`, version: '1', status: 'enabled', schema_version: 'synthetic', checksum: 'a'.repeat(64) })));
let groupedPayload;
root = createRoot(document.getElementById('root'));
await act(async () => root.render(h(PublicationEditor, { operation: 'release_create', directories: { config: { environment: 'test', items: configurations, schemas: [] }, contact: { items: [{ id: uuid(140), title: 'synthetic contact', version: '1' }] } },
  config: null, contact: null, release: null, gateVersion: '1', text: '', busy: false, onSubmit: (_, target, payload) => { assert.equal(target, '/api/support/releases'); groupedPayload = payload; } })));
const toggle = document.querySelector('input[type=checkbox]');
await act(async () => Simulate.change(toggle, { target: { checked: true } }));
for (const select of document.querySelectorAll('select')) {
  const name = select.name;
  if (name.startsWith('group_')) { const [, group, kind] = name.split('_'); const rows = configurations.filter(row => row.kind === kind); select.value = rows[['rule', 'content', 'validation'].includes(kind) ? Number(group) : 0].id; }
  else if (name === 'validation_config_id') select.value = configurations.find(row => row.kind === 'validation').id;
  else if (name === 'contact_version_id') select.value = uuid(140);
  else select.value = configurations.find(row => row.kind === name).id;
}
await act(async () => Simulate.submit(document.querySelector('form')));
assert.ok(groupedPayload); assert.equal(groupedPayload.publication_groups.length, 2); assert.equal(groupedPayload.components.length, 9);
assert.equal(new Set(groupedPayload.components.map(row => `${row.kind}:${row.config_id}`)).size, 9);
assert.equal(groupedPayload.publication_groups[0].window_id, groupedPayload.publication_groups[1].window_id);
assert.notEqual(groupedPayload.publication_groups[0].rule_id, groupedPayload.publication_groups[1].rule_id);
assert.ok(document.body.textContent.includes('未配置时不能启用'));
await act(async () => root.unmount());
console.log('PASS explicit grouped publication editor binds independent rule/content/validation and exact shared union');
