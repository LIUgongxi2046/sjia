// Synthetic, nonvisual contract/adoption tests against production adapters/hooks.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent, fixtureTime } from './response-test-fixtures.mjs';
const { fetchJson, ApiError } = await import('../src/api/client.ts');
const api = await import('../src/api/endpoints.ts');
const contract = await import('../src/api/legacyResponseSchema.ts');
const { createRecordSubmission } = await import('../src/api/recordSubmission.ts');
const patient = randomUUID(), otherPatient = randomUUID(), actor = randomUUID(), record = randomUUID(), versionId = randomUUID();
const validReceipt = { patient_id: patient, record_id: record, record_version_id: versionId, version: 1 };
const checks = [];
const pass = name => checks.push(name);
const respond = value => { globalThis.fetch = async () => Response.json(value); };
for (const value of [null, [], 'forged', true, 1]) {
  respond(value); await assert.rejects(fetchJson('/api/test'), /JSON 对象/);
}
globalThis.fetch = async () => new Response('<html>proxy failure</html>', { headers: { 'Content-Type': 'text/html' } });
await assert.rejects(fetchJson('/api/test'), /JSON/);
pass('transport rejects null, arrays, primitives and non-JSON success envelopes');
for (const body of [null, [], { code: {}, message: ['forged'] }, { code: 'X', message: 'forged', retryable: 'yes' },
  { code: 'X', message: 'forged', recovery: 'delete' }, { code: 'X', message: 'forged', field_errors: [{ path: 'x', code: [] }] }]) {
  globalThis.fetch = async () => Response.json(body, { status: 409 });
  await assert.rejects(fetchJson('/api/test'), error => error instanceof ApiError && error.code === 'HTTP_409'
    && error.retryable === false && error.recovery === 'none' && error.fieldErrors.length === 0 && !error.message.includes('forged'));
}
globalThis.fetch = async () => Response.json({ code: 'INPUT_INVALID', message: 'synthetic input', retryable: false,
  recovery: 'review_input', field_errors: [{ path: '', code: 'required' }] }, { status: 422 });
await assert.rejects(fetchJson('/api/test'), error => error instanceof ApiError && error.code === 'INPUT_INVALID'
  && error.recovery === 'review_input' && error.fieldErrors[0].message === 'required');
pass('malformed errors cannot inject retry actions; valid legacy field errors normalize missing messages');
const cancellation = new AbortController();
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => { cancellation.abort(); return {}; } });
await assert.rejects(fetchJson('/api/test', { signal: cancellation.signal }), error => error.name === 'AbortError');
pass('abort between response and JSON adoption rejects the stale payload');
respond(syntheticBootstrap()); assert.equal((await api.bootstrap()).schema_version, '1.0.0');
for (const payload of [{ csrf_token: 'only-partial' }, { ...syntheticBootstrap(), csrf_token: {} },
  { ...syntheticBootstrap(), return_paths: ['https://attacker.invalid/'] }, { ...syntheticBootstrap(), schema_version: 'future' }]) {
  respond(payload); await assert.rejects(api.bootstrap());
}
respond(syntheticSession(actor, patient)); assert.equal((await api.readSession()).selected_patient_id, patient);
for (const payload of [{ ...syntheticSession(actor, patient), principal_id: 'not-uuid' },
  { ...syntheticSession(actor, patient), capabilities: [{}] }, { ...syntheticSession(actor, patient), account_status: 'disabled' },
  { ...syntheticSession(actor, patient), expires_at: 'tomorrow' }, { ...syntheticSession(actor, patient), selected_patient_id: 'wrong-id' }]) {
  respond(payload); await assert.rejects(api.readSession());
}
respond({ ...syntheticSession(actor, null), audience: 'support' });
await assert.rejects(api.login('synthetic', 'synthetic')); await assert.rejects(api.register('synthetic', 'synthetic'));
respond({ selected_patient_id: otherPatient }); await assert.rejects(api.savePatientSelection(patient));
respond({ logged_out: true }); await assert.rejects(api.logout());
pass('bootstrap/session/login/register/logout and patient-selection reject wrong shape and identity');
const config = syntheticFieldConfig(patient, 'observation', [{ field_code: 'temp', label: 'synthetic', value_type: 'decimal', writable: true, allowed_values: [] }]);
respond(config); assert.equal((await api.readFieldConfig(patient, 'observation')).patient_id, patient);
for (const payload of [{ ...config, patient_id: otherPatient }, { ...config, kind: 'therapy' },
  { ...config, fields: [{ ...config.fields[0], writable: 'true' }] }, { ...config, checksum: 'not-a-digest' },
  { ...config, fields: [config.fields[0], config.fields[0]] }, { ...config, watch_field_codes: ['unauthorized'] },
  { ...config, state: 'read_only' }]) {
  respond(payload); await assert.rejects(api.readFieldConfig(patient, 'observation'));
}
await assert.rejects(contract.validateLegacyResource(`/api/patients/${patient}/field-config?kind=observation&kind=therapy`, config));
await assert.rejects(contract.validateLegacyResource('/api/unregistered', {}));
pass('active field configuration rejects cross-patient/kind/version, invalid types, duplicate and out-of-scope fields');
const draft = { draft_id: randomUUID(), patient_id: patient, form_type: 'observation', version: '1', status: 'editing', saved_at: fixtureTime, payload: { note: 'synthetic' } };
respond(draft); await api.readDraft(draft.draft_id); await api.createDraft(patient, 'observation', draft.payload);
respond({ ...draft, draft_id: randomUUID() }); await assert.rejects(api.readDraft(draft.draft_id));
respond({ ...draft, patient_id: otherPatient }); await assert.rejects(api.createDraft(patient, 'observation', draft.payload));
respond({ ...draft, version: '4' }); await assert.rejects(api.updateDraft(draft.draft_id, '1', draft.payload));
respond({ ...draft, version: '2' }); await api.updateDraft(draft.draft_id, '1', draft.payload);
respond({ ...draft, version: '9007199254740994' }); await api.updateDraft(draft.draft_id, '9007199254740993', draft.payload);
respond({ ...draft, payload: [] }); await assert.rejects(api.readDraft(draft.draft_id));
respond({ ...draft, version: '2', patient_id: otherPatient }); await assert.rejects(api.updateDraft(draft.draft_id, '1', draft.payload, undefined, { patientId: patient, formType: 'observation' }));
respond({ ...draft, version: '2', form_type: 'therapy' }); await assert.rejects(api.updateDraft(draft.draft_id, '1', draft.payload, undefined, { patientId: patient, formType: 'observation' }));
pass('draft adapters require valid payload envelope and exact object/patient/form/incremented version without numeric precision loss');
const recordDetail = { ...validReceipt, kind: 'therapy', origin_type: 'self_report', capture_method: 'manual', quality_status: 'pending',
  source_organization: null, correction_reason: null, corrected_at: null, recorded_at: fixtureTime, report: null, fields: [], attachments: [], fact_refs: [] };
