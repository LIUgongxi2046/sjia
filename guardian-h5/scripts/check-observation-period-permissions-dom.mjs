// Real AccessProvider, period page, APIs and response validation. Synthetic
// in-memory fetch only: no browser, database, network, provider or clinical rules.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticIntent, fixtureTime, fixtureDigest } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID, webcrypto } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/records/period' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'HTMLButtonElement', 'Event']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { UI2ObservationPeriodPage } = await import('../src/features/records/UI2ObservationPeriodPage.tsx');
const actor = randomUUID(), actor2 = randomUUID(), patient = randomUUID(), patient2 = randomUUID();
const periodId = randomUUID(), versionId = randomUUID(), intentId = randomUUID(), originalId = randomUUID();
const periodPath = id => `/api/v2/patients/${id}/observation-period`;
const requestPath = `/api/v2/requests/${intentId}`;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const failure = (status = 403) => Response.json({ code: status === 401 ? 'SESSION_EXPIRED' : 'OBSERVATION_PERIOD_FORBIDDEN', message: 'Synthetic period access denied', retryable: false, recovery: 'reauthenticate' }, { status });
const relation = id => ({ relation_id: randomUUID(), patient_id: id, display_id: 'SYNTHETIC', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, authorization_scope_fingerprint: fixtureDigest, authorization_scope_valid_until: null, actions: ['read_raw', 'write_record', 'view_request'] });
const date = local_date => ({ precision: 'date', raw_text: local_date, local_date, local_datetime: null, timezone_name: 'UTC', utc_offset_minutes: null, instant: null, resolution_status: 'local_only', local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null });
const view = (id = patient, label = 'SYNTHETIC CURRENT') => ({ patient_id: id, period_id: null, version_id: null, version: null, owner_id: null, label, label_source: null, episode_id: null, episode_label: null, created_at: null, is_current: true, start: null, end: null, timezone: 'UTC', target_interval_hours: null, interval_basis_ref: null, interval_policy_id: null, samplings: [], intervals: [], unknown_time_fact_ids: [], reason_codes: ['OBSERVATION_PERIOD_NOT_SELECTED'], snapshot_version: fixtureDigest, episode_options: [], allowed_actions: ['observation_period_change'] });
const payload = { expected_version: null, start: date('2026-10-01'), end: date('2026-10-10'), label: 'SYNTHETIC FROZEN INPUT', episode_id: null, reason: 'SYNTHETIC ORIGINAL REASON' };
const original = (overrides = {}) => ({ intent_id: intentId, request_id: originalId, operation_id: 'observation_period_change', target_key: `/patients/${patient}/observation-period/versions`, state: 'pending', may_retry: false, generation: '1', expires_at: fixtureTime, payload_sha256: fixtureDigest, receipt: null, error_code: null, period_input: structuredClone(payload), ...overrides });
let access, root, router;
function Capture() { access = useAccess(); return null; }
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
async function run(fn) { let result; await act(async () => { result = await fn(); }); await flush(); return result; }
const screen = () => document.querySelector('[data-screen-id]')?.shadowRoot ?? document;
const text = () => screen().textContent;
const button = label => [...screen().querySelectorAll('button')].find(node => node.textContent.includes(label));
const click = async node => { assert.ok(node, 'expected button'); assert.equal(node.disabled, false); await run(() => Simulate.click(node)); };
const change = async (node, value) => { assert.ok(node); await run(() => Simulate.change(node, { target: { value } })); };
const submit = async () => run(() => Simulate.submit(screen().querySelector('form')));
function server() {
  const queues = new Map();
  const state = { session: syntheticSession(actor, patient), relations: [relation(patient), relation(patient2)], calls: [], denied: null, request: original(), receipt: null };
  state.enqueue = (path, response) => { const queue = queues.get(path) ?? []; queue.push(response); queues.set(path, queue); };
  state.count = path => state.calls.filter(call => call.path === path).length;
  state.writes = () => state.calls.filter(call => call.options.method !== 'GET');
  globalThis.fetch = async (input, options = {}) => {
    const url = new URL(input, 'http://localhost'), path = url.pathname;
    state.calls.push({ path, input, options });
    // Bound the pre-fix regression itself instead of leaving an infinite loop.
    if (state.calls.length > 80) throw new Error('Synthetic request-loop safety stop');
    const queued = queues.get(path)?.shift(); if (queued !== undefined) return await queued;
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (path === '/api/session') return state.session ? Response.json(state.session) : failure(401);
    if (path === '/api/relations') return Response.json({ relations: state.relations });
    if (path === '/api/patient-selection') { state.session.selected_patient_id = JSON.parse(options.body).patient_id; return Response.json({ selected_patient_id: state.session.selected_patient_id }); }
    if (path === periodPath(patient) || path === periodPath(patient2)) return state.denied ? failure(state.denied) : Response.json(view(path.split('/')[4]));
    if (path === requestPath || path === `/api/v2/requests/${originalId}`) return Response.json(state.request);
    if (path === '/api/v2/write-intents') { const body = JSON.parse(options.body); state.request = original({ request_id: body.request_id, period_input: body.payload }); return Response.json(syntheticIntent(intentId, body.request_id)); }
    if (path === `/api/v2/write-intents/${intentId}/retry`) { assert.equal(JSON.parse(options.body).expected_generation, state.request.generation); state.request.generation = String(Number(state.request.generation) + 1); state.request.may_retry = false; return Response.json(syntheticIntent(intentId, state.request.request_id, state.request.generation)); }
    if (path === `/api/v2/patients/${patient}/observation-period/versions`) {
      assert.equal(options.headers['Idempotency-Key'], state.request.request_id);
      assert.equal(options.headers['X-Write-Intent'], state.request.intent_id);
      assert.equal(options.headers['X-Intent-Generation'], state.request.generation);
      assert.deepEqual(JSON.parse(options.body), state.request.period_input);
      state.receipt = { request_id: state.request.request_id, resource_type: 'observation_period', resource_id: versionId, version: '1', task_id: null, schema_version: '2.0.0', saved_at: '2026-10-11T00:00:00Z' };
      state.request.state = 'committed'; state.request.receipt = state.receipt;
      return Response.json(state.receipt);
    }
    if (path === `/api/v2/observation-period-versions/${versionId}`) return Response.json({ ...view(patient, state.request.period_input.label), period_id: periodId, version_id: versionId, version: '1', owner_id: actor, label_source: 'self_report', created_at: '2026-10-11T00:00:00Z' });
    throw new Error(`Unexpected synthetic request ${input}`);
  };
  return state;
}
async function mount(path = '/records/period') {
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Capture), h(Outlet)), children: [{ path: '*', element: h(UI2ObservationPeriodPage) }] }], { initialEntries: [path] });
  await run(() => root.render(h(RouterProvider, { router }))); await flush();
}
async function unmount() { await run(() => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
function closed() {
  assert.equal(button('使用此观察范围').disabled, true);
  const clinicalControls = screen().querySelectorAll('.page-content input, .page-content select');
  assert.equal(clinicalControls.length, 6, 'all observation-period controls remain covered');
  for (const input of clinicalControls) { assert.equal(input.disabled, true); assert.equal(input.value, ''); }
  assert.equal(screen().querySelector('.rich-theme-preference'), null, 'appearance settings stay on the account page');
  assert.equal(button('查询原请求结果'), undefined);
  assert.equal(button('重新授权原请求'), undefined);
}
const checks = []; function pass(label) { checks.push(label); console.log(`PASS ${label}`); }
try {
  for (const status of [403, 401]) {
    const state = server(); state.denied = status; await mount();
    for (let count = 0; count < 4; count++) await flush();
    assert.equal(state.count(periodPath(patient)), 1, `persistent ${status} has one bounded read`);
    assert.equal(state.count('/api/auth/bootstrap'), 1); assert.equal(state.count('/api/session'), 1); assert.equal(state.count('/api/relations'), 1);
    assert.match(text(), /Synthetic period access denied/); closed();
    await submit(); assert.equal(state.writes().length, 0);
    await click(button('重新核验并读取'));
    assert.equal(state.count(periodPath(patient)), 2); assert.equal(state.count('/api/session'), 2); closed();
    await flush(); assert.equal(state.count(periodPath(patient)), 2);
    state.denied = null; await click(button('重新核验并读取'));
    assert.equal(state.count(periodPath(patient)), 3); assert.equal(state.count('/api/session'), 3);
    assert.equal(screen().querySelector('input[placeholder]').value, 'SYNTHETIC CURRENT'); assert.equal(screen().querySelector('input').disabled, false);
    assert.equal(state.writes().length, 0); await unmount();
    pass(`persistent ${status} remains fail-closed; explicit failed/successful revalidation reads once and never writes`);
  }
  {
    const state = server(); delete state.relations[0].authorization_scope_fingerprint; delete state.relations[0].authorization_scope_valid_until;
    state.denied = 403; await mount(); await flush();
    assert.equal(state.count(periodPath(patient)), 1); assert.equal(state.count('/api/session'), 1); closed();
    await click(button('重新核验并读取')); await flush();
    assert.equal(state.count(periodPath(patient)), 2); assert.equal(state.count('/api/session'), 2); closed();
    assert.equal(state.writes().length, 0); await unmount();
    pass('unknown authorization fingerprint remounts only on explicit revalidation, never a backend-denial loop');
  }
  {
    const state = server(); state.denied = 403; await mount();
    const gate = deferred(); state.enqueue('/api/session', gate.promise);
    const retryButton = button('重新核验并读取');
    await run(() => { Simulate.click(retryButton); Simulate.click(retryButton); });
    assert.equal(state.count('/api/session'), 2); assert.equal(state.count(periodPath(patient)), 1);
    state.denied = null; await run(() => gate.resolve(Response.json(state.session)));
    assert.equal(state.count(periodPath(patient)), 2); assert.equal(state.writes().length, 0);
    await unmount(); pass('repeated explicit revalidation clicks share one in-flight check and one period read');
  }
  for (const failedStage of ['session', 'relations']) {
    const state = server(); state.denied = 403; await mount();
    state.enqueue(`/api/${failedStage}`, failure(failedStage === 'session' ? 401 : 503));
    await click(button('重新核验并读取'));
    assert.equal(state.count(periodPath(patient)), 1); assert.equal(state.writes().length, 0);
    assert.equal(access.hasCurrentAccess(), false); assert.equal(access.patientId, null);
    if (failedStage === 'session') assert.equal(access.principalId, null);
    assert.ok(!text().includes('SYNTHETIC CURRENT')); await unmount();
    pass(`explicit revalidation with failed ${failedStage} does not reopen period data or send writes`);
  }
  {
    const state = server(); await mount();
    const dates = screen().querySelectorAll('input[type=date]'); await change(dates[0], '2026-10-01'); await change(dates[1], '2026-10-10');
    let refreshing; await run(() => { Simulate.submit(screen().querySelector('form')); refreshing = access.refresh(); }); await refreshing; await flush();
    assert.equal(state.writes().length, 0); assert.equal(router.state.location.search, '');
    await unmount(); pass('submit and access suspension in the same tick cannot start intent preparation or a new clinical write');
  }
  for (const kind of ['account', 'patient', 'scope']) {
    for (const lateFailure of [false, true]) {
      const state = server(), gate = deferred(); state.enqueue(periodPath(patient), gate.promise); await mount();
      const oldVersion = access.scopeVersion;
      if (kind === 'account') state.session = syntheticSession(actor2, patient);
      if (kind === 'patient') state.session.selected_patient_id = patient2;
      if (kind === 'scope') state.relations[0].authorization_scope_fingerprint = 'b'.repeat(64);
      await run(() => access.refresh()); assert.notEqual(access.scopeVersion, oldVersion);
      await run(() => gate.resolve(lateFailure ? failure() : Response.json(view(patient, 'SYNTHETIC STALE PRIVATE'))));
      assert.equal(screen().querySelector('input[placeholder]').value, 'SYNTHETIC CURRENT');
      assert.ok(!text().includes('Synthetic period access denied')); assert.ok(!text().includes('SYNTHETIC STALE PRIVATE'));
      assert.equal(state.count('/api/session'), 2); assert.equal(state.writes().length, 0);
      await unmount(); pass(`late period ${lateFailure ? 'denial' : 'success'} after ${kind} change cannot alter current data or trigger refresh`);
    }
  }
  {
    const state = server(), gate = deferred(); state.enqueue(requestPath, gate.promise);
    await mount(`/records/period?request=${intentId}`);
    state.session.selected_patient_id = patient2;
    state.request = original({ target_key: `/patients/${patient2}/observation-period/versions`, period_input: { ...payload, label: 'SYNTHETIC CURRENT REQUEST' } });
    await run(() => access.refresh());
    await run(() => gate.resolve(Response.json(original({ period_input: { ...payload, label: 'SYNTHETIC STALE REQUEST' } }))));
    assert.equal(state.count(periodPath(patient)), 0); assert.equal(state.count(periodPath(patient2)), 1);
    assert.equal(screen().querySelector('input[placeholder]').value, 'SYNTHETIC CURRENT REQUEST'); assert.equal(state.writes().length, 0);
    await unmount(); pass('late original-request response cannot adopt stale input or initiate a read for the old patient');
  }
  for (const kind of ['account', 'patient', 'scope']) {
    for (const stage of ['prepare', 'submit', 'retry']) {
      const state = server(), gate = deferred();
      if (stage === 'retry') state.request = original({ may_retry: true });
      await mount(stage === 'prepare' ? '/records/period' : `/records/period?request=${intentId}`);
      const path = stage === 'prepare' ? '/api/v2/write-intents' : stage === 'submit' ? `/api/v2/patients/${patient}/observation-period/versions` : `/api/v2/write-intents/${intentId}/retry`;
      state.enqueue(path, gate.promise);
      if (stage === 'prepare') {
        const dates = screen().querySelectorAll('input[type=date]'); await change(dates[0], '2026-10-01'); await change(dates[1], '2026-10-10');
        await submit();
      } else if (stage === 'submit') await submit();
      else await click(button('重新授权原请求'));
      assert.equal(state.count(path), 1);
      const postedIdentity = stage === 'prepare' ? JSON.parse(state.calls.find(call => call.path === path).options.body) : null;
      if (kind === 'account') state.session = syntheticSession(actor2, patient);
      if (kind === 'patient') state.session.selected_patient_id = patient2;
      if (kind === 'scope') state.relations[0].authorization_scope_fingerprint = 'c'.repeat(64);
      await run(() => access.refresh());
      const callsBeforeLate = state.calls.length, writesBeforeLate = state.writes().length;
      const response = stage === 'submit'
        ? { request_id: state.request.request_id, resource_type: 'observation_period', resource_id: versionId, version: '1', task_id: null, schema_version: '2.0.0', saved_at: '2026-10-11T00:00:00Z' }
        : syntheticIntent(intentId, postedIdentity?.request_id ?? originalId, stage === 'retry' ? '2' : '1');
      await run(() => gate.resolve(Response.json(response)));
      assert.equal(state.calls.length, callsBeforeLate); assert.equal(state.writes().length, writesBeforeLate);
      assert.equal(state.count(`/api/v2/observation-period-versions/${versionId}`), 0);
      if (stage === 'prepare') assert.equal(state.count(`/api/v2/patients/${patient}/observation-period/versions`), 0);
      await unmount(); pass(`late ${stage} response after ${kind} change cannot continue a write, read a receipt or restore old identity`);
    }
  }
  {
    const state = server(); state.enqueue(periodPath(patient), failure());
    await mount(`/records/period?request=${intentId}`); closed();
    assert.equal(state.count(requestPath), 1); assert.equal(state.count(periodPath(patient)), 1); assert.equal(state.writes().length, 0);
    await click(button('重新核验并读取')); assert.equal(state.count(requestPath), 2);
    assert.equal(screen().querySelector('input[placeholder]').value, payload.label); assert.equal(state.writes().length, 0);
    await unmount(); pass('denied period read after successful request lookup clears recovered identity and frozen private inputs');
  }
  {
    const state = server(); state.enqueue(requestPath, failure()); await mount(`/records/period?request=${intentId}`);
    state.receipt = { schema_version: '2.0.0', request_id: originalId, resource_type: 'observation_period', resource_id: versionId, version: '1', task_id: null, saved_at: '2026-10-11T00:00:00Z' };
    state.request = original({ state: 'committed', receipt: state.receipt });
    await click(button('重新核验并读取'));
    assert.ok(button('读取当前范围并继续设置')); await submit(); assert.equal(state.writes().length, 0);
    assert.equal(state.count(`/api/v2/observation-period-versions/${versionId}`), 1);
    await unmount(); pass('lost committed receipt recovered after denial never duplicates a write or intent generation');
  }
  for (const byClient of [false, true]) {
    const state = server(), path = byClient ? `/api/v2/requests/${originalId}` : requestPath;
    state.enqueue(path, failure());
    await mount(`/records/period?${byClient ? `client_request=${originalId}` : `request=${intentId}`}`);
    closed(); assert.match(router.state.location.search, new RegExp(byClient ? originalId : intentId));
    await click(button('重新核验并读取'));
    assert.equal(state.count(path), 2); assert.equal(screen().querySelector('input[placeholder]').value, payload.label);
    assert.equal(screen().querySelector('input[placeholder]').disabled, true); assert.equal(state.writes().length, 0);
    await submit();
    assert.equal(state.count('/api/v2/write-intents'), 0); assert.equal(state.count(`/api/v2/patients/${patient}/observation-period/versions`), 1);
    assert.ok(button('读取当前范围并继续设置')); assert.ok(state.receipt);
    await unmount(); pass(`${byClient ? 'client' : 'intent'} URL marker survives denial and recovers the exact original payload without another intent/generation`);
  }
  {
    const state = server(); state.request = original({ may_retry: true }); state.enqueue(requestPath, failure());
    await mount(`/records/period?request=${intentId}`); await click(button('重新核验并读取'));
    assert.equal(state.writes().length, 0); await submit(); assert.equal(state.writes().length, 0);
    await click(button('重新授权原请求'));
    assert.equal(state.count(`/api/v2/write-intents/${intentId}/retry`), 1); assert.equal(state.request.generation, '2');
    await submit(); assert.equal(state.count('/api/v2/write-intents'), 0);
    assert.equal(state.count(`/api/v2/patients/${patient}/observation-period/versions`), 1);
    await unmount(); pass('expired original request requires explicit retry and keeps the same identity with exactly one new generation');
  }
  {
    const state = server(); await mount(`/records/period?request=${intentId}`);
    state.enqueue(`/api/v2/patients/${patient}/observation-period/versions`, failure());
    await submit(); closed(); assert.equal(state.count('/api/session'), 1); assert.equal(state.writes().length, 1);
    await submit(); assert.equal(state.writes().length, 1); await flush(); assert.equal(state.count('/api/session'), 1);
    await click(button('重新核验并读取'));
    assert.equal(state.writes().length, 1); assert.equal(screen().querySelector('input[placeholder]').value, payload.label);
    assert.equal(state.count('/api/v2/write-intents'), 0); assert.equal(state.count(requestPath), 2);
    await unmount(); pass('write denial clears private form and request state while explicit revalidation only queries the original result');
  }
} finally { if (root && document.getElementById('root').childNodes.length) await unmount(); }
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks, visual_comparison: 'NOT_RUN' }, null, 2));
