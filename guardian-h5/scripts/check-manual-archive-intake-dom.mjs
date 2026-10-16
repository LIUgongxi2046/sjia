// Production archive components, API adapters, schema validation and receipts.
// Only the access fixture, uploader UI and transport are synthetic. No server,
// browser, database, provider, credentials or patient information are used.
import './registration-test-loader.mjs';
import { syntheticIntent, fixtureDigest, fixtureTime } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, next) {
  if (url.endsWith('/state/access.tsx')) return { format: 'module', source: 'export function useAccess(){return globalThis.__manualArchiveAccess;}', shortCircuit: true };
  if (url.endsWith('/features/archives/ArchiveUpload.tsx')) return { format: 'module', source: 'export function ArchiveUpload(){return null;}', shortCircuit: true };
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return next(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'HTMLTextAreaElement', 'ShadowRoot']) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { UI2BatchPage } = await import('../src/features/archives/UI2BatchPage.tsx');
const { UI2DocumentCreatePage } = await import('../src/features/archives/UI2DocumentCreatePage.tsx');
const { validateApiValue } = await import('../src/api/schema.ts');
const patient = randomUUID(), groupId = randomUUID(), batchId = randomUUID(), pageId = randomUUID(), fileId = randomUUID(), documentId = randomUUID();
const now = '2026-10-12T00:00:00Z';
globalThis.__manualArchiveAccess = { principalId: randomUUID(), patientId: patient, loading: false, error: null, scopeVersion: 1, hasCurrentAccess: () => true, refresh: async () => {}, relations: [{ patient_id: patient, status: 'active', actions: ['read_raw', 'write_record'], display_id: 'SYNTHETIC' }] };
const page = { page_id: pageId, batch_id: batchId, file_page_id: fileId, file_id: fileId, version: '1', status_version: '1', file_object_version: '1', checksum: fixtureDigest, file_status: 'available', status: 'uploaded', task: null, candidate_set_id: null, error_code: null, attempts: [], allowed_actions: [] };
const counts = Object.fromEntries(['originals', 'pages', 'declared', 'receiving', 'uploaded', 'queued', 'processing', 'awaiting_confirmation', 'confirmed', 'archived', 'failed', 'uncertain', 'cancel_requested', 'cancelled', 'superseded', 'facts'].map(key => [key, ['originals', 'pages', 'uploaded'].includes(key) ? 1 : 0]));
const baseBatch = { batch_id: batchId, patient_id: patient, intake_mode: 'manual', version: '1', status: 'receiving', counts, received_at: now, budget_status: 'unknown', pages: [page], allowed_actions: [] };
const schema = name => `v2-protocol.schema.json#/$defs/${name}`;
validateApiValue(schema('BatchInput'), { file_group_ids: [groupId] });
validateApiValue(schema('BatchInput'), { file_group_ids: [groupId], intake_mode: 'manual' });
assert.throws(() => validateApiValue(schema('BatchInput'), { file_group_ids: [groupId], intake_mode: 'synthetic_invalid' }));
validateApiValue(schema('BatchView'), baseBatch);
const { intake_mode: _omitted, ...missingMode } = baseBatch;
assert.throws(() => validateApiValue(schema('BatchView'), missingMode));