const recordItem = { record_id: record, kind: 'therapy', status: 'active', current_version: 1, created_at: fixtureTime, detail: recordDetail };
respond({ records: [recordItem], next_cursor: null }); assert.equal((await api.searchRecords(patient, { kind: 'therapy', limit: 1 })).records.length, 1);
for (const item of [{ ...recordItem, detail: { ...recordDetail, patient_id: otherPatient } }, { ...recordItem, record_id: randomUUID() },
  { ...recordItem, current_version: 2 }, { ...recordItem, kind: 'observation' }, { ...recordItem, detail: { ...recordDetail, kind: 'observation' } }]) {
  respond({ records: [item], next_cursor: null }); await assert.rejects(api.searchRecords(patient, { kind: 'therapy', limit: 1 }));
}
pass('shared record search used by RecentMaterials rejects cross-patient, wrong kind, mismatched record and current version');
for (const invalid of [{ ...validReceipt, patient_id: otherPatient }, { ...validReceipt, record_id: 'invalid' },
  { ...validReceipt, record_version_id: 'invalid' }, { ...validReceipt, version: '1' }, { ...validReceipt, version: 2 },
  { ...validReceipt, extra: 'unregistered' }, { record_id: record, record_version_id: versionId, version: 1 }]) {
  const intentId = randomUUID(); let receipt = invalid, posts = 0, requestId;
  globalThis.fetch = async (path, options) => {
    const body = JSON.parse(options.body);
    if (path === '/api/write-intents') { requestId ??= body.request_id; assert.equal(body.request_id, requestId); return Response.json(syntheticIntent(intentId, requestId)); }
    posts++; assert.equal(options.headers['Idempotency-Key'], requestId); return Response.json(receipt);
  };
  const submission = createRecordSubmission(patient, { kind: 'observation', fields: [{}] });
  await assert.rejects(submission.submit()); assert.equal(submission.uncertain, true);
  receipt = validReceipt; assert.deepEqual(await submission.submit(), validReceipt); await submission.submit(); assert.equal(posts, 2);
}
pass('malformed/cross-patient/string-version creation receipts remain uncertain and uncached; validated retry reuses one key');
for (const wrongIntent of ['uuid', 'request', 'generation', 'digest']) {
  let posts = 0;
  globalThis.fetch = async (path, options) => {
    if (path !== '/api/write-intents') { posts++; return Response.json(validReceipt); }
    const body = JSON.parse(options.body), intent = syntheticIntent(randomUUID(), body.request_id);
    if (wrongIntent === 'uuid') intent.intent_id = 'bad';
    if (wrongIntent === 'request') intent.request_id = randomUUID();
    if (wrongIntent === 'generation') intent.generation = 1;
    if (wrongIntent === 'digest') intent.payload_sha256 = 'bad';
    return Response.json(intent);
  };
  const submission = createRecordSubmission(patient, { kind: 'observation', fields: [{}] });
  await assert.rejects(submission.submit()); assert.equal(submission.uncertain, true); assert.equal(posts, 0);
}
pass('invalid intent ID/request/generation/digest cannot advance to a record POST');
const request = { patient_id: patient, intent_id: randomUUID(), request_id: randomUUID(), operation_id: 'record_create',
  target_key: `/api/patients/${patient}/records`, status: 'committed', generation: '1', prepared_at: fixtureTime, expires_at: fixtureTime,
  resource_id: record, receipt: validReceipt, may_retry: false, record_input: { kind: 'observation', fields: [{}] } };
