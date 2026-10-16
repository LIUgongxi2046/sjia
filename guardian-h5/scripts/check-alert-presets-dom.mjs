// Production adapter + page, direct DOM, synthetic APIs only. No browser or model calls.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticIntent } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID, webcrypto } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/alert-presets' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Event']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act, StrictMode } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { AlertPresetsPage } = await import('../src/features/account/AlertPresetsPage.tsx');
const { createAlertPresetSubmission, validateAlertPresets, readAlertPresetRequest } = await import('../src/api/alertPresets.ts');
const actor = randomUUID(), patient = randomUUID(), patient2 = randomUUID(), assignment = randomUUID(), optionA = randomUUID(), optionB = randomUUID();
const target = `/api/patients/${patient}/alert-preset-selection`, directory = `/api/patients/${patient}/alert-presets`;
const relation = id => ({ authorization_scope_fingerprint: 'a'.repeat(64), authorization_scope_valid_until: null, relation_id: randomUUID(), patient_id: id,
  display_id: 'SYNTHETIC', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'select_alert_preset'] });
const view = (id = patient) => ({ schema_version: 'patient-alert-presets-view-1', patient_id: id, assignment_id: assignment,
  assignment_checksum: 'b'.repeat(64), assignment_version: '1', display_name: 'SYNTHETIC 已批准方案', purpose: 'family_observation', selection_version: '0',
  selected_schedule_config_id: optionA, selection_state: 'professional_default', options: [
    { schedule_config_id: optionA, label: 'SYNTHETIC 方案甲', description: '专业人员批准的观察方案甲。', preset_key: 'preset-a', config_version: '1' },
    { schedule_config_id: optionB, label: 'SYNTHETIC 方案乙', description: '专业人员批准的观察方案乙。', preset_key: 'preset-b', config_version: '2' },
  ] });