function server({ mode = 'manual', loseBatch = false, loseDocument = false, existing = false, forbiddenActions = false } = {}) {
  let batch = existing ? structuredClone({ ...baseBatch, intake_mode: mode }) : null, documentValue = null;
  if (forbiddenActions) { batch.allowed_actions = ['archive_batch_process']; batch.pages[0].allowed_actions = ['archive_page_retry', 'archive_page_cancel']; }
  const identities = new Map(), committed = new Map(), writes = [], reads = [];
  globalThis.fetch = async (url, options = {}) => {
    const path = new URL(url, 'http://localhost').pathname;
    const body = options.body ? JSON.parse(options.body) : null;
    reads.push(path);
    if (path.endsWith('/archive-batches/search')) return Response.json({ items: batch ? [batch] : [], next_cursor: null, snapshot_version: fixtureDigest });
    if (path.endsWith('/archive-batch-inputs')) return Response.json({ patient_id: patient, items: [{ file_group_id: groupId, version: '1', page_count: 1, file_ids: [fileId], created_at: now }] });
    if (path === `/api/v2/archive-batches/${batchId}`) return Response.json(batch);
    if (path.endsWith('/document-inputs')) return Response.json({ patient_id: patient, batch_id: batchId, episodes: [] });
    if (path === '/api/v2/write-intents') {
      let entry = [...identities.values()].find(item => item.identity.request_id === body.request_id);
      if (entry) assert.deepEqual(entry.identity, body);
      else { entry = { id: randomUUID(), identity: body }; identities.set(entry.id, entry); }
      return Response.json(syntheticIntent(entry.id, body.request_id));
    }
    if (path === `/api/v2/patients/${patient}/archive-batches` || path === `/api/v2/patients/${patient}/archive-documents`) {
      const entry = identities.get(options.headers['X-Write-Intent']); assert.ok(entry);
      assert.equal(options.headers['Idempotency-Key'], entry.identity.request_id);
      assert.deepEqual(body, entry.identity.payload); writes.push({ path, body, request: entry.identity.request_id });
      const isBatch = path.endsWith('/archive-batches');
      const receipt = { schema_version: '2.0.0', resource_type: isBatch ? 'archive_batch' : 'archive_document', resource_id: isBatch ? batchId : documentId, version: '1', saved_at: now, request_id: entry.identity.request_id, task_id: null };
      committed.set(entry.id, receipt);
      if (isBatch) batch = structuredClone({ ...baseBatch, intake_mode: body.intake_mode ?? 'ocr' });
      else documentValue = { document_id: documentId, patient_id: patient, version_id: documentId, version: '1', ...body, grouping_status: 'pending', available_at: now, allowed_actions: [] };
      if (isBatch && loseBatch) { loseBatch = false; throw new TypeError('Synthetic lost batch response'); }
      if (!isBatch && loseDocument) { loseDocument = false; throw new TypeError('Synthetic lost document response'); }
      return Response.json(receipt);
    }
    if (path.startsWith('/api/v2/requests/')) {
      const identifier = path.split('/').at(-1), entry = identities.get(identifier) ?? [...identities.values()].find(item => item.identity.request_id === identifier);
      assert.ok(entry); const identity = entry.identity, receipt = committed.get(entry.id) ?? null;
      return Response.json({ intent_id: entry.id, request_id: identity.request_id, operation_id: identity.operation_id, target_key: identity.target_key, prepared_at: now, state: receipt ? 'committed' : 'pending', may_retry: false, receipt, error_code: null, generation: '1', expires_at: fixtureTime, payload_sha256: fixtureDigest, [identity.operation_id === 'archive_batch_create' ? 'batch_input' : 'document_input']: identity.payload });
    }
    if (path === `/api/v2/archive-documents/${documentId}`) return Response.json(documentValue);
    throw new Error(`Unexpected request ${path}`);
  };
  return { writes, reads, get batch() { return batch; }, get document() { return documentValue; } };
}
let root, router;
const screen = () => document.querySelector('[data-screen-id]').shadowRoot;
const button = label => [...screen().querySelectorAll('button')].find(node => node.textContent === label);
const field = label => [...screen().querySelectorAll('label')].find(node => node.querySelector('span')?.textContent === label)?.querySelector('input,textarea,select');
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
async function click(node) { assert.ok(node); assert.equal(node.disabled, false); await act(async () => Simulate.click(node)); await flush(); await flush(); }
async function change(node, value) { assert.ok(node); await act(async () => Simulate.change(node, { target: { value, checked: value === true } })); await flush(); }
async function mount(path) {
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ path: '/archives/batches', element: h(UI2BatchPage) }, { path: '/archives/batches/:batchId', element: h(UI2BatchPage) }, { path: '/archives/grouping', element: h(UI2DocumentCreatePage) }], { initialEntries: [path] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
}
async function unmount() { await act(async () => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
for (const mode of ['manual', 'ocr']) {
  const state = server({ loseBatch: mode === 'manual' }); await mount('/archives/batches');
  await click(button('选择已有资料')); assert.equal(field('整理方式').value, 'manual');
  await change(field('整理方式'), mode); await change(screen().querySelector('input[type="checkbox"]'), true);
  await act(async () => { Simulate.click(button('接收所选资料')); Simulate.click(button('接收所选资料')); }); await flush(); await flush();
  if (mode === 'manual') { assert.ok(button('查询原请求')); await click(button('查询原请求')); }
  assert.equal(state.writes.length, 1); assert.equal(state.writes[0].body.intake_mode, mode);
  assert.equal(router.state.location.pathname, `/archives/batches/${batchId}`);
  if (mode === 'manual') {
    assert.ok(screen().textContent.includes('未使用自动识别'));
    assert.ok(screen().querySelector(`a[href="/archives/grouping?batch=${batchId}"]`));
    for (const label of ['处理已接收页面', '重试本页', '取消本页', '重试失败页面', '取消未开始页面']) assert.equal(button(label), undefined);
    assert.equal(screen().querySelector('a[href*="/archives/confirm"]'), null);
  }
  await unmount();
}
{
  const state = server({ existing: true, forbiddenActions: true }); await mount(`/archives/batches/${batchId}`);
  for (const label of ['处理已接收页面', '重试本页', '取消本页']) assert.equal(button(label), undefined);
  assert.equal(state.writes.length, 0); await unmount();
}
for (const loseDocument of [false, true]) {
  const state = server({ existing: true, loseDocument }); await mount(`/archives/grouping?batch=${batchId}`);
  assert.equal(button('确认归组').disabled, true);
  await change(screen().querySelector('input[type="checkbox"]'), true);
  await change(field('就诊类型'), 'inpatient');
  await change(field('就诊 1 说明'), 'SYNTHETIC family supplied visit description');
  await change(field('本人确认原文'), 'SYNTHETIC transcribed original');
  await click(button('添加另一次就诊说明'));
  await change(field('就诊 2 说明'), 'SYNTHETIC second visit description');
  await act(async () => { Simulate.click(button('确认归组')); Simulate.click(button('确认归组')); }); await flush(); await flush();
  if (loseDocument) { assert.ok(screen().textContent.includes('原请求结果待核验')); await click(button('查询原归组请求')); }
  assert.equal(state.writes.length, 1); assert.ok(screen().textContent.includes('文书归组已经保存'));
  assert.ok(screen().textContent.includes('待专业核对')); assert.equal(state.document.grouping_status, 'pending');
  assert.deepEqual(state.document.episode_ids, []); assert.equal(state.document.proposed_metadata.episodes.length, 2);
  assert.equal(state.document.proposed_metadata.episodes[0].description, 'SYNTHETIC family supplied visit description');
  assert.equal(state.document.confirmed_text, 'SYNTHETIC transcribed original');
  assert.deepEqual(state.batch.pages[0], page); assert.equal(state.batch.status, 'receiving');
  assert.ok(!state.reads.some(path => /candidates|process|retry|cancel|clinical/.test(path))); await unmount();
}
dom.window.close();
console.log('PASS manual archive intake: strict schemas; actual batch/document UI and API adapters; explicit modes; duplicate clicks; lost-receipt recovery without duplicate writes; no OCR actions/candidate reads; family-supplied episodes remain pending; original page state unchanged');
