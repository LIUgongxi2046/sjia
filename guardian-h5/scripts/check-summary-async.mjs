// Production polling/receipt contract tests; no browser, network or patient data.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
const { waitForSummaryTask, cancelSummaryTask } = await import('../src/api/summaries.ts');
const { validateSummary } = await import('../src/api/summarySchema.ts');

const id = '11111111-1111-4111-8111-111111111111';
const request = '22222222-2222-4222-8222-222222222222';
const initial = { task_id: id, task_type: 'summary', request_id: request,
  accepted_at: '2026-10-11T00:00:00Z', status: 'accepted', status_version: '1', poll_after_ms: 1,
  allowed_actions: ['task_cancel'], result_type: null, result_ref: null, error_code: null };
const originalFetch = globalThis.fetch;
const originalTimeout = globalThis.setTimeout;
let calls = [];
globalThis.setTimeout = (callback, _delay, ...args) => originalTimeout(callback, 0, ...args);
try {
  const responses = [
    { ...initial, status: 'running', status_version: '2' },
    { ...initial, status: 'completed', status_version: '3', poll_after_ms: null,
      allowed_actions: [], result_type: 'summary', result_ref: id },
  ];
  globalThis.fetch = async (path, options) => { calls.push([path, options]); return Response.json(responses.shift()); };
  const states = [];
  const result = await waitForSummaryTask(initial, request, new AbortController().signal, task => states.push(task.status));
  assert.equal(result.status, 'completed');
  assert.deepEqual(states, ['accepted', 'running', 'completed']);
  assert.equal(calls.length, 2);

  calls = [];
  const cancelled = { ...initial, status: 'cancelled', poll_after_ms: null, allowed_actions: [] };
  assert.equal((await waitForSummaryTask(cancelled, request, new AbortController().signal, () => {})).status, 'cancelled');
  assert.equal(calls.length, 0);

  const aborted = new AbortController();
  await assert.rejects(waitForSummaryTask(initial, request, aborted.signal, () => aborted.abort()), { name: 'AbortError' });
  assert.equal(calls.length, 0);

  await assert.rejects(waitForSummaryTask({ ...initial, request_id: id }, request, new AbortController().signal, () => {}), /原请求/);
  globalThis.fetch = async () => Response.json({ ...initial, status_version: '1' });
  await assert.rejects(waitForSummaryTask({ ...initial, status_version: '2' }, request, new AbortController().signal, () => {}), /倒退/);

  globalThis.fetch = async (path, options) => { calls.push([path, options]); return Response.json({}); };
  await cancelSummaryTask(id, 'synthetic-csrf', new AbortController().signal);
  assert.equal(calls.at(-1)[0], `/api/tasks/${id}/cancel`);
  assert.equal(calls.at(-1)[1].headers['X-CSRFToken'], 'synthetic-csrf');

  const scope = { from_date: '2026-09-01', to_date: '2026-09-30', scope_timezone: 'Asia/Shanghai',
    include_unknown_time: false, record_kinds: ['observation'], include_source_status: true };
  for (const resource_type of ['task', 'summary']) {
    validateSummary('SummaryRequestView', { patient_id: id, intent_id: id, request_id: request,
      operation_id: 'summary_create', target_key: `/api/patients/${id}/summaries`,
      prepared_at: initial.accepted_at, state: 'committed', may_retry: false,
      receipt: { resource_type, resource_id: id, version: '1', saved_at: initial.accepted_at, request_id: request },
      error_code: null, generation: '1', expires_at: initial.accepted_at, payload_sha256: 'a'.repeat(64), summary_input: scope });
  }
  console.log('PASS: 7 async-summary polling, abort, identity, monotonic-state, cancel and receipt-contract groups');
} finally {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalTimeout;
}
