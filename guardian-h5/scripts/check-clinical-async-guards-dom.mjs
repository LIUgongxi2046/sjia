// Actual production hooks/components, synthetic deferred APIs only. No browser,
// microphone, external provider, real patient, database or persistence fixtures.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID, webcrypto } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/register/observations' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'HTMLButtonElement', 'Event']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act, useState } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { ObservationsPage } = await import('../src/features/registration/ObservationsPage.tsx');
const { ReportsPage } = await import('../src/features/registration/ReportsPage.tsx');
const { TherapiesPage } = await import('../src/features/registration/TherapiesPage.tsx');
const { VoiceEntry } = await import('../src/components/VoiceEntry.tsx');
const { UploadedPhotos } = await import('../src/components/UploadedPhotos.tsx');
const { fetchJson } = await import('../src/api/client.ts');
const { uploadFile, recognizeReport, recognizeTherapy } = await import('../src/api/endpoints.ts');
const actor = randomUUID(), patient = randomUUID(), patient2 = randomUUID(), authority = randomUUID(), source = randomUUID(), groupId = randomUUID(), fileId = randomUUID(), taskId = randomUUID(), intentId = randomUUID();
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const flush = async () => act(async () => { await new Promise(done => setTimeout(done, 5)); });
async function run(fn) { let result; await act(async () => { result = await fn(); }); await flush(); return result; }
const failure = (status = 503) => Response.json({ code: 'SYNTHETIC_FAILURE', message: 'Synthetic authorization failure', retryable: false, recovery: 'none' }, { status });
const relation = id => ({ authorization_scope_fingerprint: 'a'.repeat(64), authorization_scope_valid_until: null, relation_id: randomUUID(), patient_id: id, display_id: 'SYNTHETIC', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'write_record', 'manage_draft', 'read_file'] });
const voiceOptions = id => ({ patient_id: id, state: 'available', reason_code: null, field_config_id: randomUUID(), provider_config_id: randomUUID(), source_options: [{ source_revision_id: source, family_authorization_id: authority, external_authorization_id: randomUUID() }], original_options: [], input_modes: ['LOCAL_AUDIO_CONFIRMED_TEXT', 'SIMULATION_TEXT_ONLY'] });
const uploadOptions = id => ({ patient_id: id, runtime_config_id: randomUUID(), runtime_checksum: 'a'.repeat(64), max_upload_bytes: 20000000, max_pixels: 40000000, max_pages: 10, media_types: ['image/png'], parser_version: 'synthetic', authorizations: [{ authorization_id: authority, source_revision_id: source, source_label: 'SYNTHETIC', evidence_ref: 'synthetic', approved_at: '2020-01-01T00:00:00Z', valid_until: null }] });
const group = (status = 'uploaded') => ({ group_id: groupId, patient_id: patient, authorization_id: authority, source_revision_id: source, status, version: '1', files: [{ file_id: fileId, ordinal: 1, purpose: 'original', media_type: 'image/png', checksum: 'a'.repeat(64), size_bytes: 12, page_count: 1 }] });
const task = status => ({ task_id: taskId, task_type: 'file_validation', request_id: groupId, accepted_at: '2026-10-11T00:00:00Z', status, status_version: status === 'completed' ? '2' : '1', poll_after_ms: status === 'completed' ? null : 1, allowed_actions: [], result_type: status === 'completed' ? 'file_group' : null, result_ref: status === 'completed' ? groupId : null, error_code: null });
let access, root, router, photos, busy, applied, recognized, probeRecognition;
function Capture() { access = useAccess(); return null; }
function PhotoProbe() {
  const [value, setValue] = useState([]); photos = value;
  return h(UploadedPhotos, { photos: value, onChange: setValue, onBusyChange: value => { busy = value; }, onUploaded: async () => { recognized++; return probeRecognition ? await probeRecognition : 1; } });
}
const voiceProps = { structured: true, onApply: (...value) => { applied.push(value); }, onBusyChange: value => { busy = value; } };
function server() {
  const queues = new Map();
  const state = { session: syntheticSession(actor, patient, 'csrf-synthetic'), relations: [relation(patient), relation(patient2)], calls: [], groupStatus: 'uploaded' };
  state.enqueue = (path, result) => { const values = queues.get(path) ?? []; values.push(result); queues.set(path, values); };
  state.count = path => state.calls.filter(value => value.path === path).length;
  state.mutations = () => state.calls.filter(value => value.options.method && value.options.method !== 'GET');
  state.response = (path, options = {}) => {
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap('csrf-bootstrap'));
    if (path === '/api/session') return state.session ? Response.json(state.session) : failure(401);
    if (path === '/api/relations') return Response.json({ relations: state.relations });
    if (path === '/api/session/logout') { state.session = null; return Response.json({}); }
    if (path === '/api/patient-selection') { state.session.selected_patient_id = JSON.parse(options.body).patient_id; return Response.json({ selected_patient_id: state.session.selected_patient_id }); }
    if (path.endsWith('/field-config')) { const url = new URL(options.originalPath, 'http://localhost'); return Response.json(syntheticFieldConfig(url.pathname.split('/')[3], url.searchParams.get('kind'), [{ field_code: 'rash', label: 'SYNTHETIC_CURRENT_FIELD', value_type: 'categorical', writable: true, allowed_values: ['yes', 'no'] }, { field_code: 'medication_name', label: 'SYNTHETIC MEDICATION', value_type: 'text', writable: true, allowed_values: [] }])); }
    if (path.endsWith('/requests')) return Response.json({ patient_id: path.split('/')[3], requests: [] });
    if (path.endsWith('/voice-input-options')) return Response.json(voiceOptions(path.split('/')[4]));
    if (path.endsWith('/archive-upload-options')) return Response.json(uploadOptions(path.split('/')[4]));
    if (path === `/api/patients/${patient}/voice-transcription`) return Response.json({ text: 'SYNTHETIC TRANSCRIPT', transcription_evidence_id: randomUUID() });
    if (path === `/api/patients/${patient}/uploads`) return Response.json({ group_id: groupId, version: '1', status: 'initiated' });
    if (path.startsWith(`/api/uploads/${groupId}/files/`)) return Response.json({ file_id: fileId, checksum: 'a'.repeat(64), size_bytes: 12 });
    if (path === `/api/uploads/${groupId}`) return Response.json(group(state.groupStatus));
    if (path === '/api/write-intents') return Response.json(syntheticIntent(intentId, groupId));
    if (path === `/api/uploads/${groupId}/complete`) return Response.json(task('accepted'));
    if (path === `/api/tasks/${taskId}`) return Response.json(task('completed'));
    if (path === '/api/recognition/therapy-ocr') return Response.json({ candidates: { items: [{ name: 'SYNTHETIC LATE MEDICINE', source_text: 'SYNTHETIC LATE SOURCE' }] } });
    if (path === '/api/recognition/report-ocr') return Response.json({ candidates: { items: [{ name: 'SYNTHETIC LATE LAB', value: '1', comparator: '=', unit: 'mg', reference: null, method: null, source_text: 'SYNTHETIC LATE SOURCE' }] } });
    throw new Error(`Unexpected synthetic request ${path}`);
  };
  globalThis.fetch = async (path, options = {}) => {
    const parsed = new URL(path, 'http://localhost');
    const actual = parsed.pathname.includes('/files/') ? `/api/uploads/${groupId}/files/upload` : parsed.pathname;
    state.calls.push({ path: actual, options });
    const queued = queues.get(actual)?.shift(); if (queued !== undefined) return await queued;
    return state.response(parsed.pathname, { ...options, originalPath: path });
  };
  globalThis.createImageBitmap = async () => ({ width: 2, height: 2, close() {} });
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true });
  probeRecognition = null; recognized = 0; applied = []; busy = false; photos = [];
  return state;
}
async function mount(component, path = '/probe') {
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Capture), h(Outlet)), children: [ { path: '*', element: component } ] }], { initialEntries: [path] });
  await run(() => root.render(h(RouterProvider, { router }))); await flush();
}
async function unmount() { await run(() => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
function screen() { return document.querySelector('[data-screen-id]')?.shadowRoot ?? document; }
const click = async node => { assert.ok(node); await run(() => Simulate.click(node)); };
const change = async (node, value) => { assert.ok(node); await run(() => Simulate.change(node, { target: { value } })); };
const file = () => new File(['synthetic'], 'synthetic.png', { type: 'image/png' });
async function upload(files = [file()]) { await run(() => Simulate.change(screen().querySelector('[aria-label="选择原始照片"]'), { target: { files, value: '' } })); }
async function audio() { await change(screen().querySelector('[aria-label="语音用途授权来源"]'), source); await run(() => Simulate.change(screen().querySelector('[aria-label="选择语音录音"]'), { target: { files: [new File(['synthetic'], 'synthetic.wav', { type: 'audio/wav' })], value: '' } })); }
async function suspend(state) {
  const gate = deferred(); state.enqueue('/api/relations', gate.promise); let pending;
  await run(() => { pending = access.refresh(); }); assert.equal(access.loading, true); assert.equal(access.hasCurrentAccess(), false);
  return { async resume() { await run(() => gate.resolve(Response.json({ relations: state.relations }))); await pending; }, async fail() { await run(() => gate.resolve(failure())); await pending; } };
}
const checks = []; function pass(text) { checks.push(text); console.log(`PASS ${text}`); }
{
  server(); assert.equal((await recognizeReport(fileId)).candidates.items[0].name, 'SYNTHETIC LATE LAB');
  assert.equal((await recognizeTherapy(fileId)).candidates.items[0].name, 'SYNTHETIC LATE MEDICINE');
  pass('OCR deferred fixtures satisfy the actual report and therapy response schemas');
}
// Direct transport gaps are checked without a React effect or provider rerender.
{
  server(); let current = true, sends = 0; globalThis.fetch = async () => { sends++; return Response.json({}); };
  const pending = fetchJson('/synthetic', { isCurrent: () => current }); current = false;
  await assert.rejects(pending, error => error.name === 'AbortError'); assert.equal(sends, 0);
  pass('fetchJson live guard closes its own pre-fetch microtask gap');
}
{
  server(); let current = true, sends = 0; const digest = deferred();
  Object.defineProperty(globalThis, 'crypto', { value: { randomUUID, subtle: { digest: () => digest.promise } }, configurable: true });
  globalThis.fetch = async () => { sends++; return Response.json({}); };
  const pending = uploadFile(groupId, randomUUID(), new ArrayBuffer(12), 'image/png', { isCurrent: () => current });
  current = false; digest.resolve(new ArrayBuffer(32)); await assert.rejects(pending, error => error.name === 'AbortError'); assert.equal(sends, 0);
  pass('uploadFile live guard closes its internal SHA-256 await before PUT');
}
{
  const state = server(), gate = deferred(); state.enqueue(`/api/patients/${patient}/field-config`, gate.promise);
  await mount(h(ObservationsPage)); const recovery = await suspend(state);
  const old = syntheticFieldConfig(patient, 'observation', [{ field_code: 'rash', label: 'SYNTHETIC_STALE_CONFIG', value_type: 'categorical', writable: true, allowed_values: ['yes'] }]);
  await run(() => gate.resolve(Response.json(old))); assert.ok(!document.body.textContent.includes('SYNTHETIC_STALE_CONFIG'));
  await recovery.resume(); assert.ok(document.body.textContent.includes('SYNTHETIC_CURRENT_FIELD')); assert.equal(state.count(`/api/patients/${patient}/field-config`), 2);
  await unmount(); pass('actual ObservationsPage discards suspended field config and re-reads safely on same-scope resume');
}
for (const boundary of ['options', 'response', 'json']) {
  const state = server(), gate = deferred();
  if (boundary === 'options') state.enqueue(`/api/v2/patients/${patient}/voice-input-options`, gate.promise);
  else state.enqueue(`/api/patients/${patient}/voice-transcription`, boundary === 'response' ? gate.promise : { ok: true, json: () => gate.promise });
  await mount(h(VoiceEntry, voiceProps)); if (boundary !== 'options') await audio();
  const recovery = await suspend(state), posts = state.count(`/api/patients/${patient}/voice-transcription`);
  await run(() => gate.resolve(boundary === 'options' ? Response.json(voiceOptions(patient)) : boundary === 'response' ? Response.json({ text: 'SYNTHETIC STALE VOICE', transcription_evidence_id: randomUUID() }) : { text: 'SYNTHETIC STALE VOICE', transcription_evidence_id: randomUUID() }));
  assert.ok(!document.body.textContent.includes('SYNTHETIC STALE VOICE')); assert.equal(screen().querySelector('textarea')?.value ?? '', ''); assert.equal(applied.length, 0);
  await recovery.resume(); assert.equal(state.count(`/api/patients/${patient}/voice-transcription`), posts); assert.equal(busy, false);
  await audio(); assert.equal(screen().querySelector('textarea').value, 'SYNTHETIC TRANSCRIPT');
  await unmount(); pass(`VoiceEntry suspension at ${boundary} discards stale result, stops busy state and never auto-replays audio`);
}
for (const changeScope of ['logout', 'patient', 'fingerprint', 'error', 'expiry']) {
  const state = server(), gate = deferred(); state.enqueue(`/api/patients/${patient}/voice-transcription`, gate.promise);
  await mount(h(VoiceEntry, voiceProps)); await audio();
  if (changeScope === 'logout') await run(() => access.logout());
  if (changeScope === 'patient') await run(() => access.selectPatient(patient2));
  if (changeScope === 'fingerprint') { state.relations[0].authorization_scope_fingerprint = 'b'.repeat(64); await run(() => access.refresh()); }
  if (changeScope === 'error') { const recovery = await suspend(state); await recovery.fail(); }
  if (changeScope === 'expiry') { state.session.expires_at = '2020-01-01T00:00:00Z'; await run(() => access.refresh()); }
  await run(() => gate.resolve(Response.json({ text: 'SYNTHETIC OLD SCOPE', transcription_evidence_id: randomUUID() })));
  assert.equal(screen().querySelector('textarea')?.value ?? '', ''); assert.equal(applied.length, 0);
  assert.equal(state.count(`/api/patients/${patient}/voice-transcription`), 1);
  await unmount(); pass(`VoiceEntry ${changeScope} fences old-scope transcription and apply`);
}
for (const boundary of ['options', 'decode', 'bytes', 'create', 'upload', 'group', 'intent', 'complete', 'poll', 'recognition']) {
  const state = server(), gate = deferred(); let selected = file();
  const paths = { options: `/api/v2/patients/${patient}/archive-upload-options`, create: `/api/patients/${patient}/uploads`, upload: `/api/uploads/${groupId}/files/upload`, group: `/api/uploads/${groupId}`, intent: '/api/write-intents', complete: `/api/uploads/${groupId}/complete`, poll: `/api/tasks/${taskId}` };
  if (paths[boundary]) state.enqueue(paths[boundary], gate.promise);
  if (boundary === 'decode') globalThis.createImageBitmap = () => gate.promise;
  if (boundary === 'bytes') selected = { name: 'synthetic.png', type: 'image/png', size: 12, arrayBuffer: () => gate.promise };
  if (boundary === 'recognition') probeRecognition = gate.promise;
  await mount(h(PhotoProbe)); if (boundary !== 'options') await upload([selected]);
  if (boundary === 'poll') await flush();
  const recovery = await suspend(state), mutations = state.mutations().length, recognitionCalls = recognized;
  const results = { options: () => Response.json(uploadOptions(patient)), decode: () => ({ width: 2, height: 2, close() {} }), bytes: () => new ArrayBuffer(12), create: () => Response.json({ group_id: groupId, version: '1', status: 'initiated' }), upload: () => Response.json({ file_id: fileId, checksum: 'a'.repeat(64), size_bytes: 12 }), group: () => Response.json(group()), intent: () => Response.json(syntheticIntent(intentId, groupId)), complete: () => Response.json(task('accepted')), poll: () => Response.json(task('completed')), recognition: () => 99 };
  await run(() => gate.resolve(results[boundary]())); assert.equal(state.mutations().length, mutations); assert.equal(recognized, recognitionCalls);
  assert.ok(photos.every(value => value.recognized_count !== 99));
  state.groupStatus = 'available'; await recovery.resume(); assert.equal(state.mutations().length, mutations); assert.equal(recognized, recognitionCalls); assert.equal(busy, false);
  if (['group', 'intent', 'complete', 'poll'].includes(boundary)) { assert.equal(photos.length, 1); assert.equal(photos[0].file_id, fileId); assert.equal(photos[0].recognized_count, undefined); }
  await unmount(); pass(`UploadedPhotos suspension at ${boundary}: no later mutation/adoption or automatic provider replay; known complete group is status-read only`);
}
for (const [component, path, recognitionPath, label] of [[ReportsPage, '/register/reports', '/api/recognition/report-ocr', 'SYNTHETIC LATE LAB'], [TherapiesPage, '/register/therapies', '/api/recognition/therapy-ocr', 'SYNTHETIC LATE MEDICINE']]) {
  const state = server(), gate = deferred(); state.enqueue(recognitionPath, gate.promise); await mount(h(component), path); await upload(); await flush();
  assert.equal(state.count(recognitionPath), 1); const recovery = await suspend(state);
  await run(() => gate.resolve(state.response(recognitionPath))); await recovery.resume();
  if (component === ReportsPage) await run(() => router.navigate('/register/reports/confirm'));
  assert.ok(!screen().textContent.includes(label)); assert.equal(state.count(recognitionPath), 1);
  await unmount(); pass(`${component.name} actual parent OCR callback cannot adopt candidates after suspension or auto-replay provider`);
}
{
  const state = server(), gate = deferred(); state.enqueue(`/api/patients/${patient}/voice-transcription`, gate.promise);
  await mount(h(VoiceEntry, voiceProps)); await audio(); const recovery = await suspend(state); await recovery.resume();
  await run(() => gate.resolve(Response.json({ text: 'SYNTHETIC LATE AFTER RESUME', transcription_evidence_id: randomUUID() })));
  assert.equal(screen().querySelector('textarea')?.value ?? '', ''); assert.equal(state.count(`/api/patients/${patient}/voice-transcription`), 1);
  await unmount(); pass('VoiceEntry old response arriving after same-scope resume remains invalid');
}
{
  const state = server(), gate = deferred(); probeRecognition = gate.promise;
  await mount(h(PhotoProbe)); await upload([file(), file()]); await flush(); assert.equal(recognized, 1);
  const recovery = await suspend(state), mutations = state.mutations().length; await recovery.resume();
  await run(() => gate.resolve(99)); assert.equal(state.mutations().length, mutations); assert.equal(recognized, 1);
  assert.equal(photos.length, 1); assert.equal(photos[0].recognized_count, undefined);
  await unmount(); pass('late OCR after same-scope resume cannot adopt its count or start a second queued file');
}
for (const changeScope of ['logout', 'patient', 'fingerprint', 'error', 'expiry', 'unmount']) {
  const state = server(), gate = deferred(); state.enqueue(`/api/patients/${patient}/uploads`, gate.promise);
  await mount(h(PhotoProbe)); await upload();
  if (changeScope === 'logout') await run(() => access.logout());
  if (changeScope === 'patient') await run(() => access.selectPatient(patient2));
  if (changeScope === 'fingerprint') { state.relations[0].authorization_scope_fingerprint = 'b'.repeat(64); await run(() => access.refresh()); }
  if (changeScope === 'error') { const recovery = await suspend(state); await recovery.fail(); }
  if (changeScope === 'expiry') { state.session.expires_at = '2020-01-01T00:00:00Z'; await run(() => access.refresh()); }
  if (changeScope === 'unmount') await unmount();
  const mutations = state.mutations().length;
  await run(() => gate.resolve(Response.json({ group_id: groupId, version: '1', status: 'initiated' })));
  assert.equal(state.mutations().length, mutations); assert.equal(recognized, 0); assert.equal(photos.length, 0);
  if (changeScope !== 'unmount') await unmount(); pass(`UploadedPhotos ${changeScope} fences old group creation before uploading bytes`);
}
{
  const state = server(), gate = deferred(); let stopped = 0, constructed = 0;
  Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true });
  Object.defineProperty(navigator, 'mediaDevices', { value: { getUserMedia: () => gate.promise }, configurable: true });
  globalThis.MediaRecorder = class { constructor() { constructed++; } };
  await mount(h(VoiceEntry, voiceProps)); await change(screen().querySelector('[aria-label="语音用途授权来源"]'), source);
  await click([...screen().querySelectorAll('button')].find(node => node.textContent.includes('开始语音输入')));
  const recovery = await suspend(state); await run(() => gate.resolve({ getTracks: () => [{ stop: () => { stopped++; } }] }));
  assert.equal(stopped, 1); assert.equal(constructed, 0); assert.equal(state.count(`/api/patients/${patient}/voice-transcription`), 0);
  await recovery.resume(); await unmount(); delete globalThis.MediaRecorder;
  pass('mocked deferred microphone permission stops its stream on suspension before constructing a recorder or sending audio');
}
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_ACTUAL_COMPONENT_ASYNC_GUARDS', checks }, null, 2));
