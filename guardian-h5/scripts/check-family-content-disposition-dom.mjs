// Permanent synthetic-only coverage of the real family panel and API validators.
// No browser, backend, database, provider, or real clinical data is accessed.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'https://localhost/' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'HTMLButtonElement', 'Event'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { FamilyContentDispositionTools } = await import('../src/features/account/FamilyContentDispositionTools.tsx');
const api = await import('../src/api/familyContentDisposition.ts');
const id = n => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const patient = id(1), patientB = id(2), actor = id(3), actorB = id(4), policy = id(5), targetA = id(6), targetB = id(7);
const now = '2026-10-11T00:00:00Z';
const clone = value => structuredClone(value);
const choices = p => ({ schema_version: 'content-disposition-family-options-1', patient_id: p, default_policy: null,
  policies: [{ policy_id: policy, version: 3, mode: 'erase_selected_content', execution_enabled: false, retention_seconds: 0,
    clock: 'recorded_at', access_purpose: 'family_observation', recheck_at: '2099-01-01T00:00:00Z' }],
  targets: p === patient ? [{ record_version_id: targetA, version: '2', record_id: id(8), recorded_at: now },
    { record_version_id: targetB, version: '3', record_id: id(8), recorded_at: now }] : [{ record_version_id: id(9), version: '4', record_id: id(10), recorded_at: now }] });
const view = (input, p = patient) => ({ schema_version: 'content-disposition-family-view-1', request_id: randomUUID(),
  client_request_id: input.request_id, patient_id: p, policy_id: input.policy_id, created_at: now, targets: clone(input.targets),
  status: 'requested', linked_plan_id: null, logical_payload_erased: false, all_copies_erased: false });
const marker = (client, server, p = patient, a = actor) => new URLSearchParams({ content_request_client: client, content_request_actor: a,
  content_request_patient: p, ...(server ? { content_request_id: server } : {}) }).toString();
const panel = () => document.querySelector('[aria-label="临床记录内容处置申请"]');
const button = text => [...document.querySelectorAll('button')].find(node => node.textContent === text);
const select = () => panel()?.querySelector('select');
const checks = [];
const pass = name => { checks.push(name); console.log(`PASS ${name}`); };
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 0)); });
const click = async node => { assert.ok(node, 'Synthetic button exists'); assert.equal(node.disabled, false, node.textContent); await act(async () => Simulate.click(node)); await flush(); await flush(); };
const change = async (node, value) => { assert.ok(node, 'Synthetic input exists'); await act(async () => Simulate.change(node, { target: { value, checked: value === true } })); await flush(); };
let capturedAccess, activeRouter;
function Capture() { capturedAccess = useAccess(); return null; }
function server() {
  const state = { principal: actor, patient, permission: true, history: true, fingerprint: 'a'.repeat(64), posts: [], calls: [], stored: [],
    optionsTransform: value => value, listTransform: value => value, readTransform: value => value, postTransform: value => value,
    postMode: 'normal', deny: false, delayedOptions: null, delayedPost: null, releases: [] };
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(path, 'https://localhost'), body = options.body ? JSON.parse(options.body) : null;
    state.calls.push({ path: url.pathname, method: options.method ?? 'GET', signal: options.signal, body });
    if (url.pathname === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (url.pathname === '/api/session') return Response.json(syntheticSession(state.principal, state.patient));
    if (url.pathname === '/api/relations') return Response.json({ relations: [patient, patientB].map(p => ({
      relation_id: p, patient_id: p, display_id: 'SYNTHETIC FAMILY', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z',
      valid_until: null, authorization_scope_fingerprint: state.fingerprint, authorization_scope_valid_until: null,
      actions: ['read_raw', ...(state.history ? ['read_history'] : []), ...(state.permission ? ['request_disposition'] : [])] })) });
    if (url.pathname === '/api/patient-selection') { state.patient = body.patient_id; return Response.json({ selected_patient_id: state.patient }); }
    assert.ok(url.pathname.includes('content-disposition'), `Unexpected request ${path}`);
    assert.ok(!url.pathname.includes('support') && !url.pathname.match(/approve|execute/), 'Family never uses engine authority');
    if (state.deny) return Response.json({ code: 'CASE_ACTION_DENIED', message: 'SYNTHETIC DENIED' }, { status: 403 });
    const p = url.pathname.split('/')[4];
    if (url.pathname.endsWith('/options')) {
      const result = state.optionsTransform(choices(p));
      if (state.delayedOptions === p) return new Promise(resolve => state.releases.push(() => resolve(Response.json(result))));
      return Response.json(result);
    }
    if (url.pathname.match(/\/patients\/.*\/requests$/)) {
      if (options.method === 'POST') {
        assert.equal(new URLSearchParams(activeRouter.state.location.search).get('content_request_client'), body.request_id,
          'Client identity must be persisted in the route before POST');
        state.posts.push(clone(body));
        const saved = state.postTransform(view(body, p));
        if (state.postMode !== 'lost-uncommitted') state.stored.push(saved);
        if (state.postMode.startsWith('lost-')) throw new TypeError('Synthetic lost POST response');
        if (state.delayedPost) return new Promise(resolve => state.releases.push(() => resolve(Response.json(saved))));
        return Response.json(saved, { status: 201 });
      }
      return Response.json(state.listTransform({ schema_version: 'content-disposition-family-requests-1', patient_id: p,
        requests: clone(state.stored.filter(item => item.patient_id === p)) }));
    }
    const saved = state.stored.find(item => item.request_id === url.pathname.split('/').at(-1));
    return saved ? Response.json(state.readTransform(clone(saved))) : Response.json({ code: 'RESOURCE_NOT_FOUND', message: 'SYNTHETIC NOT FOUND' }, { status: 404 });
  };
  return state;
}
async function mount(state = server(), search = '') {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  const router = createMemoryRouter([{ path: '/account', element: h(AccessProvider, null, h(Capture), h(FamilyContentDispositionTools)) }],
    { initialEntries: ['/account' + (search ? '?' + search : '')] });
  activeRouter = router;
  const root = createRoot(document.getElementById('root'));
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
  return { state, router, get access() { return capturedAccess; }, unmount: async () => { await act(async () => root.unmount()); router.dispose(); } };
}
async function open() { await click(button('申请处理临床记录内容')); }
async function fill(index = 1) {
  await change(select(), policy); await change(panel().querySelectorAll('input[type=checkbox]')[index], true);
}
const sentButton = () => button('提交临床内容处置申请');
const refreshButton = () => button('刷新政策、记录版本与申请');
const recoverButton = () => button('查询原内容处置申请');
const releaseAll = async state => { await act(async () => state.releases.splice(0).forEach(release => release())); await flush(); await flush(); };