const scope = { patientId: patient, operation: 'record_create', target: request.target_key, kind: 'observation', intentId: request.intent_id };
assert.deepEqual(contract.validateRecordRequest(request, scope), request);
for (const payload of [{ ...request, intent_id: randomUUID() }, { ...request, patient_id: otherPatient },
  { ...request, resource_id: randomUUID() }, { ...request, may_retry: true }, { ...request, receipt: null },
  { ...request, receipt: { ...validReceipt, version: 3 } }, { ...request, status: 'prepared' },
  { ...request, record_input: { kind: 'therapy', fields: [{}] } }]) assert.throws(() => contract.validateRecordRequest(payload, scope));
assert.throws(() => contract.validateRecordRequests({ patient_id: patient, requests: [request, request] }, scope));
pass('recovery verifies patient, intent, operation, target, form, receipt/resource and committed state before adoption');
// DOM: the actual legacy hook must never expose malformed configuration data.
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'https://localhost/' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLButtonElement', 'Event'])
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { useResource } = await import('../src/api/useResource.ts');
const { AccessProvider } = await import('../src/state/access.tsx');
const { useRecordSubmission } = await import('../src/state/useRecordSubmission.ts');
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 5)); });
let result, root;
function ResourceProbe() { result = useResource(`/api/patients/${patient}/field-config?kind=observation`); return null; }
respond({ ...config, patient_id: otherPatient }); root = createRoot(document.getElementById('root'));
await act(async () => root.render(h(ResourceProbe))); await flush(); assert.equal(result.data, null); assert.ok(result.error); assert.equal(result.loading, false);
respond(config); await act(async () => result.reload()); await flush(); assert.deepEqual(result.data, config); assert.equal(result.error, null);
await act(async () => root.unmount());
pass('actual legacy React hook rejects malformed configuration and successfully reloads a valid one');
let markedSubmitted = 0;
function RecoveryProbe() { result = useRecordSubmission({ beginSubmission: () => ({}), cancelSubmission: () => {}, markSubmitted: () => { markedSubmitted++; } }, 'observation'); return null; }
for (const receipt of [{ ...validReceipt, patient_id: otherPatient }, { ...validReceipt, version: '1' }, validReceipt]) {
  globalThis.fetch = async path => {
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (path === '/api/session') return Response.json(syntheticSession(actor, patient));
    if (path === '/api/relations') return Response.json({ relations: [{ relation_id: patient, patient_id: patient, display_id: 'synthetic', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'write_record'] }] });
    if (path === `/api/requests/${request.intent_id}`) return Response.json({ ...request, receipt });
    throw new Error(`Unexpected synthetic request ${path}`);
  };
  root = createRoot(document.getElementById('root'));
  const router = createMemoryRouter([{ path: '/form', element: h(AccessProvider, null, h(RecoveryProbe)) }],
    { initialEntries: [`/form?record_intent=${request.intent_id}&record_intent_patient=${patient}&record_intent_actor=${actor}`] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
  if (receipt === validReceipt) { assert.equal(result.recoveryError, null); await act(async () => { await result.recover(result.recoveries[0]); }); assert.equal(markedSubmitted, 1); }
  else { assert.equal(result.pending, true); assert.ok(result.recoveryError); assert.equal(result.recoveries.length, 0); assert.equal(markedSubmitted, 0); }
  await act(async () => root.unmount()); router.dispose();
}
pass('actual recovery hook retains lock on invalid committed receipts and marks submitted only after exact validated receipt');
dom.window.close();
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_CONTRACT_AND_DOM', checks }, null, 2));
