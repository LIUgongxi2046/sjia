// Real production viewers, original-source hook, AccessProvider and transports.
// Synthetic in-memory deferred fetch only; no server, database, browser or writes.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, fixtureDigest } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { randomUUID, webcrypto, createHash } from 'node:crypto';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/source', pretendToBeVisual: true });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLButtonElement', 'HTMLDialogElement', 'Element', 'Event', 'Node', 'ShadowRoot', 'MutationObserver']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
globalThis.getComputedStyle = window.getComputedStyle.bind(window);
globalThis.requestAnimationFrame = window.requestAnimationFrame.bind(window);
globalThis.cancelAnimationFrame = window.cancelAnimationFrame.bind(window);
window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
HTMLElement.prototype.getClientRects = function () { return [{ x: 0, y: 0, width: 100, height: 30 }]; };
HTMLDialogElement.prototype.showModal = function () { this.open = true; };
HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')); };
const activeUrls = new Set(), createdUrls = [], revokedUrls = [];
URL.createObjectURL = () => { const url = `blob:synthetic-${randomUUID()}`; activeUrls.add(url); createdUrls.push(url); return url; };
URL.revokeObjectURL = url => { activeUrls.delete(url); revokedUrls.push(url); };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act, useState } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { UI2Frame } = await import('../src/components/UI2Frame.tsx');
const { PrivateSourceGallery } = await import('../src/components/PrivateSourceGallery.tsx');
const { PrivateDocumentSource } = await import('../src/components/PrivateDocumentSource.tsx');
const { ArchiveOriginals } = await import('../src/features/archives/ArchiveOriginals.tsx');
const { useOriginalSource } = await import('../src/api/useOriginalSource.ts');
const { validateOriginalSource } = await import('../src/api/originalSourceSchema.ts');
const actor = randomUUID(), actor2 = randomUUID(), patient = randomUUID(), patient2 = randomUUID();
const fileId = randomUUID(), fileId2 = randomUUID(), pageId = randomUUID(), recordId = randomUUID(), versionId = randomUUID(), versionId2 = randomUUID();
const body = 'SYNTHETIC PRIVATE ORIGINAL';
const checksum = createHash('sha256').update(body).digest('hex');
const pathFor = (kind, revised = false) => kind === 'metadata' ? `/api/record-versions/${revised ? versionId2 : versionId}/originals` : `/api/files/${revised ? fileId2 : fileId}/content`;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const failure = (status = 403) => Response.json({ code: 'SYNTHETIC_SOURCE_DENIED', message: 'Synthetic source access denied', retryable: false, recovery: 'reauthenticate' }, { status });
const relation = id => ({ relation_id: randomUUID(), patient_id: id, display_id: 'SYNTHETIC', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, authorization_scope_fingerprint: fixtureDigest, authorization_scope_valid_until: null, actions: ['read_raw', 'read_file'] });
const metadata = (id = patient, label = body, revised = false) => ({ patient_id: id, record_id: recordId, record_version_id: revised ? versionId2 : versionId, record_version: 1, source_organization: label, originals: [{ file_id: revised ? fileId2 : fileId, ordinal: 1, checksum, media_type: 'text/plain', object_version: '1', status: 'available', created_at: '2026-10-11T00:00:00Z', pages: [], documents: [], use_authorizations: [], purpose_copies: [] }] });
validateOriginalSource(metadata());
let access, root, router, setRevised, mountedKind, multiple = false;
function Capture() { access = useAccess(); return null; }
function MetadataProbe({ revised }) {
  const current = useAccess();
  const resource = useOriginalSource(current.loading ? null : { patient_id: current.patientId, record_id: recordId, record_version_id: revised ? versionId2 : versionId, version: 1, attachments: [{ file_id: revised ? fileId2 : fileId, ordinal: 1 }] });
  return h('section', null, resource.loading && h('p', { role: 'status' }, '正在读取获准原件'), resource.error && h('p', { role: 'alert' }, resource.error.message), resource.data && h('pre', null, resource.data.source_organization), h('button', { onClick: () => void resource.reload() }, '重新读取原件'));
}
function Probe({ kind }) {
  const [revised, change] = useState(false); setRevised = change;
  const file = { file_id: revised ? fileId2 : fileId, checksum, media_type: 'text/plain', content_path: pathFor(kind, revised) };
  const child = kind === 'gallery' ? h(PrivateSourceGallery, { files: [{ ...file, ordinal: 1 }, ...(multiple ? [{ ...file, file_id: fileId2, content_path: pathFor(kind, true), ordinal: 2 }] : [])] })
    : kind === 'document' ? h(PrivateDocumentSource, { file })
    : kind === 'archive' ? h(ArchiveOriginals, { pages: [{ ...file, page_id: pageId, page_order: 1, object_version: '1', status: 'available' }, ...(multiple ? [{ ...file, file_id: fileId2, page_id: fileId2, page_order: 2, object_version: '1', status: 'available' }] : [])] })
    : h(MetadataProbe, { revised });
  return h(UI2Frame, { title: 'Synthetic originals', screenId: 'SOURCE-TEST', note: '', onBack() {} }, child);
}
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
async function run(fn) { let result; await act(async () => { result = await fn(); }); await flush(); return result; }
const screen = () => document.querySelector('[data-screen-id]')?.shadowRoot ?? document;
const text = () => screen().textContent;
const button = label => [...screen().querySelectorAll('button')].find(node => node.textContent.includes(label));
const click = async node => { assert.ok(node, 'expected button'); assert.equal(node.disabled, false); await run(() => Simulate.click(node)); };
async function open() { if (mountedKind !== 'metadata') await click(button(mountedKind === 'gallery' ? '查看原始资料' : '查看原件')); }
function server(kind) {
  const queues = new Map();
  const state = { session: syntheticSession(actor, patient), relations: [relation(patient), relation(patient2)], calls: [], denied: null };
  state.enqueue = (path, value) => { const queue = queues.get(path) ?? []; queue.push(value); queues.set(path, queue); };
  state.count = path => state.calls.filter(call => call.path === path).length;
  state.writes = () => state.calls.filter(call => call.options.method && call.options.method !== 'GET');
  state.success = (revised = false, label = body) => kind === 'metadata' ? Response.json(metadata(state.session.selected_patient_id, label, revised)) : new Response(body, { headers: { 'Content-Type': kind === 'gallery' ? 'image/png' : 'text/plain' } });
  globalThis.fetch = async (input, options = {}) => {
    const path = new URL(input, 'http://localhost').pathname;
    state.calls.push({ path, options });
    if (state.calls.length > 60) throw new Error('Synthetic request-loop safety stop');
    const queued = queues.get(path)?.shift(); if (queued !== undefined) return await queued;
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (path === '/api/session') return state.session ? Response.json(state.session) : failure(401);
    if (path === '/api/relations') return Response.json({ relations: state.relations });
    if (path === pathFor(kind) || path === pathFor(kind, true)) return state.denied ? failure(state.denied) : state.success(path === pathFor(kind, true));
    throw new Error(`Unexpected synthetic request ${input}`);
  };
  return state;
}
async function mount(kind) {
  mountedKind = kind; root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Capture), h(Outlet)), children: [{ path: '*', element: h(Probe, { kind }) }] }], { initialEntries: ['/source'] });
  await run(() => root.render(h(RouterProvider, { router }))); await flush(); await open();
}
async function unmount() { await run(() => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); assert.equal(activeUrls.size, 0, 'all private object URLs revoked'); }
const closed = () => { assert.equal(activeUrls.size, 0); assert.equal(screen().querySelector('pre'), null); assert.equal(screen().querySelector('iframe'), null); assert.equal(document.querySelector('.source-gallery-dialog'), null); };
const success = kind => { if (kind === 'gallery') { assert.ok(document.querySelector('.source-gallery-dialog')); assert.equal(activeUrls.size, 1); } else { assert.match(text(), /SYNTHETIC PRIVATE ORIGINAL/); if (kind !== 'metadata') assert.equal(activeUrls.size, 1); } };
async function suspend(state) {
  const gate = deferred(); state.enqueue('/api/relations', gate.promise); let pending;
  await run(() => { pending = access.refresh(); }); assert.equal(access.loading, true); assert.equal(access.hasCurrentAccess(), false); closed();
  return async () => { await run(() => gate.resolve(Response.json({ relations: state.relations }))); await pending; await flush(); };
}
const checks = []; function pass(label) { checks.push(label); console.log(`PASS ${label}`); }
try {
  for (const kind of ['gallery', 'document', 'archive', 'metadata']) {
    for (const status of [403, 401]) {
      const state = server(kind); state.denied = status; await mount(kind);
      for (let index = 0; index < 4; index++) await flush();
      assert.equal(state.count(pathFor(kind)), 1, `${kind}: persistent ${status} bounded`); assert.equal(state.count('/api/session'), 1); closed(); assert.match(text(), /Synthetic source access denied/);
      await run(() => access.refresh()); assert.equal(state.count(pathFor(kind)), 1, 'background same-scope refresh does not retry denial'); assert.match(text(), /Synthetic source access denied/);
      await click(button('重新读取原件')); assert.equal(state.count(pathFor(kind)), 2); assert.equal(state.count('/api/session'), 3); closed(); assert.match(text(), /Synthetic source access denied/);
      state.denied = null; await click(button('重新读取原件')); assert.equal(state.count(pathFor(kind)), 3); assert.equal(state.count('/api/session'), 4); success(kind);
      assert.equal(state.writes().length, 0); await unmount(); pass(`${kind}: persistent ${status}, background refresh and explicit failed/successful revalidation are bounded and read-only`);
    }
    {
      const state = server(kind); state.denied = 403; await mount(kind);
      await run(() => { Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true }); document.dispatchEvent(new Event('visibilitychange')); }); closed();
      await run(() => { Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true }); document.dispatchEvent(new Event('visibilitychange')); });
      if (['document', 'archive'].includes(kind)) await open();
      assert.equal(state.count(pathFor(kind)), 1); assert.equal(state.count('/api/session'), 2); assert.match(text(), /Synthetic source access denied/); assert.equal(state.writes().length, 0);
      await unmount(); pass(`${kind}: hidden/visible and sheet reopen retain the denial without source replay`);
    }
    if (kind !== 'metadata') for (const boundary of ['blob', 'bytes', 'digest']) {
      const state = server(kind), gate = deferred();
      if (boundary === 'digest') {
        let first = true;
        Object.defineProperty(globalThis, 'crypto', { value: { randomUUID, subtle: { digest: (...args) => { if (first) { first = false; return gate.promise; } return webcrypto.subtle.digest(...args); } } }, configurable: true });
      } else {
        const blob = boundary === 'bytes' ? { arrayBuffer: () => gate.promise } : new Blob([body]);
        state.enqueue(pathFor(kind), { ok: true, headers: new Headers({ 'Content-Type': kind === 'gallery' ? 'image/png' : 'text/plain' }), blob: () => boundary === 'blob' ? gate.promise : Promise.resolve(blob) });
      }
      await mount(kind); const resume = await suspend(state); await resume(); await flush(); const created = createdUrls.length;
      const result = boundary === 'blob' ? new Blob([body]) : boundary === 'bytes' ? new TextEncoder().encode(body).buffer : await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(body));
      await run(() => gate.resolve(result)); assert.equal(createdUrls.length, created); success(kind); assert.equal(state.count(pathFor(kind)), 2); assert.equal(state.writes().length, 0);
      await unmount(); Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true }); pass(`${kind}: deferred ${boundary} cannot allocate or adopt private content after same-scope resume`);
    }
    for (const failedStage of ['session', 'relations']) {
      const state = server(kind); state.denied = 403; await mount(kind); state.enqueue(`/api/${failedStage}`, failure(failedStage === 'session' ? 401 : 503));
      await click(button('重新读取原件')); assert.equal(state.count(pathFor(kind)), 1); assert.equal(access.hasCurrentAccess(), false); assert.equal(state.writes().length, 0); closed();
      await unmount(); pass(`${kind}: failed ${failedStage} revalidation cannot issue another source read`);
    }
    {
      const state = server(kind); state.denied = 403; await mount(kind); const gate = deferred(); state.enqueue('/api/session', gate.promise); const retry = button('重新读取原件');
      await run(() => { Simulate.click(retry); Simulate.click(retry); }); assert.equal(state.count('/api/session'), 2); assert.equal(state.count(pathFor(kind)), 1);
      state.denied = null; await run(() => gate.resolve(Response.json(state.session))); assert.equal(state.count(pathFor(kind)), 2); success(kind); assert.equal(state.writes().length, 0);
      await unmount(); pass(`${kind}: repeated retry clicks share one revalidation and source read`);
    }
    for (const change of ['account', 'patient', 'scope', 'same-scope']) {
      for (const lateFailure of [false, true]) {
        const state = server(kind), gate = deferred(); state.enqueue(pathFor(kind), gate.promise); await mount(kind);
        const originalScope = access.scopeVersion;
        if (change === 'same-scope') { const resume = await suspend(state); await resume(); assert.equal(access.scopeVersion, originalScope); }
        else {
          if (change === 'account') state.session = syntheticSession(actor2, patient);
          if (change === 'patient') state.session.selected_patient_id = patient2;
          if (change === 'scope') state.relations[0].authorization_scope_fingerprint = 'b'.repeat(64);
          await run(() => access.refresh()); assert.notEqual(access.scopeVersion, originalScope); await open();
        }
        await flush(); const created = createdUrls.length;
        await run(() => gate.resolve(lateFailure ? failure() : state.success(false, 'SYNTHETIC STALE METADATA')));
        assert.equal(createdUrls.length, created, 'stale response cannot allocate an object URL'); assert.ok(!text().includes('SYNTHETIC STALE METADATA')); assert.ok(!text().includes('Synthetic source access denied'));
        success(kind); assert.equal(state.count('/api/session'), 2); assert.equal(state.writes().length, 0); await unmount();
        pass(`${kind}: late ${lateFailure ? 'denial' : 'success'} after ${change} cannot adopt data, allocate URLs or refresh`);
      }
    }
    {
      const state = server(kind); await mount(kind); success(kind); state.denied = 403; const resume = await suspend(state); await resume();
      closed(); assert.match(text(), /Synthetic source access denied/); assert.equal(state.count(pathFor(kind)), 2); assert.equal(state.count('/api/session'), 2);
      await flush(); assert.equal(state.count(pathFor(kind)), 2); assert.equal(state.writes().length, 0); await unmount(); pass(`${kind}: fresh denial clears previously rendered private content and revokes URLs`);
    }
    {
      const state = server(kind), gate = deferred(); state.enqueue(pathFor(kind), gate.promise); await mount(kind); await run(() => setRevised(true)); await flush();
      assert.equal(state.count(pathFor(kind, true)), 1); const created = createdUrls.length; await run(() => gate.resolve(state.success(false, 'SYNTHETIC STALE METADATA')));
      assert.equal(createdUrls.length, created); assert.ok(!text().includes('SYNTHETIC STALE METADATA')); success(kind); assert.equal(state.writes().length, 0); await unmount(); pass(`${kind}: source/version changes fence late response adoption`);
    }
  }
  for (const kind of ['gallery', 'archive']) {
    multiple = true; const state = server(kind), gate = deferred(); state.enqueue(pathFor(kind, true), gate.promise); await mount(kind);
    assert.equal(activeUrls.size, 1, 'one successful part has a temporary URL');
    await run(() => gate.resolve(failure())); closed(); assert.match(text(), /Synthetic source access denied/);
    assert.equal(state.count('/api/session'), 1); await flush(); assert.equal(state.count(pathFor(kind)), 1); assert.equal(state.count(pathFor(kind, true)), 1); assert.equal(state.writes().length, 0);
    await unmount(); multiple = false; pass(`${kind}: partial multi-file success is revoked on another file denial with no replay`);
  }
} finally { if (root?._internalRoot) await unmount(); dom.window.close(); }
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_REAL_PRIVATE_SOURCE_COMPONENTS', checks }, null, 2));