{
  const test = await mount();
  assert.equal(test.state.calls.filter(call => call.path.includes('content-disposition')).length, 0);
  await open(); assert.equal(select().value, ''); assert.equal(panel().querySelectorAll('input').length, 0); assert.equal(sentButton().disabled, true);
  await fill(); assert.equal(sentButton().disabled, false);
  await click(refreshButton()); assert.equal(select().value, ''); assert.equal(sentButton().disabled, true);
  await fill(); await act(async () => { Simulate.click(sentButton()); Simulate.click(sentButton()); }); await flush(); await flush();
  assert.equal(test.state.posts.length, 1);
  assert.deepEqual(test.state.posts[0].targets, [{ record_version_id: targetB, version: '3' }]);
  assert.deepEqual(Object.keys(test.state.posts[0]).sort(), ['policy_id', 'request_id', 'schema_version', 'targets']);
  assert.equal(test.state.posts[0].policy_id, policy);
  assert.match(panel().textContent, /已提交，等待审查/); assert.match(panel().textContent, /all_copies_erased=false/);
  assert.ok(![...panel().querySelectorAll('button')].some(node => /批准|执行|立即删除/.test(node.textContent)));
  const savedSearch = test.router.state.location.search.slice(1), saved = test.state.stored[0];
  assert.equal(new URLSearchParams(savedSearch).get('content_request_id'), saved.request_id);
  assert.equal(localStorageSafeLength(), 0); await test.unmount();
  const resumed = await mount(test.state, savedSearch);
  assert.match(panel().textContent, /已提交，等待审查/); assert.equal(test.state.posts.length, 1);
  assert.ok(test.state.calls.some(call => call.path.endsWith('/requests/' + saved.request_id)));
  await resumed.unmount(); pass('No default policy/target; refresh resets stable selection; exact one POST; IDs persist before transport and recover on reload');
}
function localStorageSafeLength() { return window.localStorage.length; }
{
  const state = server(); state.history = false;
  // The server grants only the current whole-readable version. The mounted
  // account control must not invent a read_history prerequisite of its own.
  state.optionsTransform = value => ({ ...value, targets: value.targets.slice(-1) });
  const test = await mount(state); await open(); await fill(0);
  assert.equal(sentButton().disabled, false); await click(sentButton());
  assert.equal(state.posts.length, 1);
  assert.deepEqual(state.posts[0].targets, [{ record_version_id: targetB, version: '3' }]);
  assert.match(panel().textContent, /已提交，等待审查/);
  await test.unmount(); pass('Current read_raw plus request permission needs no client-side history privilege');
}
{
  const state = server(); state.postMode = 'lost-uncommitted'; const test = await mount(state); await open(); await fill(); await click(sentButton());
  assert.equal(state.posts.length, 1); assert.match(panel().textContent, /尚未查到原请求不代表提交失败/); assert.equal(sentButton(), undefined);
  const oldClient = state.posts[0].request_id, search = test.router.state.location.search.slice(1);
  assert.equal(new URLSearchParams(search).get('content_request_client'), oldClient);
  await click(recoverButton()); assert.equal(state.posts.length, 1); assert.equal(sentButton(), undefined);
  await test.unmount(); const resumed = await mount(state, search);
  assert.equal(sentButton(), undefined); await click(recoverButton()); assert.equal(state.posts.length, 1);
  state.stored.push(view(state.posts[0])); await click(recoverButton());
  assert.match(panel().textContent, /已提交，等待审查/); assert.equal(state.posts.length, 1);
  assert.equal(new URLSearchParams(resumed.router.state.location.search).get('content_request_client'), oldClient);
  await resumed.unmount(); pass('Uncertain POST and empty GET remain pending across reload; later lookup resolves the same client identity without another POST');
}
{
  const state = server(); state.postMode = 'lost-committed'; const test = await mount(state); await open(); await fill(); await click(sentButton());
  assert.match(panel().textContent, /已提交，等待审查/); assert.equal(state.posts.length, 1);
  const postIndex = state.calls.findIndex(call => call.method === 'POST');
  assert.ok(state.calls.slice(postIndex + 1).some(call => call.method === 'GET' && call.path.endsWith('/requests')));
  await test.unmount(); pass('A committed response loss immediately queries the original client request and never repeats mutation');
}
{
  const state = server(); state.delayedPost = true; const test = await mount(state); await open(); await fill(); await click(sentButton());
  const pending = state.calls.find(call => call.method === 'POST'); assert.ok(pending);
  await act(async () => window.dispatchEvent(new Event('pagehide'))); assert.equal(pending.signal.aborted, true); assert.equal(panel(), null);
  await releaseAll(state); assert.equal(panel(), null); const search = test.router.state.location.search.slice(1); await test.unmount();
  const resumed = await mount(state, search); assert.match(panel().textContent, /已提交，等待审查/); assert.equal(state.posts.length, 1);
  await resumed.unmount(); pass('pagehide aborts and clears pending clinical state; late POST cannot resurrect it; reload queries its saved identity');
}
{
  const state = server(); state.delayedOptions = patient; const test = await mount(state); await open();
  const pending = state.calls.find(call => call.path.endsWith('/options')); assert.ok(pending);
  await act(async () => test.access.selectPatient(patientB)); await flush(); assert.equal(pending.signal.aborted, true);
  await releaseAll(state); assert.ok(!document.body.textContent.includes(targetA)); await open(); await fill(0);
  assert.ok(panel().textContent.includes(id(9))); assert.ok(!panel().textContent.includes(targetB));
  await test.unmount(); pass('Patient switch aborts an in-flight read, rejects its late body, and uses only the new patient targets');
}
{
  const state = server(); const test = await mount(state); await open(); await fill();
  state.permission = false; state.fingerprint = 'b'.repeat(64); await act(async () => test.access.refresh()); await flush();
  assert.equal(panel(), null); assert.ok(!document.body.textContent.includes(targetB));
  state.permission = true; state.fingerprint = 'c'.repeat(64); await act(async () => test.access.refresh()); await flush(); await open();
  assert.equal(select().value, ''); assert.equal(sentButton().disabled, true);
  await fill(); Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
  await act(async () => document.dispatchEvent(new Event('visibilitychange'))); assert.equal(panel(), null);
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  await act(async () => document.dispatchEvent(new Event('visibilitychange'))); await flush(); await open(); assert.equal(select().value, '');
  await test.unmount(); pass('Authorization scope changes, permission loss and hidden pages discard all selections and private data');
}
{
  const state = server(); const test = await mount(state); await open(); await fill(); state.deny = true;
  await click(refreshButton()); assert.equal(panel(), null); assert.ok(!document.body.textContent.includes(targetB));
  assert.match(document.body.textContent, /访问核验未通过/);
  const deniedReads = state.calls.filter(call => call.path.includes('content-disposition')).length; await flush(); await flush();
  assert.equal(state.calls.filter(call => call.path.includes('content-disposition')).length, deniedReads, 'Denied access cannot loop automatic reads');
  await test.unmount(); pass('403 clears all displayed content and does not enter an automatic refresh loop');
}
{
  const state = server(), saved = view({ request_id: randomUUID(), policy_id: policy, targets: [{ record_version_id: targetA, version: '2' }] }); state.stored.push(saved);
  state.principal = actorB; const test = await mount(state, marker(saved.client_request_id, saved.request_id));
  assert.equal(panel(), null); assert.equal(test.router.state.location.search, '');
  assert.ok(!state.calls.some(call => call.path.includes('content-disposition'))); await test.unmount();
  pass('Another account cannot replay route-bound patient/request IDs');
}
for (const [name, transform] of [
  ['extra clinical body', value => ({ ...value, raw_body: 'SYNTHETIC SECRET' })],
  ['foreign patient', value => ({ ...value, patient_id: patientB })],
  ['duplicate version identity', value => ({ ...value, targets: [...value.targets, value.targets[0]] })],
  ['implicit default', value => ({ ...value, default_policy: policy })],
]) {
  const state = server(); state.optionsTransform = transform; const test = await mount(state); await open();
  assert.ok(panel().querySelector('[role=alert]'), name); assert.equal(select(), null); assert.equal(sentButton(), undefined);
  assert.ok(!panel().textContent.includes('SYNTHETIC SECRET')); await test.unmount();
}
pass('Strict standalone options contract rejects unknown clinical fields, wrong scope, duplicate IDs and implicit policy defaults');
{
  const state = server(); state.postTransform = value => ({ ...value, targets: [{ record_version_id: targetA, version: '2' }] });
  const test = await mount(state); await open(); await fill(); await click(sentButton());
  assert.match(panel().textContent, /准确版本范围不一致/); assert.equal(panel().querySelector('[aria-label="已核对的内容处置申请"]'), null);
  assert.equal(sentButton(), undefined); assert.equal(state.posts.length, 1);
  await click(recoverButton()); assert.match(panel().textContent, /准确版本范围不一致/);
  assert.equal(panel().querySelector('[aria-label="已核对的内容处置申请"]'), null); assert.equal(state.posts.length, 1); await test.unmount();
  pass('POST and immediate recovery both reject a receipt that changes the selected version');
}
{
  const input = { request_id: randomUUID(), policy_id: policy, targets: [{ record_version_id: targetA, version: '2' }] }, saved = view(input);
  for (const malformed of [{ ...saved, all_copies_erased: true }, { ...saved, client_request_id: randomUUID() },
    { ...saved, patient_id: patientB }, { ...saved, logical_payload_erased: true }, { ...saved, manifest: { adapters: ['synthetic'] } },
    { ...saved, targets: [] }]) assert.throws(() => api.validateFamilyContentRequest(malformed, patient, saved.request_id, saved.client_request_id));
  assert.throws(() => api.validateFamilyContentRequest(saved, patient, randomUUID(), saved.client_request_id));
  const state = server(); state.stored.push(saved); state.listTransform = value => ({ ...value, requests: [...value.requests, value.requests[0]] });
  await assert.rejects(api.listFamilyContentRequests(patient));
  state.listTransform = value => ({ ...value, requests: value.requests.map(request => ({ ...request, patient_id: patientB })) });
  await assert.rejects(api.listFamilyContentRequests(patient));
  pass('Read/list validators reject malformed receipts, wrong patient/client/server identities, invalid status claims and internal manifests');
}
{
  const file = readFileSync(new URL('../src/features/account/DispositionTools.tsx', import.meta.url), 'utf8');
  assert.match(file, /<FamilyContentDispositionTools \/>/); assert.match(file, /prepareDisposition\(identity/); assert.match(file, /submitDisposition\(identity/);
  pass('Existing file-disposition panel retains its original preparation/submission workflow and mounts the independent family panel');
}
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_ACTUAL_REACT_DOM_AND_STRICT_AJV', checks }, null, 2));
window.close();
