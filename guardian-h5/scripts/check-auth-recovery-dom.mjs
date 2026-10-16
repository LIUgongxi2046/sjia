// Actual access/login/draft/submission modules; synthetic deferred network only.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticIntent, fixtureTime } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/login' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'HTMLButtonElement', 'Event']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act, useState } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { LoginPage } = await import('../src/features/account/LoginPage.tsx');
const { useFormDraft } = await import('../src/state/useFormDraft.tsx');
const { useRecordSubmission } = await import('../src/state/useRecordSubmission.ts');
const { createRecordSubmission } = await import('../src/api/recordSubmission.ts');
const { navigationMemory } = await import('../src/state/navigation.ts');
const { getSessionCsrfToken } = await import('../src/api/client.ts');
const actor = randomUUID(), actor2 = randomUUID(), patient = randomUUID(), patient2 = randomUUID();
const relation = (id = patient, extra = {}) => ({ authorization_scope_fingerprint: 'a'.repeat(64), authorization_scope_valid_until: null, relation_id: randomUUID(), patient_id: id, display_id: 'SYNTHETIC', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'write_record', 'manage_draft'], ...extra });
const failure = (status = 503) => Response.json({ code: 'SYNTHETIC_FAILURE', message: `合成读取失败 ${status}`, retryable: status === 503, recovery: status === 401 ? 'reauthenticate' : 'retry_after' }, { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
async function run(fn) { let result; await act(async () => { result = await fn(); }); await flush(); return result; }
let access, submission, draft, edit, marked, root, router;
function Capture() { access = useAccess(); return null; }
function Probe() {
  const [payload, setPayload] = useState({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC ORIGINAL' }] });
  edit = setPayload; draft = useFormDraft('observation', payload, setPayload);
  submission = useRecordSubmission({ ...draft, markSubmitted: () => { marked++; draft.markSubmitted(); } }, 'observation');
  return h('div', { 'data-testid': 'clinical' }, payload.fields[0].raw_text);
}
function server(authenticated = false) {
  const queue = new Map();
  const state = { session: authenticated ? syntheticSession(actor, patient, 'csrf-session') : null, relations: [relation(), relation(patient2)],
    calls: [], relationsFail: false, recoveryFail: false, loseRecord: false, recordPosts: [], intents: new Map(), bootstrapToken: 'csrf-bootstrap' };
  state.enqueue = (path, result) => { const items = queue.get(path) ?? []; items.push(result); queue.set(path, items); };
  state.count = path => state.calls.filter(call => call.path === path).length;
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(path, 'http://localhost'), body = options.body ? JSON.parse(options.body) : null;
    const entry = { path: url.pathname, body, options }; state.calls.push(entry);
    const queued = queue.get(url.pathname)?.shift(); if (queued !== undefined) return await queued;
    if (url.pathname === '/api/auth/bootstrap') return Response.json(syntheticBootstrap(state.bootstrapToken));
    if (url.pathname === '/api/session') return state.session ? Response.json(state.session) : failure(401);
    if (['/api/auth/login', '/api/auth/register'].includes(url.pathname)) { state.session = syntheticSession(actor, patient, 'csrf-auth'); return Response.json(state.session, { status: url.pathname.endsWith('register') ? 201 : 200 }); }
    if (url.pathname === '/api/relations') return state.relationsFail ? failure() : Response.json({ relations: state.relations });
    if (url.pathname === '/api/session/logout') { assert.equal(options.headers['X-CSRFToken'], state.bootstrapToken); state.session = null; return Response.json({}); }
    if (url.pathname === '/api/patient-selection') { state.session.selected_patient_id = body.patient_id; return Response.json({ selected_patient_id: body.patient_id }); }
    if (url.pathname.endsWith('/requests') || url.pathname.startsWith('/api/requests/')) {
      if (state.recoveryFail) return failure();
      const values = [...state.intents.values()].map(value => ({ ...value, receipt: null, resource_id: null, may_retry: false, expires_at: fixtureTime }));
      if (url.pathname.startsWith('/api/requests/')) return Response.json(values.find(value => value.intent_id === url.pathname.split('/').at(-1)));
      return Response.json({ patient_id: url.pathname.split('/')[3], requests: values.filter(value => value.patient_id === url.pathname.split('/')[3]) });
    }
    if (url.pathname === '/api/write-intents') {
      let intent = state.intents.get(body.request_id);
      if (!intent) { intent = { intent_id: randomUUID(), request_id: body.request_id, patient_id: patient, operation_id: body.operation_id, target_key: body.target_key, generation: '1', status: 'prepared', prepared_at: '2026-10-11T00:00:00Z', record_input: body.payload }; state.intents.set(body.request_id, intent); }
      else assert.deepEqual(body.payload, intent.record_input);
      return Response.json(syntheticIntent(intent.intent_id, intent.request_id));
    }
    if (url.pathname.endsWith('/records') && options.method === 'POST') { state.recordPosts.push(entry); if (state.loseRecord) throw new TypeError('Synthetic lost response'); return Response.json(receipt()); }
    if (url.pathname.endsWith('/drafts') && options.method === 'POST') return Response.json(draftReceipt(body.payload));
    throw new Error(`Unexpected synthetic request ${path}`);
  };
  return state;
}
const receipt = () => ({ patient_id: patient, record_id: randomUUID(), record_version_id: randomUUID(), version: 1 });
const draftReceipt = payload => ({ draft_id: randomUUID(), patient_id: patient, form_type: 'observation', version: '1', status: 'editing', saved_at: '2026-10-11T00:00:00Z', payload });
async function mount(path = '/login') {
  marked = 0; root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Capture), h(Outlet)), children: [
    { path: '/login', element: h(LoginPage) }, { path: '/home', element: h('p', null, 'SYNTHETIC HOME') }, { path: '/record', element: h(Probe) },
  ] }], { initialEntries: [path] });
  await run(() => root.render(h(RouterProvider, { router }))); await flush();
}
async function unmount() { await run(() => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
const page = () => document.querySelector('[data-screen-id="UI2-17"]')?.shadowRoot;
const button = text => [...page().querySelectorAll('button')].find(node => node.textContent === text);
const click = async node => { assert.ok(node); await run(() => Simulate.click(node)); };
const change = async (node, value) => { assert.ok(node); await run(() => Simulate.change(node, { target: { value } })); };
const closed = () => { assert.equal(access.patientId, null); assert.deepEqual(access.relations, []); assert.deepEqual(access.capabilities, []); assert.equal(access.hasCurrentAccess(), false); };
const checks = []; const pass = text => { checks.push(text); console.log(`PASS ${text}`); };
for (const mode of ['login', 'register']) {
  const state = server(); state.relationsFail = true; await mount();
  if (mode === 'register') await click(button('没有账号，去注册'));
  await change(page().querySelector('[name=username]'), 'synthetic'); await change(page().querySelector('[name=password]'), 'synthetic-password');
  const password = page().querySelector('[name=password]'), gate = deferred(); state.enqueue('/api/relations', gate.promise);
  await run(() => Simulate.submit(page().querySelector('form')));
  assert.equal(password.value, ''); assert.equal(access.principalId, actor); closed();
  await run(() => gate.resolve(failure()));
  assert.equal(access.accountStatus, 'active'); assert.equal(access.errorSource, 'relations'); closed();
  assert.equal(getSessionCsrfToken(), 'csrf-auth'); assert.equal(page().querySelector('form'), null);
  assert.match(page().textContent, /当前账号已登录.*无需重复注册/s); assert.ok(!page().textContent.includes('身份核验未完成'));
  assert.equal(router.state.location.pathname, '/login');
  await click(button('重新核验访问权限')); closed(); assert.equal(access.principalId, actor);
  state.relationsFail = false; await click(button('重新核验访问权限'));
  assert.equal(access.patientId, patient); assert.equal(access.error, null); await click(button('进入首页'));
  assert.equal(router.state.location.pathname, '/home'); assert.equal(state.count(`/api/auth/${mode}`), 1);
  await unmount(); pass(`${mode} succeeds before relations 503: clear password, retain identity, retry reads only, restore validated access`);
}
{
  const state = server(); state.enqueue('/api/auth/login', failure(401)); await mount();
  await change(page().querySelector('[name=username]'), 'synthetic'); await change(page().querySelector('[name=password]'), 'synthetic-password');
  await run(() => Simulate.submit(page().querySelector('form')));
  assert.equal(page().querySelector('[name=password]').value, ''); assert.match(page().textContent, /身份核验未完成/); closed();
  await unmount(); pass('rejected credentials clear password and do not claim login success');
}
{
  const state = server(true); await mount('/home'); const old = deferred(); state.enqueue('/api/relations', old.promise);
  let retry; await run(() => { retry = access.refresh(); });
  const gate = deferred(); state.enqueue('/api/session/logout', gate.promise); let logout; await run(() => { logout = access.logout(); });
  closed(); assert.equal(access.principalId, null); assert.equal(getSessionCsrfToken(), null);
  await run(() => old.resolve(Response.json({ relations: state.relations }))); await retry; closed(); assert.equal(access.principalId, null);
  await run(() => gate.resolve(Response.json({}))); await logout; assert.equal(access.loading, false);
  await unmount(); pass('logout immediately invalidates a delayed relation retry and ignores its late success');
}
{
  const state = server(true); await mount('/home'); const old = deferred(); state.enqueue('/api/relations', old.promise);
  let retry; await run(() => { retry = access.refresh(); }); state.session = syntheticSession(actor2, patient2, 'csrf-new'); await run(() => access.refresh());
  assert.equal(access.principalId, actor2); assert.equal(access.patientId, patient2);
  await run(() => old.resolve(failure())); await retry; assert.equal(access.principalId, actor2); assert.equal(access.patientId, patient2); assert.equal(access.error, null);
  await unmount(); pass('stale relation failure cannot overwrite a newer account and patient');
}
{
  const state = server(true); await mount('/home'); const gate = deferred(); state.enqueue('/api/patient-selection', gate.promise);
  let selection; await run(() => { selection = access.selectPatient(patient2).catch(error => error); }); await run(() => access.logout());
  await run(() => gate.resolve(Response.json({ selected_patient_id: patient2 }))); assert.ok(await selection instanceof Error); closed();
  await unmount(); pass('late patient-selection success after logout cannot restore access');
}
{
  const state = server(true); await mount('/home'); navigationMemory.rememberScroll('private', 123);
  const old = createRecordSubmission(patient, { kind: 'observation', fields: [] }); state.session = syntheticSession(actor2, patient2);
  await run(() => access.refresh()); assert.equal(navigationMemory.readScroll('private'), 0); const count = state.calls.length;
  await assert.rejects(old.submit(), /登录身份或当前患儿已改变/); assert.equal(state.calls.length, count);
  state.relations = [relation(patient2, { status: 'revoked' }), relation(patient, { valid_from: fixtureTime })]; await run(() => access.refresh()); assert.equal(access.patientId, null);
  state.session = null; await run(() => access.refresh()); closed(); assert.equal(access.principalId, null);
  await unmount(); pass('account change clears navigation/pending writes; revoked, future and expired access stay closed');
}
{
  const state = server(true); state.loseRecord = true; await mount('/record');
  await run(async () => { await assert.rejects(submission.submit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC FROZEN' }] }), /尚未确认/); });
  const original = [...state.intents.values()][0], marker = router.state.location.search; assert.match(marker, /record_intent=/);
  await run(() => access.refresh()); assert.equal(submission.recovering, true);
  const gate = deferred(); state.enqueue('/api/relations', gate.promise); const staleSubmit = submission.submit, staleSave = draft.save;
  let refresh; await run(() => { refresh = access.refresh(); }); const count = state.calls.length;
  await assert.rejects(staleSubmit({}), /登录身份或当前患儿已改变/); assert.equal(await staleSave(), false); assert.equal(state.calls.length, count);
  await run(() => gate.resolve(failure())); await refresh; closed(); assert.equal(router.state.location.search, marker);
  assert.ok(document.querySelector('[data-testid=clinical]').closest('[hidden]')); assert.match(document.body.textContent, /未保存的本地编辑已清除/);
  state.recoveryFail = true; await run(() => access.refresh()); assert.ok(submission.recoveryError);
  await run(async () => { await assert.rejects(submission.submit({ kind: 'observation', fields: [] }), /请先核对/); }); assert.equal(state.intents.size, 1);
  state.recoveryFail = false; await run(() => submission.recheck()); assert.equal(submission.recoveries[0].request_id, original.request_id);
  state.loseRecord = false; await run(() => submission.recover(submission.recoveries[0]));
  assert.equal(state.recordPosts.length, 2); assert.equal(state.intents.size, 1); assert.equal(state.recordPosts[1].options.headers['Idempotency-Key'], original.request_id);
  assert.equal(router.state.location.search, ''); assert.equal(marked, 1);
  await unmount(); pass('same-patient refresh preserves frozen request; failed relation check clears clinical state, blocks new keys and recovers the original server-bound request');
}
for (const kind of ['revoked', 'patient', 'account', 'expired']) {
  const state = server(true); state.loseRecord = true; await mount('/record');
  await run(async () => { await assert.rejects(submission.submit({ kind: 'observation', fields: [] })); }); const original = [...state.intents.values()][0];
  state.relationsFail = true; await run(() => access.refresh()); assert.match(router.state.location.search, /record_intent=/); state.relationsFail = false;
  if (kind === 'revoked') state.relations = [];
  if (kind === 'patient') state.session = syntheticSession(actor, patient2);
  if (kind === 'account') state.session = syntheticSession(actor2, patient2);
  if (kind === 'expired') state.session = null;
  const reads = state.count(`/api/requests/${original.intent_id}`); await run(() => access.refresh());
  assert.equal(router.state.location.search, ''); assert.equal(state.count(`/api/requests/${original.intent_id}`), reads); assert.equal(state.recordPosts.length, 1); assert.equal(state.intents.size, 1);
  await unmount(); pass(`${kind} on retry scrubs old recovery marker before any old-scope request, with no automatic replay`);
}
{
  const state = server(true); await mount('/record'); const gate = deferred(); state.enqueue(`/api/patients/${patient}/records`, gate.promise);
  let saving; await run(() => { saving = submission.submit({ kind: 'observation', fields: [] }).catch(error => error); }); const marker = router.state.location.search; assert.match(marker, /record_intent=/);
  const relationGate = deferred(); state.enqueue('/api/relations', relationGate.promise); let refresh; await run(() => { refresh = access.refresh(); });
  await run(() => gate.resolve(Response.json(receipt()))); assert.ok(await saving instanceof Error); assert.equal(marked, 0); assert.equal(router.state.location.search, marker);
  await run(() => relationGate.resolve(failure())); await refresh; assert.equal(router.state.location.search, marker);
  await unmount(); pass('in-flight record receipt while permissions are unknown cannot be adopted or erase recovery identity');
}
{
  const state = server(true); await mount('/record'); const payload = { kind: 'observation', fields: [{ raw_text: 'SYNTHETIC EDIT' }] };
  await run(() => edit(payload)); const gate = deferred(); state.enqueue(`/api/patients/${patient}/drafts`, gate.promise); let saving; await run(() => { saving = draft.save(); });
  const relationGate = deferred(); state.enqueue('/api/relations', relationGate.promise); let refresh; await run(() => { refresh = access.refresh(); });
  await run(() => gate.resolve(Response.json(draftReceipt(payload)))); assert.equal(await saving, false); assert.equal(draft.draft, null); assert.ok(!router.state.location.search.includes('draft='));
  await run(() => relationGate.resolve(failure())); await refresh; await unmount(); pass('in-flight draft receipt while permissions are unknown is not adopted or added to URL');
}
{
  const state = server(true); await mount('/home'); state.enqueue('/api/session/logout', failure());
  await run(async () => { await assert.rejects(access.logout()); }); closed(); assert.equal(access.principalId, null); assert.equal(access.errorSource, 'logout');
  const count = state.count('/api/session'); await run(() => access.refresh()); assert.equal(state.count('/api/session'), count); await assert.rejects(access.login('synthetic', 'synthetic'), /当前账号操作/);
  state.bootstrapToken = 'csrf-rotated-after-lost-logout'; await run(() => access.logout()); assert.equal(access.error, null); assert.equal(access.principalId, null);
  await unmount(); pass('failed logout clears local access, blocks resurrection and retries with a freshly bootstrapped CSRF token');
}
for (const expiresDuringRead of [false, true]) {
  const state = server(true); state.session.expires_at = new Date(Date.now() + (expiresDuringRead ? 80 : -1000)).toISOString();
  const gate = deferred(); if (expiresDuringRead) state.enqueue('/api/relations', gate.promise);
  await mount('/home'); await act(async () => { await new Promise(done => setTimeout(done, 100)); });
  if (expiresDuringRead) await run(() => gate.resolve(Response.json({ relations: state.relations })));
  closed(); assert.equal(access.principalId, null); assert.equal(access.loading, false); assert.equal(getSessionCsrfToken(), null);
  await unmount(); pass(`${expiresDuringRead ? 'delayed relation success after expiry' : 'already expired session receipt'} cannot authenticate or reveal patient data`);
}
{
  const state = server(true); await mount('/record'); const gate = deferred(); state.enqueue(`/api/patients/${patient}/records`, gate.promise);
  let saving; await run(() => { saving = submission.submit({ kind: 'observation', fields: [] }).catch(error => error); });
  const marker = router.state.location.search; state.relationsFail = true; await run(() => access.refresh()); closed();
  await run(() => gate.resolve(Response.json(receipt()))); assert.ok(await saving instanceof Error); assert.equal(marked, 0); assert.equal(router.state.location.search, marker);
  await unmount(); pass('late record receipt after failed-check unmount cannot adopt a result or remove its original marker');
}
{
  const state = server(true); state.loseRecord = true; await mount('/record');
  await run(async () => { await assert.rejects(submission.submit({ kind: 'observation', fields: [] })); });
  state.enqueue('/api/session/logout', failure()); await run(async () => { await assert.rejects(access.logout()); });
  assert.equal(router.state.location.search, ''); closed(); await unmount(); pass('explicit logout clears the original recovery URL even when server logout fails');
}
{
  const state = server(); await mount(); const auth = deferred(); state.enqueue('/api/auth/login', auth.promise);
  let signingIn; await run(() => { signingIn = access.login('synthetic', 'synthetic-password'); });
  const sessionReads = state.count('/api/session'); await run(() => { window.dispatchEvent(new Event('pageshow')); document.dispatchEvent(new Event('visibilitychange')); });
  assert.equal(state.count('/api/session'), sessionReads, 'background refresh must not supersede authentication');
  let signingOut; await run(() => { signingOut = access.logout(); }); closed(); assert.equal(state.count('/api/session/logout'), 0, 'logout must wait for already-sent login');
  state.bootstrapToken = 'csrf-after-late-login'; await run(() => auth.resolve(Response.json(syntheticSession(actor, patient, 'csrf-late-login'))));
  assert.equal(await signingIn, false); await signingOut; assert.equal(state.count('/api/session/logout'), 1); assert.equal(state.count('/api/relations'), 0); closed();
  await unmount(); pass('background restore cannot interrupt sign-in; concurrent logout waits for cookie rotation and prevents late authentication revival');
}
{
  const state = server(true); state.relations = [relation(patient, { valid_until: new Date(Date.now() + 80).toISOString() })]; await mount('/record');
  assert.equal(access.patientId, patient); await act(async () => { await new Promise(done => setTimeout(done, 100)); });
  closed(); assert.equal(access.principalId, actor); assert.equal(access.errorSource, 'relations'); assert.ok(document.querySelector('[data-testid=clinical]').closest('[hidden]'));
  await unmount(); pass('live relation expiry hides and clears clinical content while retaining authenticated identity');
}
{
  const state = server(true); state.relations = [relation(), relation(patient2, { valid_until: new Date(Date.now() + 80).toISOString() })]; await mount('/home');
  const gate = deferred(); state.enqueue('/api/patient-selection', gate.promise); let selection; await run(() => { selection = access.selectPatient(patient2).catch(error => error); });
  await act(async () => { await new Promise(done => setTimeout(done, 100)); }); await run(() => gate.resolve(Response.json({ selected_patient_id: patient2 })));
  assert.ok(await selection instanceof Error); closed(); await unmount(); pass('a selected relation that expires before selection receipt is never adopted');
}
for (const status of [401, 403, 503]) {
  const state = server(true); await mount('/record'); state.enqueue('/api/patient-selection', failure(status));
  await run(async () => { await assert.rejects(access.selectPatient(patient2)); }); closed();
  if (status === 401) assert.equal(access.principalId, null);
  else assert.ok(document.querySelector('[data-testid=clinical]').closest('[hidden]'));
  await unmount(); pass(`patient selection ${status} does not reauthorize the previous patient scope`);
}
{
  const state = server(true); const marker = `?record_intent=${randomUUID()}&record_intent_actor=${actor}&record_intent_patient=${patient}&draft=${randomUUID()}`;
  await mount('/home' + marker); assert.equal(new URLSearchParams(router.state.location.search).get('record_intent_actor'), actor);
  await run(() => access.logout()); assert.equal(router.state.location.search, ''); closed();
  await unmount(); pass('provider clears scoped recovery/draft URLs on logout even on routes without submission hooks');
}
for (const key of ['relation_id', 'version', 'actions']) {
  const state = server(true); state.loseRecord = true; await mount('/record');
  await run(() => edit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC PRIVATE EDIT' }] }));
  await run(async () => { await assert.rejects(submission.submit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC FROZEN' }] })); });
  const oldScope = access.scopeVersion, oldDraftSave = draft.save, marker = router.state.location.search;
  if (key === 'relation_id') state.relations[0].relation_id = randomUUID();
  if (key === 'version') state.relations[0].version = '2';
  if (key === 'actions') state.relations[0].actions = ['read_raw'];
  await run(() => access.refresh());
  assert.notEqual(access.scopeVersion, oldScope); assert.equal(access.hasCurrentAccess(actor, patient, oldScope), false);
  assert.equal(document.querySelector('[data-testid=clinical]').textContent, 'SYNTHETIC ORIGINAL'); assert.equal(await oldDraftSave(), false);
  assert.equal(router.state.location.search, marker); assert.equal(state.recordPosts.length, 1);
  if (key === 'actions') { const count = state.calls.length; assert.equal(await draft.save(), false); await assert.rejects(submission.submit({})); assert.equal(state.calls.length, count); }
  else assert.equal(submission.recoveries.length, 1);
  await unmount(); pass(`same-patient ${key} change remounts clinical state and fences old callbacks without replaying the original write`);
}
{
  const state = server(true); state.relations[0].actions = ['read_raw', 'write_record']; await mount('/record');
  await run(() => edit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC PRIVATE EDIT' }] })); const count = state.calls.length;
  assert.equal(await draft.save(), false); assert.equal(state.calls.length, count);
  await unmount(); pass('read/write permission without manage_draft cannot autosave or explicitly save a draft');
}
{
  const state = server(true); await mount('/record'); const gate = deferred(); state.enqueue('/api/write-intents', gate.promise);
  let saving; await run(() => { saving = submission.submit({ kind: 'observation', fields: [] }).catch(error => error); });
  const requestId = state.calls.find(call => call.path === '/api/write-intents').body.request_id;
  state.relationsFail = true; await run(() => access.refresh());
  await run(() => gate.resolve(Response.json(syntheticIntent(randomUUID(), requestId)))); assert.ok(await saving instanceof Error);
  assert.equal(state.recordPosts.length, 0); assert.equal(state.calls.filter(call => call.path.endsWith('/records')).length, 0);
  await unmount(); pass('late unacknowledged intent preparation after access failure never advances to a clinical record POST');
}
for (const fingerprint of ['b'.repeat(64), null, undefined, 'malformed']) {
  const state = server(true); await mount('/record'); await run(() => edit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC PRIVATE EDIT' }] }));
  const oldVersion = access.scopeVersion;
  if (fingerprint === undefined) delete state.relations[0].authorization_scope_fingerprint;
  else state.relations[0].authorization_scope_fingerprint = fingerprint;
  await run(() => access.refresh()); assert.notEqual(access.scopeVersion, oldVersion);
  assert.equal(document.querySelector('[data-testid=clinical]').textContent, 'SYNTHETIC ORIGINAL');
  if (fingerprint === 'malformed') closed();
  else { assert.equal(access.patientId, patient); if (!fingerprint) { const version = access.scopeVersion; await run(() => access.refresh()); assert.notEqual(access.scopeVersion, version); } }
  await unmount(); pass(`${fingerprint === undefined ? 'missing' : fingerprint === null ? 'null' : fingerprint === 'malformed' ? 'malformed' : 'changed'} authorization digest never preserves previous clinical state`);
}
{
  const state = server(true); const marker = `?record_intent=${randomUUID()}&record_intent_actor=${actor}&record_intent_patient=${patient}&draft=${randomUUID()}`;
  await mount('/home' + marker); state.session = syntheticSession(actor2, patient2); state.relationsFail = true;
  await run(() => access.refresh()); assert.equal(access.principalId, actor2); closed(); assert.equal(router.state.location.search, '');
  await unmount(); pass('proved account change removes old scoped URLs even when the new account relation read fails');
}
{
  const state = server(true); await mount('/home'); const selectOld = access.selectPatient;
  state.session = syntheticSession(actor2, patient2); await run(() => access.refresh()); const count = state.calls.length;
  await assert.rejects(selectOld(patient), /当前关联不允许/); assert.equal(state.calls.length, count);
  await unmount(); pass('a retained old selectPatient callback cannot issue a request after account/scope changes');
}
for (const binding of ['b'.repeat(64), null, undefined, 'malformed']) {
  const state = server(true); await mount('/record'); await run(() => edit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC PRIVATE EDIT' }] }));
  const oldVersion = access.scopeVersion;
  if (binding === undefined) delete state.session.session_binding;
  else state.session.session_binding = binding;
  await run(() => access.refresh()); assert.notEqual(access.scopeVersion, oldVersion);
  assert.equal(document.querySelector('[data-testid=clinical]').textContent, 'SYNTHETIC ORIGINAL');
  if (binding === 'malformed') closed();
  else { assert.equal(access.patientId, patient); if (!binding) { const version = access.scopeVersion; await run(() => access.refresh()); assert.notEqual(access.scopeVersion, version); } }
  await unmount(); pass(`${binding === undefined ? 'missing' : binding === null ? 'null' : binding === 'malformed' ? 'malformed' : 'changed'} session binding never preserves previous clinical state`);
}
{
  const state = server(true); await mount('/record'); await run(() => edit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC PRIVATE EDIT' }] }));
  const version = access.scopeVersion;
  state.session.expires_at = '2099-01-02T00:00:00Z'; state.session.csrf_token = 'new-masked-token-same-bound-session';
  await run(() => access.refresh()); assert.equal(access.scopeVersion, version); assert.equal(document.querySelector('[data-testid=clinical]').textContent, 'SYNTHETIC PRIVATE EDIT');
  await unmount(); pass('unchanged session binding and scope digest preserve continuity despite refreshed expiry or masked CSRF token');
}
for (const field of ['valid_until', 'authorization_scope_valid_until']) {
  const state = server(true); state.relations = [relation(patient, { [field]: new Date(Date.now() + 100).toISOString() })];
  const marker = `?record_intent=${randomUUID()}&record_intent_actor=${actor}&record_intent_patient=${patient}&draft=${randomUUID()}`;
  await mount('/home' + marker); assert.equal(access.patientId, patient);
  await act(async () => { await new Promise(done => setTimeout(done, 120)); });
  closed(); assert.equal(access.principalId, actor); assert.equal(access.errorSource, 'relations'); assert.equal(router.state.location.search, '');
  await unmount(); pass(`${field} boundary closes live access and clears stale scoped URLs even outside record routes`);
}
{
  const state = server(true); delete state.relations[0].authorization_scope_valid_until; await mount('/record');
  await run(() => edit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC PRIVATE EDIT' }] })); const version = access.scopeVersion;
  await run(() => access.refresh()); assert.notEqual(access.scopeVersion, version); assert.equal(document.querySelector('[data-testid=clinical]').textContent, 'SYNTHETIC ORIGINAL');
  await unmount(); pass('missing temporal-scope declaration never preserves prior clinical state');
}
for (const failedRevalidation of [false, true]) {
  const state = server(true); await mount('/record'); await run(() => edit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC PRIVATE EDIT' }] }));
  const version = access.scopeVersion, gate = deferred(); state.enqueue('/api/patient-selection', gate.promise);
  let selected; await run(() => { selected = access.selectPatient(patient).catch(error => error); });
  state.relations[0] = { ...state.relations[0], authorization_scope_fingerprint: 'b'.repeat(64), actions: ['read_raw'] };
  state.relationsFail = failedRevalidation; const reads = state.count('/api/session'), relationReads = state.count('/api/relations');
  await run(() => gate.resolve(Response.json({ selected_patient_id: patient }))); const result = await selected;
  assert.equal(state.count('/api/session'), reads + 1); assert.equal(state.count('/api/relations'), relationReads + 1);
  assert.notEqual(access.scopeVersion, version); assert.equal(document.querySelector('[data-testid=clinical]').textContent, 'SYNTHETIC ORIGINAL');
  if (failedRevalidation) { assert.ok(result instanceof Error); closed(); }
  else { assert.equal(access.patientId, patient); const count = state.calls.length; await assert.rejects(submission.submit({})); assert.equal(state.calls.length, count); }
  await unmount(); pass(`selection commit uses fresh session and grants before adoption (${failedRevalidation ? 'failed revalidation stays closed' : 'changed write scope remounts and blocks writes'})`);
}
for (const change of ['target_revoked', 'session_replaced']) {
  const state = server(true); await mount('/home'); const gate = deferred(); state.enqueue('/api/patient-selection', gate.promise);
  let selected; await run(() => { selected = access.selectPatient(patient).catch(error => error); });
  if (change === 'target_revoked') state.relations = [relation(patient2)];
  else state.session.session_binding = 'b'.repeat(64);
  await run(() => gate.resolve(Response.json({ selected_patient_id: patient }))); assert.ok(await selected instanceof Error);
  closed(); assert.notEqual(access.patientId, patient2);
  await unmount(); pass(`selection ${change} between commit and recheck fails closed instead of accepting fallback/old-session authority`);
}
{
  const state = server(true); await mount('/record'); const gate = deferred(); state.enqueue('/api/relations', gate.promise);
  let saving, refresh;
  await run(() => {
    saving = submission.submit({ kind: 'observation', fields: [{ raw_text: 'SYNTHETIC SAME-TICK FROZEN' }] }).catch(error => error);
    refresh = access.refresh();
  });
  assert.ok(await saving instanceof Error); assert.equal(access.loading, true); assert.equal(state.count('/api/write-intents'), 0); assert.equal(state.recordPosts.length, 0);
  await run(() => gate.resolve(Response.json({ relations: state.relations }))); await refresh;
  await run(() => submission.retry()); assert.equal(state.count('/api/write-intents'), 1); assert.equal(state.recordPosts.length, 1);
  assert.equal(state.recordPosts[0].body.fields[0].raw_text, 'SYNTHETIC SAME-TICK FROZEN');
  await unmount(); pass('submit then refresh in the same tick sends nothing while suspended and later retries only the frozen original operation');
}
{
  const state = server(true); await mount('/record'); const payload = { kind: 'observation', fields: [{ raw_text: 'SYNTHETIC SAME-TICK DRAFT' }] };
  await run(() => edit(payload)); const gate = deferred(); state.enqueue('/api/relations', gate.promise); let saving, refresh;
  await run(() => { saving = draft.save(); refresh = access.refresh(); });
  assert.equal(await saving, false); assert.equal(access.loading, true); assert.equal(state.count(`/api/patients/${patient}/drafts`), 0);
  await run(() => gate.resolve(Response.json({ relations: state.relations }))); await refresh;
  assert.equal(await run(() => draft.save()), true); assert.equal(state.count(`/api/patients/${patient}/drafts`), 1);
  await unmount(); pass('draft save then refresh in the same tick cannot cross the import/fetch send boundary until access is revalidated');
}
{
  const state = server(true); await mount('/record'); const { readDraft } = await import('../src/api/endpoints.ts');
  const captured = access, id = randomUUID(), gate = deferred(); state.enqueue('/api/relations', gate.promise); let reading, refresh;
  await run(() => { reading = readDraft(id, undefined, { isCurrent: () => captured.hasCurrentAccess(captured.principalId, captured.patientId, captured.scopeVersion) }).catch(error => error); refresh = access.refresh(); });
  assert.ok(await reading instanceof Error); assert.equal(state.count(`/api/drafts/${id}`), 0);
  await run(() => gate.resolve(Response.json({ relations: state.relations }))); await refresh;
  await unmount(); pass('guarded draft restore then refresh in the same tick is fenced before any clinical read is sent');
}
dom.window.close();
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_ACTUAL_ACCESS_LOGIN_HOOK_DOM', checks }, null, 2));
