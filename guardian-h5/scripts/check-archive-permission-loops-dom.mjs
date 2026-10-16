// Actual archive pages/provider/transport/schema validators; synthetic fetch only.
// No browser, database, filesystem runtime fixture or external service changes.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticIntent, fixtureTime, fixtureDigest } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID, webcrypto } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, next) { return /\.(png|webp|jpg|svg)$/.test(url) ? { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true } : next(url, context); } });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const key of ['window','document','navigator','HTMLElement','HTMLInputElement','HTMLSelectElement','HTMLTextAreaElement','HTMLButtonElement','ShadowRoot','Event']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { ApiError } = await import('../src/api/client.ts');
const { UI2ReadState } = await import('../src/components/UI2ReadState.tsx');
const components = Object.fromEntries(await Promise.all(['PageOriginalPage','UI2ConfirmPage','UI2DocumentCreatePage','UI2BatchPage','UI2DeltaPage','UI2GroupingPage','UI2DuplicatesPage'].map(async name => [name, (await import(`../src/features/archives/${name}.tsx`))[name]])));
const actor = randomUUID(), actor2 = randomUUID(), patient = randomUUID(), patient2 = randomUUID(), pageId = randomUUID(), batchId = randomUUID(), fileId = randomUUID(), documentId = randomUUID(), intentId = randomUUID(), requestId = randomUUID();
const originalPath = `/api/v2/archive-pages/${pageId}/original`, batchPath = `/api/v2/archive-batches/${batchId}`, intentPath = `/api/v2/requests/${intentId}`;
const relation = id => ({ relation_id: randomUUID(), patient_id: id, display_id: 'SYNTHETIC', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, authorization_scope_fingerprint: fixtureDigest, authorization_scope_valid_until: null, actions: ['read_raw','write_record','view_request'] });
const page = () => ({ page_id: pageId, batch_id: batchId, file_page_id: fileId, file_id: fileId, file_object_version: '1', checksum: fixtureDigest, file_status: 'available', version: '1', status_version: '1', status: 'uploaded', task: null, candidate_set_id: null, error_code: null, attempts: [], allowed_actions: [] });
const original = id => ({ patient_id: id, page: page(), ordinal: 1, created_at: '2026-10-11T00:00:00Z', file: { file_id: fileId, status: 'available', checksum: fixtureDigest, size_bytes: 1, media_type: 'image/png', content_path: '/api/synthetic-original' }, use_authorizations: [], purpose_copies: [] });
const batch = id => ({ batch_id: batchId, patient_id: id, version: '1', intake_mode: 'ocr', status: 'receiving', counts: Object.fromEntries(['originals','pages','declared','receiving','uploaded','queued','processing','awaiting_confirmation','confirmed','archived','failed','uncertain','cancel_requested','cancelled','superseded','facts'].map(key => [key, 0])), received_at: '2026-10-11T00:00:00Z', budget_status: 'available', pages: [page()], allowed_actions: [] });
const fields = { batch_id: batchId, page_ids: [pageId], document_type: 'laboratory', organization: 'SYNTHETIC PRIVATE ORGANIZATION', documented_time: null, episode_ids: [], confirmed_text: 'SYNTHETIC PRIVATE DOCUMENT', proposed_metadata: { episodes: [], grouping_note: null } };
const doc = id => ({ document_id: documentId, patient_id: id, version_id: documentId, version: '1', ...fields, grouping_status: 'confirmed', available_at: '2026-10-11T00:00:00Z', allowed_actions: ['archive_document_group'] });
const baseRequest = { intent_id: intentId, request_id: requestId, state: 'failed', may_retry: true, receipt: null, error_code: 'SYNTHETIC', generation: '1', expires_at: fixtureTime, payload_sha256: fixtureDigest };
function request(kind, id = patient) {
  if (kind === 'UI2DeltaPage') return { ...baseRequest, operation_id: 'archive_delta_create', target_key: `/archive-batches/${batchId}/delta-summaries`, delta_input: { baseline_batch_id: null, baseline_document_version_ids: [] } };
  const value = { ...baseRequest, prepared_at: '2026-10-11T00:00:00Z' };
  if (kind === 'UI2BatchPage') return { ...value, operation_id: 'archive_batch_create', target_key: `/patients/${id}/archive-batches`, batch_input: { file_group_ids: [fileId] } };
  if (kind === 'UI2GroupingPage') return { ...value, operation_id: 'archive_document_group', target_key: `/archive-documents/${documentId}/grouping`, document_input: { ...fields, expected_version: '1', reason: 'SYNTHETIC ORIGINAL REASON' } };
  if (kind === 'UI2DocumentCreatePage') return { ...value, operation_id: 'archive_document_create', target_key: `/patients/${id}/archive-documents`, document_input: fields };
  if (kind === 'UI2ConfirmPage') return { ...value, operation_id: 'archive_candidates_confirm', target_key: `/archive-pages/${pageId}/confirm`, fact_input: { candidate_set_id: pageId, expected_page_version: '1', items: [] } };
  return { ...value, operation_id: 'archive_relation_create', target_key: `/patients/${id}/archive-relations`, relation_input: { relation_type: 'same_bytes', left_fact_version_id: null, right_fact_version_id: null, left_file_id: fileId, right_file_id: documentId, status: 'confirmed', evidence_ref: 'SYNTHETIC EVIDENCE', reason: 'SYNTHETIC ORIGINAL REASON' } };
}
const failure = (status = 403) => Response.json({ code: 'SYNTHETIC_ENDPOINT_DENIAL', message: 'Synthetic endpoint permission denied', retryable: false, recovery: 'reauthenticate' }, { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const scenarios = [
  ['PageOriginalPage', `/archives/source?page=${pageId}`, originalPath],
  ['UI2ConfirmPage', `/archives/confirm?page=${pageId}&batch=${batchId}`, originalPath],
  ['UI2DocumentCreatePage', `/archives/grouping?batch=${batchId}`, batchPath],
  ['UI2GroupingPage', `/archives/grouping?document=${documentId}`, `/api/v2/archive-documents/${documentId}`],
  ['UI2GroupingPage', `/archives/grouping?document=${documentId}`, `/api/v2/archive-batches/${batchId}/document-inputs`],
  ['UI2DuplicatesPage', '/archives/duplicates', `/api/v2/patients/${patient}/archive-relations/search`],
  ['UI2BatchPage', '/archives/batches', `/api/v2/patients/${patient}/archive-batches/search`],
  ...['UI2ConfirmPage','UI2DocumentCreatePage','UI2BatchPage','UI2DeltaPage','UI2GroupingPage','UI2DuplicatesPage'].map(name => [name, `/archives/probe?request=${intentId}&batch=${batchId}&document=${documentId}&page=${pageId}`, intentPath]),
];
let root, router, access;
function Capture() { access = useAccess(); return null; }
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
async function run(fn) { let result; await act(async () => { result = await fn(); }); await flush(); return result; }
const screen = () => document.querySelector('[data-screen-id]')?.shadowRoot ?? document;
const text = () => screen().textContent;
const button = label => [...screen().querySelectorAll('button')].find(node => node.textContent === label);
async function click(node) { assert.ok(node, 'expected actionable button'); assert.equal(node.disabled, false); await run(() => Simulate.click(node)); }
function server(kind, deniedPath, status = 403) {
  const queues = new Map();
  const state = { session: syntheticSession(actor, patient), relations: [relation(patient), relation(patient2)], calls: [], deniedPath, status };
  state.count = path => state.calls.filter(call => call.path === path).length;
  state.writes = () => state.calls.filter(call => call.options.method !== 'GET' && !call.path.endsWith('/search'));
  state.enqueue = (path, result) => { const queue = queues.get(path) ?? []; queue.push(result); queues.set(path, queue); };
  state.response = path => {
    const id = state.session?.selected_patient_id ?? patient;
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (path === '/api/session') return state.session ? Response.json(state.session) : failure(401);
    if (path === '/api/relations') return Response.json({ relations: state.relations });
    if (path === originalPath) { const value = original(id); if (kind === 'UI2ConfirmPage') value.page.candidate_set_id = pageId; return Response.json(value); }
    if (path === `/api/v2/archive-pages/${pageId}/candidates`) return Response.json({ candidate_set_id: pageId, page_id: pageId, page_version: '1', model_version: 'synthetic', status: 'awaiting_confirmation', items: [], extraction: null });
    if (path === batchPath) return Response.json(batch(id));
    if (path === `/api/v2/archive-batches/${batchId}/document-inputs`) return Response.json({ batch_id: batchId, patient_id: id, episodes: [] });
    if (path === `/api/v2/archive-pages/${pageId}/facts`) return Response.json({ items: [], next_cursor: null, snapshot_version: fixtureDigest });
    if (path === `/api/v2/archive-documents/${documentId}`) return Response.json(doc(id));
    if (path.endsWith('/archive-batches/search')) return Response.json({ items: [batch(id)], next_cursor: null, snapshot_version: fixtureDigest });
    if (path.endsWith('/archives/search')) return Response.json({ patient_id: id, items: [], next_cursor: null, snapshot_version: fixtureDigest, matched_count: 0, authorized_document_count: 0, elapsed_ms: 1, facets: { organizations: [], episodes: [], unknown_organization_count: 0, unknown_time_count: 0, unknown_episode_count: 0 } });
    if (path.endsWith('/archive-relations/search')) return Response.json({ patient_id: id, sources: [], candidates: [], relations: [], snapshot_version: fixtureDigest, next_cursor: state.directoryCursor ?? null });
    if (path === intentPath) return Response.json(request(kind, id));
    throw new Error(`Unexpected synthetic endpoint ${path}`);
  };
  globalThis.fetch = async (input, options = {}) => {
    const path = new URL(input, 'http://localhost').pathname; state.calls.push({ path, options });
    if (state.calls.length > 100) throw new Error('Synthetic loop safety stop');
    const queued = queues.get(path)?.shift(); if (queued !== undefined) return await queued;
    return path === state.deniedPath ? failure(state.status) : state.response(path);
  };
  return state;
}
async function mount(name, path, element) {
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Capture), h(Outlet)), children: [{ path: '*', element: element ?? h(components[name], name === 'PageOriginalPage' ? { pageId } : {}) }] }], { initialEntries: [path] });
  await run(() => root.render(h(RouterProvider, { router }))); await flush();
}
async function unmount() { await run(() => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
const checks = []; const pass = label => { checks.push(label); console.log('PASS ' + label); };
try {
  for (const [name, path, target] of scenarios) for (const status of [403, 401]) {
    const state = server(name, target, status); await mount(name, path); for (let index = 0; index < 3; index++) await flush();
    assert.equal(state.count(target), 1, `${name} ${target} remains bounded`); assert.equal(state.count('/api/session'), 1);
    assert.match(text(), /Synthetic endpoint permission denied/); assert.equal(state.writes().length, 0);
    assert.ok(!text().includes('SYNTHETIC PRIVATE DOCUMENT'));
    await click(button('重新核验访问权限')); await flush();
    assert.equal(state.count(target), 2, `${name} explicit failed retry reads exactly once`); assert.equal(state.count('/api/session'), 2); assert.equal(state.writes().length, 0);
    state.deniedPath = null; await click(button('重新核验访问权限')); await flush();
    assert.equal(state.count(target), 3, `${name} explicit successful retry reads exactly once`); assert.equal(state.count('/api/session'), 3);
    assert.ok(!text().includes('Synthetic endpoint permission denied')); assert.equal(state.writes().length, 0);
    assert.equal(button('重新读取资料'), undefined, `${name} success must not hide a schema/response failure`); await unmount();
    pass(`${name} ${target === intentPath ? 'original request' : 'initial read'} ${status}: bounded denial, explicit failed/successful retry, no mutation`);
  }
  for (const [name, path, target] of scenarios) {
    const state = server(name, target); await mount(name, path);
    await run(() => access.refresh()); await flush();
    assert.equal(state.count(target), 1, `${name} unrelated same-scope auth refresh cannot replay a denied endpoint`);
    assert.match(text(), /Synthetic endpoint permission denied/); assert.equal(state.writes().length, 0);
    await unmount(); pass(`${name} ${target === intentPath ? 'recovery' : 'read'} denial survives background same-scope revalidation without replay`);
  }
  for (const [name, path, target] of scenarios) {
    const state = server(name, target); await mount(name, path);
    const gate = deferred(); state.enqueue('/api/session', gate.promise);
    const retry = button('重新核验访问权限'); await run(() => { Simulate.click(retry); Simulate.click(retry); });
    assert.equal(state.count('/api/session'), 2); assert.equal(state.count(target), 1);
    await run(() => gate.resolve(Response.json(state.session))); await flush();
    assert.equal(state.count(target), 2); assert.equal(state.writes().length, 0);
    await unmount(); pass(`${name} ${target === intentPath ? 'recovery' : 'read'} duplicate retries share one check and one failed read`);
  }
  for (const [name, path, target] of scenarios) for (const kind of ['account','patient','scope','same-scope']) for (const staleDenial of [false,true]) {
    const state = server(name, null), gate = deferred(); state.enqueue(target, gate.promise);
    const stale = await state.response(target).json();
    await mount(name, path); assert.equal(state.count(target), 1);
    if (kind === 'account') state.session = syntheticSession(actor2, patient);
    if (kind === 'patient') state.session.selected_patient_id = patient2;
    if (kind === 'scope') state.relations[0].authorization_scope_fingerprint = 'b'.repeat(64);
    const authGate = deferred(); state.enqueue('/api/relations', authGate.promise); let checking;
    await run(() => { checking = access.refresh(); }); assert.equal(access.hasCurrentAccess(), false);
    await run(() => authGate.resolve(Response.json({ relations: state.relations }))); await checking; await flush();
    const before = state.calls.length;
    await run(() => gate.resolve(staleDenial ? failure() : Response.json(stale))); await flush();
    assert.equal(state.calls.length, before, `${name} late ${kind} response cannot initiate another request`);
    assert.ok(!text().includes('Synthetic endpoint permission denied')); assert.equal(state.writes().length, 0);
    assert.equal(button('重新读取资料'), undefined, `${name} current result stays valid`);
    await unmount(); pass(`${name} ${target === intentPath ? 'recovery' : 'read'} late ${staleDenial ? 'denial' : 'success'} after ${kind} change is fenced`);
  }
  {
    const target = `/api/v2/patients/${patient}/archive-relations/search`, state = server('UI2DuplicatesPage', null); state.directoryCursor = 'synthetic-next';
    await mount('UI2DuplicatesPage', '/archives/duplicates');
    for (const label of ['关系依据','确认或解除原因']) await run(() => Simulate.change(screen().querySelector(`[aria-label="${label}"]`), { target: { value: 'SYNTHETIC PRIVATE UNSAVED' } }));
    assert.equal(screen().querySelector('[aria-label="关系依据"]').value, 'SYNTHETIC PRIVATE UNSAVED');
    state.deniedPath = target; await click(button('继续读取来源与关系')); await flush();
    for (const label of ['关系依据','确认或解除原因']) { const node = screen().querySelector(`[aria-label="${label}"]`); assert.equal(node.value, ''); assert.equal(node.disabled, true); }
    assert.equal(state.count(target), 2); assert.equal(state.count('/api/session'), 1); assert.equal(state.writes().length, 0);
    await unmount(); pass('duplicates pagination denial clears prior private evidence/reason and disables editing without auth refresh');
  }
  {
    const state = server('UI2GroupingPage', null), gate = deferred(), target = `/api/v2/archive-batches/${batchId}/document-inputs`;
    state.enqueue(target, gate.promise); await mount('UI2GroupingPage', `/archives/grouping?document=${documentId}`);
    assert.ok([...screen().querySelectorAll('textarea')].some(node => node.value === fields.confirmed_text));
    await run(() => gate.resolve(failure()));
    assert.ok(!text().includes(fields.organization)); assert.ok(![...screen().querySelectorAll('input,textarea')].some(node => node.value.includes('SYNTHETIC PRIVATE')));
    assert.equal(state.count('/api/session'), 1); assert.equal(state.writes().length, 0);
    await unmount(); pass('secondary episode-input denial clears already displayed grouping document and form');
  }
  {
    const name = 'UI2BatchPage', target = `/api/v2/patients/${patient}/archive-batches/search`, state = server(name, target);
    // A retained upload marker must not keep the independent uploader active after denial.
    state.enqueue(`/api/v2/patients/${patient}/archive-upload-options`, failure(503));
    await mount(name, `/archives/batches?upload_group=${fileId}`);
    assert.ok(!text().includes('提供新的原件')); assert.equal(state.writes().length, 0);
    await unmount(); pass('batch denial unmounts the independent upload child and clears its editable surface');
  }
  for (const name of ['UI2ConfirmPage','UI2DocumentCreatePage','UI2BatchPage']) {
    const state = server(name, '/api/v2/write-intents'); const recovered = { ...request(name), state: 'pending', may_retry: false };
    state.enqueue(intentPath, Response.json(recovered));
    const path = `/archives/probe?request=${intentId}&batch=${batchId}&page=${pageId}`;
    await mount(name, path); assert.equal(state.count('/api/v2/write-intents'), 1); assert.equal(state.count('/api/session'), 1);
    assert.match(text(), /Synthetic endpoint permission denied/);
    assert.equal(state.writes().length, 1); await flush(); assert.equal(state.writes().length, 1);
    state.enqueue(intentPath, Response.json(recovered));
    await click(button('重新核验访问权限')); await flush();
    assert.equal(state.count('/api/v2/write-intents'), 2); assert.equal(state.count('/api/session'), 2);
    const preparations = state.writes().map(call => JSON.parse(call.options.body));
    assert.equal(preparations[0].request_id, requestId); assert.deepEqual(preparations[0], preparations[1]);
    assert.match(router.state.location.search, new RegExp(intentId));
    await unmount(); pass(`${name} original preparation denial is bounded and explicit retry retains the exact request and payload`);
  }
  const retryLabels = { UI2ConfirmPage: '重新准备原请求', UI2DocumentCreatePage: '重新准备原归组请求', UI2BatchPage: '重新准备原请求', UI2DeltaPage: '重新授权原请求', UI2GroupingPage: '重新授权原请求', UI2DuplicatesPage: '重新授权原请求' };
  for (const name of Object.keys(retryLabels)) for (const kind of ['account','patient','scope','same-scope']) {
    const state = server(name, null), retryPath = `/api/v2/write-intents/${intentId}/retry`, gate = deferred();
    const path = `/archives/probe?request=${intentId}&batch=${batchId}&page=${pageId}&document=${documentId}`;
    await mount(name, path); assert.equal(state.writes().length, 0);
    state.enqueue(retryPath, gate.promise);
    const node = button(retryLabels[name]) ?? [...screen().querySelectorAll('button')].find(node => /重新.*(请求|提交)/.test(node.textContent) && !node.textContent.includes('核验访问'));
    await click(node); assert.equal(state.count(retryPath), 1, `${name} explicit original retry`);
    assert.equal(JSON.parse(state.calls.find(call => call.path === retryPath).options.body).expected_generation, '1');
    if (kind === 'account') state.session = syntheticSession(actor2, patient);
    if (kind === 'patient') state.session.selected_patient_id = patient2;
    if (kind === 'scope') state.relations[0].authorization_scope_fingerprint = 'c'.repeat(64);
    const authGate = deferred(); state.enqueue('/api/relations', authGate.promise); let checking;
    await run(() => { checking = access.refresh(); }); await run(() => authGate.resolve(Response.json({ relations: state.relations }))); await checking; await flush();
    const before = state.calls.length;
    await run(() => gate.resolve(Response.json(syntheticIntent(intentId, requestId, '2')))); await flush();
    assert.equal(state.calls.length, before, `${name} stale retry cannot continue to submit or query a receipt`); assert.equal(state.writes().length, 1);
    await unmount(); pass(`${name} original retry response after ${kind} change cannot submit or adopt a new generation`);
  }
  for (const status of [401,403]) {
    server('PageOriginalPage', null);
    await mount(null, '/probe', h(UI2ReadState, { error: new ApiError(status, 'SYNTHETIC', 'Synthetic endpoint permission denied', false, 'none') }));
    assert.equal(button('重新核验访问权限'), undefined); await click(button('核验访问权限'));
    assert.equal(router.state.location.pathname, '/associations'); await unmount();
    pass(`shared permission panel ${status} without a retry callback retains its safe navigation fallback`);
  }
} finally { if (root && document.getElementById('root').childNodes.length) await unmount(); }
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks }, null, 2));
