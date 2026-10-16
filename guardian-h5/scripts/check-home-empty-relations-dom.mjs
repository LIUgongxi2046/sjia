// Actual AccessProvider, home/read-state components, API adapters and validators.
// Synthetic fetch only: no backend/browser, account creation or authorization changes.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, fixtureDigest } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { registerHooks } from 'node:module';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, next) {
  if (/\.(png|jpg|webp|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return next(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/home' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'HTMLSelectElement', 'ShadowRoot', 'Event']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { UI2HomePage } = await import('../src/features/home/UI2HomePage.tsx');
const actor = randomUUID(), patient = randomUUID(), patient2 = randomUUID();
const relation = (id, extra = {}) => ({ relation_id: randomUUID(), patient_id: id, display_id: `SYNTHETIC ${id}`, status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, authorization_scope_fingerprint: fixtureDigest, authorization_scope_valid_until: null, actions: ['read_raw', 'write_record'], ...extra });
const home = id => ({ patient_id: id, display_id: `SYNTHETIC ${id}`, service_state: 'disabled', data_state: 'incomplete', processing_state: 'idle', alert_state: 'none', read_state: 'not_applicable', capabilities: [], assessment_state: 'configuration_pending', assessed_at: null, current_alert_id: null, recent_records: [] });
const failure = status => Response.json({ code: 'SYNTHETIC_DENIED', message: `SYNTHETIC DENIED ${status}`, retryable: false, recovery: 'reauthenticate' }, { status });
function server(options = {}) {
  const state = { relations: [], relationsStatus: 200, homeStatus: 200, malformed: false, calls: [], gate: null, ...options };
  globalThis.fetch = async (path, request = {}) => {
    path = new URL(path, 'http://localhost').pathname;
    state.calls.push({ path, method: request.method ?? 'GET' });
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (path === '/api/session') return Response.json(syntheticSession(actor, null));
    if (path === '/api/relations') {
      if (state.gate) await state.gate;
      return state.relationsStatus === 200 ? Response.json(state.malformed ? { relations: 'invalid' } : { relations: state.relations }) : failure(state.relationsStatus);
    }
    if (/\/api\/patients\/[^/]+\/home$/.test(path)) return state.homeStatus === 200 ? Response.json(home(path.split('/')[3])) : failure(state.homeStatus);
    if (path.endsWith('/records/search')) return Response.json({ records: [], next_cursor: null });
    throw new Error(`Unexpected request ${path}`);
  };
  return state;
}
let root, router, access;
function Capture() { access = useAccess(); return null; }
const screen = () => document.querySelector('[data-screen-id]')?.shadowRoot;
const text = () => (screen()?.textContent ?? '') + document.body.textContent;
const button = label => [...screen().querySelectorAll('button')].find(node => node.textContent === label);
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 15)); });
async function click(node) { assert.ok(node); await act(async () => Simulate.click(node)); await flush(); await flush(); }
async function mount() {
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Capture), h(Outlet)), children: [{ path: '/home', element: h(UI2HomePage) }, { path: '/associations/new', element: h('p', null, 'SYNTHETIC existing application route') }, { path: '/login', element: h('p', null, 'SYNTHETIC existing login route') }] }], { initialEntries: ['/home'] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
}
async function unmount() { await act(async () => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
function noWrites(state) { assert.ok(state.calls.every(call => call.method === 'GET' || call.path.endsWith('/records/search')), JSON.stringify(state.calls)); }
function emptyHome(state) {
  assert.equal(access.principalId, actor); assert.equal(access.patientId, null); assert.equal(access.error, null);
  assert.ok(text().includes('还没有关联的孩子')); assert.ok(text().includes('关联后查看'));
  assert.ok(!text().includes('当前访问关系需要核验')); assert.ok(!text().includes('需要核验权限'));
  assert.ok(!text().includes('核验患儿关联')); assert.equal(access.hasCurrentAccess(), false);
  assert.ok(!state.calls.some(call => /\/patients\//.test(call.path))); noWrites(state);
}
{
  const state = server(); await mount(); emptyHome(state);
  await click(button('添加患儿关联'));
  assert.equal(router.state.location.pathname, '/associations/new'); noWrites(state); await unmount();
}
{
  const state = server(); await mount(); emptyHome(state);
  const count = state.calls.filter(call => call.path === '/api/relations').length;
  await click(button('刷新关联')); emptyHome(state);
  assert.equal(state.calls.filter(call => call.path === '/api/relations').length, count + 1);
  // Same signed-in session gains two grants on the next real adapter read.
  state.relations = [relation(patient), relation(patient2)];
  let release; state.gate = new Promise(resolve => { release = resolve; });
  const refresh = button('刷新关联');
  await act(async () => { Simulate.click(refresh); Simulate.click(refresh); }); await flush();
  assert.equal(state.calls.filter(call => call.path === '/api/relations').length, count + 2, 'duplicate refresh shares one access refresh');
  assert.equal(access.patientId, null, 'no patient is adopted before the relation response');
  await act(async () => release()); await flush(); await flush(); state.gate = null;
  assert.equal(access.patientId, patient); assert.equal(access.error, null); assert.equal(access.hasCurrentAccess(), true);
  assert.ok(!text().includes('还没有关联的孩子')); assert.ok(!text().includes('当前访问关系需要核验'));
  assert.ok(screen().querySelector('select')?.options.length === 2);
  assert.ok(state.calls.some(call => call.path === `/api/patients/${patient}/home`));
  assert.ok(state.calls.filter(call => call.path === '/api/session').length >= 3);
  noWrites(state); await unmount();
}
for (const status of [401, 403]) {
  const state = server({ relations: [relation(patient)], homeStatus: status }); await mount();
  assert.ok(text().includes('当前访问关系需要核验')); assert.ok(text().includes(`HTTP ${status}`));
  assert.ok(!text().includes('还没有关联的孩子')); noWrites(state); await unmount();
}
for (const options of [{ relationsStatus: 403 }, { malformed: true }]) {
  const state = server(options); await mount();
  assert.equal(access.patientId, null); assert.equal(access.errorSource, 'relations'); assert.ok(access.error);
  assert.ok(text().includes('患儿关联暂未读取')); assert.ok(!text().includes('还没有关联的孩子'));
  noWrites(state); await unmount();
}
{
  const state = server(); await mount(); emptyHome(state);
  state.relationsStatus = 403; await click(button('刷新关联'));
  assert.equal(access.patientId, null); assert.equal(access.hasCurrentAccess(), false);
  assert.ok(text().includes('患儿关联暂未读取')); assert.ok(text().includes('SYNTHETIC DENIED 403'));
  assert.ok(!text().includes('还没有关联的孩子')); noWrites(state); await unmount();
}
{
  const state = server({ relations: [relation(patient, { status: 'revoked' })] }); await mount();
  assert.equal(access.patientId, null); assert.equal(access.error, null);
  assert.ok(text().includes('当前访问关系需要核验')); assert.ok(!text().includes('还没有关联的孩子'));
  noWrites(state); await unmount();
}
dom.window.close();
console.log('PASS home empty relations: actual AccessProvider/home/API/schema; 200 empty onboarding; existing apply route; true relations/session refresh and two-grant adoption; duplicate/pending refresh; 401/403/malformed/revoked remain distinct; no account/patient/grant writes. Synthetic HTTP only; no browser/backend.');
