// Production components and contracts, synthetic values only; no real identity data.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { syntheticBootstrap, syntheticSession, syntheticIntent } from './response-test-fixtures.mjs';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const key of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLTextAreaElement','HTMLSelectElement','HTMLButtonElement','Event','ShadowRoot']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// JSDOM has no layout; give focusable controls nonzero synthetic rectangles.
Object.defineProperty(HTMLElement.prototype, 'getClientRects', { value() { return [{ width: 1, height: 1 }]; }, configurable: true });
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
let access;
function CaptureAccess() { access = useAccess(); return null; }
const { UI2AssociationApplyPage } = await import('../src/features/association/UI2AssociationApplyPage.tsx');
const { UI2AssociationsPage } = await import('../src/features/association/UI2AssociationsPage.tsx');
const { ApplicationsReviewPage } = await import('../src/features/support/ApplicationsReviewPage.tsx');
const { validateRelationRead } = await import('../src/api/relationReadSchema.ts');
const { validateApiValue } = await import('../src/api/schema.ts');
const actor = randomUUID(), applicationId = randomUUID();
const declaration = { schema_version: '1.0.0', declared_patient_name: 'SYNTHETIC CHILD LABEL', declared_relationship: 'mother', guardian_scope_acknowledged: true };
const legacy = { evidence_ref: 'SYNTHETIC EVIDENCE', note: null, requested_access: 'read' };
const complete = { ...legacy, applicant_declaration: declaration };
for (const check of [value => validateRelationRead('ApplicationInput', value), value => validateApiValue('protocol.schema.json#/$defs/ApplicationInput', value)]) {
  assert.deepEqual(check(legacy), legacy); assert.deepEqual(check(complete), complete);
  for (const value of [{ ...complete, patient_id: randomUUID() }, { ...complete, applicant_declaration: null },
    ...[false, 1, undefined].map(ack => ({ ...complete, applicant_declaration: { ...declaration, guardian_scope_acknowledged: ack } })),
    { ...complete, applicant_declaration: { ...declaration, declared_patient_name: '   ' } },
    { ...complete, applicant_declaration: { ...declaration, patient_id: randomUUID() } }]) assert.throws(() => check(value));
}
let root, router, state;
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
const shadow = () => document.querySelector('[data-screen-id]')?.shadowRoot;
const input = id => { const node = shadow().querySelector(`#${id}`); assert.ok(node, id); return node; };
async function change(id, value) { await act(async () => Simulate.change(input(id), { target: { value, checked: value } })); await flush(); }
const button = text => [...shadow().querySelectorAll('button')].find(node => node.textContent.includes(text));
async function click(node) { assert.ok(node); await act(async () => Simulate.click(node)); await flush(); }
async function submit() { await act(async () => Simulate.submit(shadow().querySelector('form'))); await flush(); }
function request() {
  if (!state.intent) return null;
  const { status, ...identity } = state.intent;
  return { ...identity, operation_id: 'application_create', target_key: '/api/relation-applications', prepared_at: '2026-10-11T00:00:00Z',
    state: state.receipt ? 'committed' : 'pending', may_retry: false, receipt: state.receipt, error_code: null, application_input: state.payload };
}
function reset(loss = null) {
  state = { calls: [], posts: [], prepares: [], intent: null, payload: null, receipt: null, application: null, loss, mismatch: false };
  globalThis.fetch = async (path, options = {}) => {
    state.calls.push(path);
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (path === '/api/session') { if (state.sessionWait) await state.sessionWait; return Response.json(syntheticSession(actor, null)); }
    if (path === '/api/relations') return Response.json({ relations: [] });
    if (path === '/api/requests') return Response.json({ requests: request() ? [request()] : [] });
    if (path.startsWith('/api/requests/')) return Response.json(request());
    if (path === '/api/relation-applications' && (!options.method || options.method === 'GET')) { const app = state.application; return Response.json({ applications: app ? [app] : [] }); }
    if (path === '/api/write-intents') {
      const body = JSON.parse(options.body); state.prepares.push(body);
      if (state.intent) { assert.equal(body.request_id, state.intent.request_id); assert.deepEqual(body.payload, state.payload); }
      else { state.intent = syntheticIntent(randomUUID(), body.request_id); state.payload = structuredClone(body.payload); }
      return Response.json(state.intent);
    }
    if (path === '/api/relation-applications' && options.method === 'POST') {
      const body = JSON.parse(options.body); state.posts.push({ body, key: options.headers['Idempotency-Key'] });
      assert.deepEqual(body, state.payload); assert.equal(options.headers['Idempotency-Key'], state.intent.request_id);
      if (state.loss === 'before') { state.loss = null; throw new TypeError('SYNTHETIC reply lost before commit'); }
      state.receipt ??= { resource_type: 'relation_application', resource_id: applicationId, version: '1', saved_at: '2026-10-11T00:00:00Z', request_id: state.intent.request_id };
      state.application ??= { application_id: applicationId, version: '1', status: 'pending', patient_id: null, reason: null,
        ...state.payload, applicant_declaration: state.payload.applicant_declaration ?? null,
        declaration_verification: state.payload.applicant_declaration ? 'unverified' : 'not_provided', created_at: '2026-10-11T00:00:00Z' };
      if (state.loss === 'after') { state.loss = null; throw new TypeError('SYNTHETIC reply lost after commit'); }
      return Response.json(state.receipt);
    }
    if (path === `/api/relation-applications/${applicationId}`) return Response.json(state.mismatch
      ? { ...state.application, applicant_declaration: { ...state.application.applicant_declaration, declared_patient_name: 'SYNTHETIC OTHER LABEL' } } : state.application);
    if (path === `/api/support/relation-applications/${applicationId}`) return Response.json({ ...state.application, applicant_id: actor, checked_at: null });
    throw new Error(`Unexpected synthetic request ${path}`);
  };
}
async function mount(page = 'apply') {
  root = createRoot(document.getElementById('root'));
  let element = page === 'list' ? h(UI2AssociationsPage) : page === 'review' ? h(ApplicationsReviewPage, {
    session: { principal_id: actor, capabilities: [{ capability_id: 'decide_relation', state: 'available' }] }, accessState: 'default', accessError: null, onRefresh() {} }) : h(UI2AssociationApplyPage);
  if (page !== 'review') element = h(AccessProvider, null, h(CaptureAccess), element);
  router = createMemoryRouter([{ path: '*', element }], { initialEntries: [page === 'review' ? `/support/applications?application_id=${applicationId}` : '/associations/new'] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
}
async function unmount() { await act(async () => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
async function fill() { await change('application-declared-name', declaration.declared_patient_name); await change('application-declared-relationship', declaration.declared_relationship); await change('application-evidence', legacy.evidence_ref); }
for (const loss of ['before', 'after']) {
  reset(loss); await mount(); assert.equal(input('application-guardian-acknowledgement').checked, false);
  await submit(); assert.equal(state.prepares.length, 0); await fill(); await submit(); assert.equal(state.prepares.length, 0);
  await change('application-guardian-acknowledgement', true); await change('application-permission', 'read_write');
  assert.equal(input('application-guardian-acknowledgement').checked, false); await submit(); assert.equal(state.prepares.length, 0);
  await change('application-permission', 'read'); await change('application-guardian-acknowledgement', true);
  await submit(); assert.equal(state.posts.length, 1); assert.deepEqual(state.payload, complete);
  for (const id of ['application-declared-name','application-declared-relationship','application-evidence','application-guardian-acknowledgement','application-permission']) assert.equal(input(id).disabled, true, id);
  assert.ok(!state.calls.some(path => path.includes('/patients')));
  let resumeSession, refreshing;
  state.sessionWait = new Promise(resolve => { resumeSession = resolve; });
  await act(async () => { refreshing = access.refresh(); }); await flush();
  assert.equal(input('application-declared-name').value, '', 'private declaration hidden during authorization recheck');
  assert.equal(input('application-declared-name').disabled, true);
  await act(async () => { resumeSession(); await refreshing; }); await flush(); state.sessionWait = null;
  assert.equal(input('application-declared-name').value, declaration.declared_patient_name);
  assert.equal(input('application-declared-name').disabled, true, 'recheck must retain original frozen request identity');
  assert.equal(state.posts.length, 1); await unmount(); await mount();
  assert.equal(input('application-guardian-acknowledgement').checked, false, 'new form never inherits recent acknowledgement');
  await click([...shadow().querySelectorAll('button.list-row')].find(node => node.textContent.includes('关联申请')));
  assert.equal(input('application-guardian-acknowledgement').checked, true, 'recovery displays frozen acknowledgement'); assert.equal(input('application-guardian-acknowledgement').disabled, true);
  if (loss === 'before') { await submit(); assert.equal(state.posts.length, 2); } else assert.equal(state.posts.length, 1);
  assert.ok(shadow().textContent.includes('申请已经保存')); assert.ok(shadow().textContent.includes('申请人自述（未经核验）'));
  assert.ok(shadow().textContent.includes(declaration.declared_patient_name)); assert.equal(state.application.patient_id, null);
  for (const post of state.posts) assert.deepEqual(post.body, complete); await unmount();
}
reset(); state.mismatch = true; await mount(); await fill(); await change('application-guardian-acknowledgement', true); await submit();
assert.ok(!shadow().textContent.includes('申请已经保存')); assert.ok(shadow().textContent.includes('重新读取的申请声明')); await unmount(); state.mismatch = false;
await mount('list'); await click(button('待核验'));
assert.ok(shadow().textContent.includes('申请人自述（未经核验）')); assert.ok(shadow().textContent.includes(declaration.declared_patient_name)); await unmount();
await mount('review'); assert.ok(shadow().textContent.includes('申请人自述（未经核验）')); assert.ok(shadow().textContent.includes(declaration.declared_patient_name));
await act(async () => Simulate.change(shadow().querySelector('select'), { target: { value: 'approved' } })); await flush();
const target = [...shadow().querySelectorAll('label')].find(node => node.textContent.includes('核验后的准确患儿标识')).querySelector('input');
assert.equal(target.value, '', 'declared name never becomes a patient identifier'); await unmount();
reset(); state.intent = syntheticIntent(randomUUID(), randomUUID()); state.payload = structuredClone(legacy);
await mount(); await click([...shadow().querySelectorAll('button.list-row')][0]);
assert.equal(input('application-declared-name').value, ''); assert.equal(input('application-guardian-acknowledgement').checked, false);
await submit(); assert.deepEqual(state.posts[0].body, legacy); assert.ok(shadow().textContent.includes('此申请未提供结构化自述')); await unmount();
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks: [
  'legacy and complete contracts; invalid acknowledgement and patient injection refused', 'new form explicitly acknowledges current edited scope',
  'lost response before and after commit recovers frozen payload and request key', 'authorization refresh hides declaration and retains original request lock', 'mismatched declaration read-back never shows success',
  'family detail and reviewer label statements unverified', 'declared name never becomes patient identity',
  'legacy recovery preserves original payload and does not invent declaration',
] }, null, 2));