const body = () => ({ assignment_id: assignment, assignment_checksum: 'b'.repeat(64), expected_version: '0', schedule_config_id: optionB });
const error = (code, status = 409) => Response.json({ code, message: code, retryable: false, recovery: 'none', field_errors: [] }, { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
let root, router, access;
function Capture() { access = useAccess(); return null; }
async function run(fn) { let value; await act(async () => { value = await fn(); }); await flush(); return value; }
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
const screen = () => document.querySelector('[data-screen-id]')?.shadowRoot ?? document;
const text = () => screen().textContent;
const button = label => [...screen().querySelectorAll('button')].find(node => node.textContent === label);
const click = async node => { assert.ok(node); await run(() => Simulate.click(node)); };
const chooseB = async () => run(() => Simulate.change(screen().querySelector(`input[value="${optionB}"]`), { target: { checked: true } }));
async function mount(path = '/alert-presets', strict = false) {
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Capture), h(Outlet)), children: [ { path: '*', element: h(AlertPresetsPage) } ] }], { initialEntries: [path] });
  await run(() => root.render(strict ? h(StrictMode, null, h(RouterProvider, { router })) : h(RouterProvider, { router }))); await flush();
}
async function unmount() { await run(() => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
function server() {
  const queue = new Map();
  const state = { calls: [], session: syntheticSession(actor, patient, 'csrf-synthetic'), relations: [relation(patient), relation(patient2)], current: view(), original: null,
    losePrepare: false, loseCommit: false, unknown: false, commits: 0, rejectPrepare: false, rejectSubmit: false };
  state.enqueue = (path, response) => { const values = queue.get(path) ?? []; values.push(response); queue.set(path, values); };
  state.count = path => state.calls.filter(call => call.path === path).length;
  state.receipt = identity => ({ resource_type: 'alert_preset_choice', resource_id: randomUUID(), version: String(BigInt(identity.payload.expected_version) + 1n), saved_at: new Date().toISOString(), request_id: identity.request_id });
  state.respond = (path, options) => {
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap('csrf-bootstrap'));
    if (path === '/api/session') return Response.json(state.session);
    if (path === '/api/relations') return Response.json({ relations: state.relations });
    if (path === '/api/patient-selection') { state.session.selected_patient_id = JSON.parse(options.body).patient_id; return Response.json({ selected_patient_id: state.session.selected_patient_id }); }
    if (path.endsWith('/alert-presets')) return Response.json(path === directory ? state.current : view(patient2));
    if (path === '/api/write-intents') {
      if (state.rejectPrepare) return error('PRESET_ASSIGNMENT_CHANGED');
      const input = JSON.parse(options.body);
      assert.equal(input.operation_id, 'alert_preset_select'); assert.equal(input.schema_version, '1.0.0'); assert.equal(input.target_key, target);
      if (!state.original) state.original = { patient_id: patient, intent_id: randomUUID(), request_id: input.request_id, operation_id: input.operation_id, target_key: input.target_key,
        prepared_at: new Date().toISOString(), state: 'pending', may_retry: false, receipt: null, error_code: null, generation: '1', expires_at: new Date(Date.now() + 300000).toISOString(), payload_sha256: 'c'.repeat(64), preset_input: input.payload };
      else { assert.equal(input.request_id, state.original.request_id); assert.deepEqual(input.payload, state.original.preset_input); }
      if (state.losePrepare) { state.losePrepare = false; throw new TypeError('Synthetic lost prepare response'); }
      return Response.json({ ...syntheticIntent(state.original.intent_id, input.request_id), generation: state.original.generation, payload_sha256: state.original.payload_sha256 });
    }
    if (path.startsWith('/api/requests/')) {
      if (!state.original) return error('RESOURCE_NOT_FOUND', 404);
      return Response.json({ ...state.original, state: state.unknown ? 'unknown' : state.original.state });
    }
    if (path.startsWith('/api/write-intents/') && path.endsWith('/retry')) {
      assert.equal(JSON.parse(options.body).expected_generation, state.original.generation);
      state.original.generation = String(BigInt(state.original.generation) + 1n); state.original.state = 'pending'; state.original.may_retry = false;
      state.original.expires_at = new Date(Date.now() + 300000).toISOString();
      return Response.json({ ...syntheticIntent(state.original.intent_id, state.original.request_id), generation: state.original.generation, payload_sha256: state.original.payload_sha256 });
    }
    if (path === target) {
      assert.equal(options.headers['Idempotency-Key'], state.original.request_id); assert.equal(options.headers['X-Write-Intent'], state.original.intent_id);
      assert.equal(options.headers['X-Intent-Generation'], state.original.generation); assert.deepEqual(JSON.parse(options.body), state.original.preset_input);
      if (state.rejectSubmit) { state.original.expires_at = '2020-01-01T00:00:00Z'; state.original.may_retry = true; return error('PRESET_INTENT_EXPIRED'); }
      if (!state.original.receipt) { state.commits++; state.original.receipt = state.receipt({ request_id: state.original.request_id, payload: state.original.preset_input }); state.original.state = 'committed';
        state.current = { ...state.current, selection_state: 'selected', selection_version: state.original.receipt.version, selected_schedule_config_id: state.original.preset_input.schedule_config_id }; }
      if (state.loseCommit) { state.loseCommit = false; throw new TypeError('Synthetic lost receipt'); }
      return Response.json(state.original.receipt);
    }
    throw new Error(`Unexpected synthetic request ${path}`);
  };
  globalThis.fetch = async (url, options = {}) => {
    const path = new URL(url, 'http://localhost').pathname;
    state.calls.push({ path, url, options }); const queued = queue.get(path)?.shift();
    if (queued instanceof Error) throw queued;
    if (queued !== undefined) return await queued;
    return state.respond(path, options);
  };
  return state;
}
const checks = []; const pass = name => { checks.push(name); console.log(`PASS ${name}`); };
{
  assert.equal(validateAlertPresets(view(), patient).options.length, 2);
  for (const invalid of [ { ...view(), patient_id: patient2 }, { ...view(), options: [view().options[0], view().options[0]] },
    { ...view(), clinical_threshold: 42 }, { ...view(), selected_schedule_config_id: randomUUID() }, { ...view(), selection_state: 'requires_reselection' },
    { ...view(), options: [{ ...view().options[0], weight: 2 }] } ]) assert.throws(() => validateAlertPresets(invalid, patient));
  pass('strict directory schema rejects foreign patients, duplicate/unapproved options, clinical controls and inconsistent selection');
}
{
  const state = server(); let current = true;
  const request = createAlertPresetSubmission(patient, body(), { isCurrent: () => current });
  const pending = request.submit(); current = false;
  await assert.rejects(pending, error => error.name === 'AbortError'); assert.equal(state.calls.length, 0);
  pass('live authorization guard closes pre-transport microtask');
}
{
  const state = server(), gate = deferred(); state.enqueue('/api/write-intents', gate.promise); let current = true;
  const request = createAlertPresetSubmission(patient, body(), { isCurrent: () => current }); const pending = request.submit(); await new Promise(done => setTimeout(done, 0));
  current = false; gate.resolve(Response.json(syntheticIntent(randomUUID(), request.requestId)));
  await assert.rejects(pending, error => error.name === 'AbortError'); assert.equal(state.count(target), 0);
  pass('late prepare response cannot submit after lease invalidation');
}
{
  const state = server(); await mount(); assert.equal(screen().querySelectorAll('input[type="radio"]').length, 2);
  assert.equal(screen().querySelectorAll('input[type="number"],input[type="range"],textarea').length, 0);
  assert.equal(button('保存方案选择').disabled, true); await chooseB();
  const save = button('保存方案选择'); await run(() => { Simulate.click(save); Simulate.click(save); });
  assert.equal(state.count('/api/write-intents'), 1); assert.equal(state.count(target), 1); assert.equal(state.commits, 1);
  assert.ok(text().includes('已保存方案选择')); assert.equal(button('保存方案选择').disabled, true);
  assert.ok(!router.state.location.search.includes('alert_preset_request')); await unmount();
  pass('actual page exposes only approved radios; double-click creates one write and refreshes current selection');
}
{
  const state = server(); state.loseCommit = true; await mount(); await chooseB(); await click(button('保存方案选择'));
  assert.equal(state.commits, 1); assert.equal(screen().querySelector('fieldset').disabled, true);
  const originalId = state.original.request_id; await click(button('重试本次选择'));
  assert.equal(state.commits, 1); assert.equal(state.count(target), 1); assert.equal(state.original.request_id, originalId); assert.ok(text().includes('已保存方案选择')); await unmount();
  pass('lost committed receipt queries original ID and never duplicates the selection');
}
{
  const state = server(); state.losePrepare = true; await mount(); await chooseB(); await click(button('保存方案选择'));
  assert.equal(state.count(target), 0); const originalId = state.original.request_id;
  await click(button('重试本次选择')); assert.equal(state.commits, 1); assert.equal(state.count('/api/write-intents'), 1); assert.equal(state.original.request_id, originalId); await unmount();
  pass('lost preparation is recovered by original request lookup without new request ID');
}
{
  const state = server(); state.rejectSubmit = true; await mount(); await chooseB(); await click(button('保存方案选择'));
  await click(button('核对原请求')); assert.ok(text().includes('原请求已过期'));
  state.rejectSubmit = false; await click(button('重试本次选择'));
  assert.equal(state.count(`/api/write-intents/${state.original.intent_id}/retry`), 1); assert.equal(state.original.generation, '2'); assert.equal(state.commits, 1); await unmount();
  pass('expired proven-uncommitted intent is renewed with expected generation and same frozen body');
}
{
  const state = server(); state.losePrepare = true; await mount(); await chooseB(); await click(button('保存方案选择'));
  state.unknown = true; await click(button('核对原请求')); assert.equal(button('重试本次选择'), undefined);
  assert.equal(button('确认未保存后重新选择'), undefined); assert.equal(state.count(target), 0); assert.equal(screen().querySelector('fieldset').disabled, true); await unmount();
  pass('unknown server outcome keeps choices locked and only permits read-only recovery');
}
{
  const state = server(); state.loseCommit = true; await mount(); await chooseB(); await click(button('保存方案选择'));
  const path = '/alert-presets' + router.state.location.search; await unmount(); await mount(path, true);
  assert.ok(text().includes('已保存方案选择')); assert.equal(state.count(target), 1); assert.equal(state.commits, 1); await unmount();
  pass('reload marker recovers committed result read-only, bound to actor and patient');
}
{
  const state = server(); await mount(`/alert-presets?alert_preset_request=${randomUUID()}&alert_preset_actor=${randomUUID()}&alert_preset_patient=${patient}`);
  assert.equal(state.calls.filter(call => call.path.startsWith('/api/requests')).length, 0); assert.ok(!router.state.location.search.includes('alert_preset_request')); await unmount();
  pass('foreign actor recovery marker is discarded without querying or sending');
}
{
  const state = server(); state.relations[0].actions = ['read_raw']; await mount(); assert.ok(text().includes('没有方案选择权限')); assert.equal(state.count(directory), 0);
  assert.equal(screen().querySelectorAll('input').length, 0); await unmount();
  pass('select_alert_preset authority is mandatory even when raw-record access exists');
}
{
  const state = server(); state.current.options = []; state.current.selected_schedule_config_id = null; await mount(); assert.ok(text().includes('未取得可用的已批准方案')); assert.equal(button('保存方案选择'), undefined); await unmount();
  const next = server(); next.enqueue(directory, error('PRESET_ASSIGNMENT_UNAVAILABLE', 422)); await mount(); assert.ok(text().includes('未取得可用的已批准方案')); assert.equal(screen().querySelectorAll('input').length, 0); await unmount();
  pass('empty and unqualified assignments render explicit safe states without controls');
}
{
  const state = server(), late = deferred(); state.enqueue(directory, late.promise); await mount();
  state.relations[0].authorization_scope_fingerprint = 'd'.repeat(64);
  await run(() => access.refresh()); late.resolve(Response.json({ ...view(), display_name: 'STALE_FORBIDDEN_LABEL' })); await flush();
  assert.ok(!text().includes('STALE_FORBIDDEN_LABEL')); assert.equal(state.count(directory), 2); await unmount();
  pass('same-patient authorization revision discards a late directory response');
}
{
  const state = server(), late = deferred(); await mount(); await chooseB(); state.enqueue('/api/write-intents', late.promise); await click(button('保存方案选择'));
  const input = JSON.parse(state.calls.find(call => call.path === '/api/write-intents').options.body);
  await run(() => access.selectPatient(patient2)); late.resolve(Response.json(syntheticIntent(randomUUID(), input.request_id))); await flush();
  assert.equal(state.count(target), 0); assert.ok(!router.state.location.search.includes('alert_preset_request')); await unmount();
  pass('patient switch fences late preparation and clears patient-bound recovery marker');
}
{
  const state = server(); state.current.selection_state = 'requires_reselection'; state.current.selection_version = '1'; state.current.selected_schedule_config_id = null;
  await mount(); assert.ok(text().includes('请重新选择当前已批准')); assert.equal(button('保存方案选择').disabled, true); await chooseB(); assert.equal(button('保存方案选择').disabled, false); await unmount();
  pass('stale professional assignment explicitly requires a fresh approved choice');
}
{
  const state = server(); const request = createAlertPresetSubmission(patient, body(), {}); state.losePrepare = true; await assert.rejects(request.submit());
  state.original.receipt = state.receipt({ request_id: randomUUID(), payload: body() }); state.original.state = 'committed';
  await assert.rejects(readAlertPresetRequest(patient, request.requestId)); assert.equal(state.count(target), 0);
  pass('foreign recovery receipt is rejected before adoption');
}
{
  const state = server(); state.enqueue('/api/write-intents', new TypeError('Synthetic prepare never reached server'));
  await mount(); await chooseB(); await click(button('保存方案选择')); assert.equal(state.original, null);
  const first = JSON.parse(state.calls.find(call => call.path === '/api/write-intents').options.body);
  await click(button('重试本次选择')); assert.equal(state.original.request_id, first.request_id); assert.equal(state.commits, 1);
  assert.equal(state.count('/api/write-intents'), 2); await unmount();
  pass('unregistered lost prepare is queried first and replays the exact same request ID/body');
}
{
  const state = server(); state.rejectSubmit = true; await mount(); await chooseB(); await click(button('保存方案选择'));
  await click(button('核对原请求')); assert.equal(button('确认未保存后重新选择'), undefined);
  state.original.state = 'failed'; state.original.may_retry = false; state.original.error_code = 'PRESET_SELECTION_SUPERSEDED';
  const oldId = state.original.request_id;
  state.current = { ...state.current, assignment_id: randomUUID(), selection_version: '1', selection_state: 'requires_reselection', selected_schedule_config_id: null };
  await click(button('核对原请求')); assert.ok(text().includes('原选择已失效且确认未保存')); assert.equal(button('重试本次选择'), undefined);
  await click(button('确认未保存后重新选择')); assert.equal(screen().querySelector('fieldset').disabled, false);
  assert.equal(state.count('/api/write-intents'), 1); assert.ok(!router.state.location.search.includes(oldId)); assert.equal(button('保存方案选择').disabled, true);
  await chooseB(); state.rejectSubmit = false; state.original = null; await click(button('保存方案选择'));
  assert.notEqual(state.original.request_id, oldId); assert.equal(state.original.preset_input.assignment_id, state.current.assignment_id); assert.equal(state.commits, 1); await unmount();
  pass('only explicit superseded-and-uncommitted outcome unlocks reload and a user-chosen new request');
}
{
  const state = server(), gate = deferred(); let current = true;
  state.rejectSubmit = true;
  const request = createAlertPresetSubmission(patient, body(), { isCurrent: () => current }); await assert.rejects(request.submit()); state.rejectSubmit = false;
  state.enqueue(`/api/write-intents/${state.original.intent_id}/retry`, gate.promise);
  const retried = request.submit(); await new Promise(done => setTimeout(done, 0)); current = false;
  gate.resolve(Response.json({ ...syntheticIntent(state.original.intent_id, state.original.request_id), generation: '2', payload_sha256: state.original.payload_sha256 }));
  await assert.rejects(retried, failure => failure.name === 'AbortError'); assert.equal(state.count(target), 1); assert.equal(state.commits, 0);
  pass('late generation-renewal response cannot submit after lease revocation');
}
{
  const state = server(), gate = deferred(); let current = true;
  state.enqueue(target, { ok: true, json: () => gate.promise });
  const request = createAlertPresetSubmission(patient, body(), { isCurrent: () => current }); const saving = request.submit();
  await new Promise(done => setTimeout(done, 0)); current = false; gate.resolve(state.receipt({ request_id: request.requestId, payload: body() }));
  await assert.rejects(saving, failure => failure.name === 'AbortError'); assert.equal(request.uncertain, true);
  pass('late receipt JSON is fenced before adoption after authorization expires');
}
console.log(`PASS ${checks.length} approved alert preset DOM/contract checks (synthetic, no browser/model calls)`);
