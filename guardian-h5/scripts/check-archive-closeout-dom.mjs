// Actual React components with synthetic values. No browser/visual or patient data.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent, fixtureTime } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/', pretendToBeVisual: true });
for (const name of ['window', 'document', 'navigator', 'Node', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'HTMLButtonElement', 'HTMLAnchorElement', 'Event']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act, useState } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { FactFieldsEditor, editableTime } = await import('../src/features/archives/FactFieldsEditor.tsx');
const { CommonFactTime, applyCommonTime } = await import('../src/features/archives/CommonFactTime.tsx');
const { timePayload, emptyTime, isTimeInputValid } = await import('../src/components/medicalTimeValue.ts');
const { RecordFieldEditor, recordTimeInput } = await import('../src/components/RecordFieldEditor.tsx');
const { validateApiValue } = await import('../src/api/schema.ts');
const checks = [];
const pass = text => { checks.push(text); console.log('PASS ' + text); };
const base = { precision: 'unknown', raw_text: '合成时间原文', local_year: null, local_month: null,
  end_year: null, end_month: null, relative_text: null, local_date: null, local_datetime: null,
  timezone_name: null, utc_offset_minutes: null, instant: null, resolution_status: 'local_only' };
const examples = [
  { ...base, precision: 'second', local_datetime: '2026-10-10T12:01:02', timezone_name: 'America/New_York', utc_offset_minutes: -240 },
  { ...base, precision: 'minute', local_datetime: '2026-10-10T12:01:00', utc_offset_minutes: 330 },
  { ...base, precision: 'year', local_year: 2024 },
  { ...base, precision: 'month', local_year: 2024, local_month: 2 },
  { ...base, precision: 'month_range', local_year: 2024, local_month: 2, end_year: 2024, end_month: 5 },
  { ...base, precision: 'relative', relative_text: '住院前三天' },
  { ...base, precision: 'date', local_date: '2026-10-10', timezone_name: 'Europe/Berlin', utc_offset_minutes: 120 },
  { ...base, precision: 'unknown', resolution_status: 'unknown' },
];
for (const example of examples) assert.deepEqual(timePayload(editableTime(example)), example);
pass('all eight stored precisions preserve raw text, timezone and fixed offset without invented dates');
for (const example of examples) {
  const { preserved, ...input } = recordTimeInput({ medical_time: example });
  assert.deepEqual(preserved, example);
  validateApiValue('family-record.schema.json#/$defs/CorrectionDraft/properties/timeInputs/additionalProperties', JSON.parse(JSON.stringify(input)));
}
pass('record-correction adapter and strict draft contract support all retained precisions and offsets');

function decision(time = null, raw = null, origin = time ? 'item' : 'unknown') {
  return { decision: 'pending', reason: '', field: { field_code: 'report_item', original_name: '合成项目',
    medical_time: time, sample_time_origin: origin, value_type: 'decimal', raw_numeric: '1', raw_text: '1',
    value_state: 'known', raw_unit: 'x', qualifier: 'eq', method: null, reference_interval: null },
    context: { medical_time: time, documented_time: null, context_type: 'current', assertion_state: 'present',
      time_origin: origin, raw_time_text: raw ?? time?.raw_text ?? null, episode_id: null },
    time: editableTime(time), documented: { ...emptyTime } };
}
async function mount(element) {
  const root = createRoot(document.getElementById('root'));
  await act(async () => root.render(element));
  return async () => act(async () => root.unmount());
}
function input(label) { return document.querySelector(`[aria-label="${label}"]`) || [...document.querySelectorAll('label')].find(node => node.querySelector('span')?.textContent === label)?.querySelector('input,textarea,select'); }
async function change(node, value) {
  assert.ok(node); if (node.type === 'checkbox') node.checked = value; else node.value = value;
  await act(async () => Simulate.change(node, { target: node, currentTarget: node }));
}
let current;
function EditorHarness({ initial }) {
  const [value, setValue] = useState(initial); current = value;
  return h(FactFieldsEditor, { value, onChange: setValue, disabled: false, sourceLabel: 'synthetic source' });
}
for (const example of examples) {
  const cleanup = await mount(h(EditorHarness, { initial: decision(example) }));
  assert.equal(input('时间精度').value, example.precision);
  if (['minute', 'second'].includes(example.precision)) {
    assert.equal(Date.parse(input('此项医学时间').value), Date.parse(editableTime(example).value));
  } else assert.equal(input('此项医学时间').value, editableTime(example).value);
  if (example.timezone_name) assert.equal(input('时区').value, example.timezone_name);
  await change(input('原始项目名称'), '合成更正名称');
  assert.deepEqual(current.field.medical_time, example);
  await cleanup();
}
pass('actual fact editor displays every saved precision; unrelated field edits retain exact time payload');

{
  const cleanup = await mount(h(EditorHarness, { initial: decision(examples[0]) }));
  assert.equal(input('此项医学时间').step, '1');
  await change(input('时间精度'), 'year');
  await change(input('此项医学时间'), '2');
  assert.equal(isTimeInputValid(current.time), false);
  assert.ok(document.querySelector('[role="alert"]'));
  await change(input('此项医学时间'), '2025');
  assert.equal(current.field.medical_time.local_year, 2025);
  assert.equal(current.field.medical_time.precision, 'year');
  await cleanup();
}
pass('second precision uses a second-capable input and partial year edits remain safely incomplete');

{
  let changed = false;
  const field = { ...decision(examples[0]).field, field_id: '00000000-0000-4000-8000-000000000022' };
  const cleanup = await mount(h(RecordFieldEditor, { field, onChange: () => { changed = true; },
    onTimeChange: () => { changed = true; }, ui2: true }));
  assert.equal(input('时间精度').value, 'second');
  assert.equal(input('时区').value, 'America/New_York');
  assert.equal(changed, false);
  await cleanup();
}
pass('opening actual record-correction editor preserves source time and does not fire a write/change');

{
  const form = { missing: decision(), explicit: decision(examples[0]), rawOnly: decision(null, '上午 09:00', 'item'), untouched: decision() };
  let saved;
  const cleanup = await mount(h(CommonFactTime, { form, disabled: false, onApply: value => { saved = value; } }));
  const button = () => [...document.querySelectorAll('button')].find(node => node.textContent === '应用到所选项目');
  assert.equal(button().disabled, true);
  assert.equal(input('共同时间项目 2').disabled, true);
  assert.equal(input('共同时间项目 3').disabled, true);
  await change(input('时间精度'), 'date');
  await change(input('共同医学时间'), '2026-10-10');
  await change(input('共同时间原文依据'), '本页所选检查均于 2026-10-10 采样');
  await change(input('共同时间项目 1'), true);
  assert.equal(button().disabled, true);
  await change(input('确认共同时间依据'), true);
  assert.equal(button().disabled, false);
  await act(async () => Simulate.click(button()));
  assert.equal(saved.missing.context.time_origin, 'explicit_common');
  assert.equal(saved.missing.field.medical_time.local_date, '2026-10-10');
  assert.deepEqual(saved.explicit, form.explicit); assert.deepEqual(saved.rawOnly, form.rawOnly);
  assert.deepEqual(saved.untouched, form.untouched);
  assert.equal(button().disabled, true);
  const defensive = applyCommonTime(form, Object.keys(form), editableTime(examples[6]), 'synthetic common evidence', true);
  assert.deepEqual(defensive.explicit, form.explicit); assert.deepEqual(defensive.rawOnly, form.rawOnly);
  assert.throws(() => applyCommonTime(form, ['missing'], editableTime(examples[6]), 'evidence', false));
  await cleanup();
}
pass('actual common-time flow requires item selection and source confirmation; explicit and unselected times cannot be overwritten');

{
  const { createMemoryRouter, RouterProvider } = await import('react-router');
  const { AccessProvider } = await import('../src/state/access.tsx');
  const { UI2ArchivePage } = await import('../src/features/archives/UI2ArchivePage.tsx');
  const patientId = '00000000-0000-4000-8000-000000000001', queries = [];
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(path, 'http://localhost');
    if (url.pathname === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (url.pathname === '/api/session') return Response.json(syntheticSession(patientId));
    if (url.pathname === '/api/relations') return Response.json({ relations: [{ relation_id: patientId, patient_id: patientId, display_id: '合成患儿', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'read_file', 'write_record'] }] });
    if (url.pathname.endsWith('/archives/search')) {
      queries.push(JSON.parse(options.body));
      return Response.json({ patient_id: patientId, items: [], next_cursor: null, snapshot_version: 'a'.repeat(64),
        matched_count: 0, authorized_document_count: 0, elapsed_ms: 1, facets: { organizations: [], episodes: [],
          unknown_organization_count: 0, unknown_time_count: 0, unknown_episode_count: 0 } });
    }
    return Response.json({ code: 'NOT_CONFIGURED', message: 'synthetic optional feature' }, { status: 404 });
  };
  const router = createMemoryRouter([{ path: '/archives', element: h(AccessProvider, null, h(UI2ArchivePage)) }], { initialEntries: ['/archives'] });
  const cleanup = await mount(h(RouterProvider, { router }));
  const flush = async () => act(async () => new Promise(resolve => setTimeout(resolve, 10)));
  await flush();
  assert.ok(queries.length); assert.equal(queries[0].time_basis, undefined);
  const shadow = () => document.querySelector('[data-screen-id]').shadowRoot;
  await act(async () => Simulate.click([...shadow().querySelectorAll('button')].find(node => node.textContent === '筛选')));
  await change(shadow().querySelector('[aria-label="日期依据"]'), 'episode');
  const filters = shadow().querySelector('[aria-label="筛选文书"]');
  await act(async () => Simulate.blur(filters, { relatedTarget: null }));
  await flush();
  assert.equal(queries.at(-1).time_basis, 'episode');
  assert.ok(shadow().textContent.includes('任一边界不明时保留未知'));
  await cleanup(); router.dispose();
}
pass('actual archive page keeps document-date default and submits explicit encounter-time filter');

{
  const { createMemoryRouter, RouterProvider } = await import('react-router');
  const { AccessProvider } = await import('../src/state/access.tsx');
  const { UI2RegroupPage } = await import('../src/features/archives/UI2RegroupPage.tsx');
  const { checkRegroupReceipt } = await import('../src/api/archiveRegrouping.ts');
  const id = value => `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`;
  const patient = id(1), batch = id(2), documentId = id(3), sourceVersion = id(4), pages = [id(5), id(6)];
  let identity, receipt, generation = '1', committed = false, lostResponse = true;
  const calls = [];
  const documentValue = { document_id: documentId, patient_id: patient, version_id: sourceVersion, version: '1', batch_id: batch,
    document_type: 'laboratory', organization: 'SYNTHETIC HOSPITAL', documented_time: null, page_ids: pages, episode_ids: [],
    grouping_status: 'pending', confirmed_text: 'Synthetic prior whole document', proposed_metadata: null,
    available_at: '2026-01-01T00:00:00Z', allowed_actions: ['archive_document_group'] };
  const intent = () => ({ intent_id: id(7), request_id: identity.request_id, generation, status: committed ? 'committed' : 'prepared',
    expires_at: '2099-01-01T00:00:00Z', payload_sha256: 'a'.repeat(64) });
  const requestView = () => ({ intent_id: id(7), request_id: identity.request_id, operation_id: identity.operation_id,
    target_key: identity.target_key, prepared_at: '2026-01-01T00:00:00Z', state: committed ? 'committed' : 'pending',
    may_retry: !committed, receipt: committed ? receipt : null, error_code: null, generation,
    expires_at: '2026-01-01T00:00:00Z', payload_sha256: 'a'.repeat(64), regroup_input: identity.payload });
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(path, 'http://localhost'), body = options.body ? JSON.parse(options.body) : null;
    calls.push({ path: url.pathname, method: options.method ?? 'GET', body, headers: options.headers });
    if (url.pathname === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (url.pathname === '/api/session') return Response.json(syntheticSession(patient));
    if (url.pathname === '/api/relations') return Response.json({ relations: [{ relation_id: id(10), patient_id: patient, display_id: '合成患儿', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'read_file', 'write_record', 'correct_record'] }] });
    if (url.pathname.endsWith('/regroup-options')) return Response.json({ patient_id: patient, batch_id: batch, documents: [documentValue] });
    if (url.pathname.endsWith('/document-inputs')) return Response.json({ patient_id: patient, batch_id: batch, episodes: [] });
    if (url.pathname === '/api/v2/write-intents') { identity = body; return Response.json(intent()); }
    if (url.pathname.endsWith('/retry')) { assert.equal(body.expected_generation, generation); generation = String(Number(generation) + 1); return Response.json(intent()); }
    if (url.pathname.endsWith('/archive-document-regroupings')) {
      assert.deepEqual(body, identity.payload);
      assert.equal(options.headers['Idempotency-Key'], identity.request_id);
      assert.equal(options.headers['X-Intent-Generation'], generation);
      receipt = { schema_version: '2.0.0', resource_type: 'archive_regrouping', resource_id: id(11), version: '1',
        saved_at: '2026-01-01T00:00:00Z', request_id: identity.request_id, task_id: null,
        result: { regrouping_id: id(11), patient_id: patient, batch_id: batch,
          sources: [{ document_id: documentId, version_id: sourceVersion, version: '1', page_ids: pages }],
          outputs: body.outputs.map((output, index) => ({ document_id: output.document_id ?? id(12), version_id: id(13 + index),
            version: output.document_id ? '2' : '1', page_ids: output.page_ids })), retired_document_ids: [], unassigned_page_ids: body.unassigned_page_ids } };
      committed = true;
      if (lostResponse) { lostResponse = false; throw new TypeError('Synthetic lost response after commit'); }
      return Response.json(receipt);
    }
    if (url.pathname.startsWith('/api/v2/requests/')) return Response.json(requestView());
    return Response.json({ code: 'NOT_CONFIGURED', message: 'synthetic optional feature' }, { status: 404 });
  };
  const create = entry => createMemoryRouter([{ path: '/archives/regroup', element: h(AccessProvider, null, h(UI2RegroupPage)) }], { initialEntries: [entry] });
  let router = create(`/archives/regroup?batch=${batch}`), cleanup = await mount(h(RouterProvider, { router }));
  const flush = async () => act(async () => new Promise(resolve => setTimeout(resolve, 15)));
  const shadow = () => document.querySelector('[data-screen-id]').shadowRoot;
  const button = text => [...shadow().querySelectorAll('button')].find(node => node.textContent === text);
  await flush();
  await change(shadow().querySelector('input[type="checkbox"]'), true);
  await act(async () => Simulate.click(button('增加新文书（拆分）')));
  await change(shadow().querySelector('[aria-label="原页 2 的目标文书"]'), '1');
  const confirmations = [...shadow().querySelectorAll('label')].filter(node => node.textContent.includes('本人已核对这份目标文书'));
  assert.equal(confirmations.length, 2);
  assert.equal(button('一次保存全部文书归属').disabled, true);
  for (const node of confirmations) await change(node.querySelector('input'), true);
  const reason = [...shadow().querySelectorAll('label')].find(node => node.querySelector('span')?.textContent === '重组原因').querySelector('textarea');
  await change(reason, 'Synthetic split by source pages');
  assert.equal(button('一次保存全部文书归属').disabled, false);
  await act(async () => Simulate.submit(shadow().querySelector('form'))); await flush();
  assert.deepEqual(identity.payload.outputs.map(value => value.page_ids), [[pages[0]], [pages[1]]]);
  assert.equal(identity.payload.outputs[1].document_id, null);
  assert.equal(identity.payload.outputs[0].confirmed_text, null);
  assert.equal(identity.payload.outputs[1].documented_time, null);
  assert.ok(router.state.location.search.includes('client_request='));
  assert.ok(!router.state.location.search.includes('Synthetic'));
  assert.equal(calls.filter(call => call.path.endsWith('/archive-document-regroupings')).length, 1);
  await act(async () => Simulate.click(button('查询原保存结果'))); await flush();
  assert.ok(shadow().textContent.includes('全部文书归属已保存'));
  assert.equal(calls.filter(call => call.path.endsWith('/archive-document-regroupings')).length, 1);
  assert.ok([...shadow().querySelectorAll('a')].some(node => node.href.includes(`document_version=${sourceVersion}`)));
  assert.throws(() => checkRegroupReceipt({ ...receipt, result: { ...receipt.result, outputs: receipt.result.outputs.toReversed() } }, identity));
  await cleanup(); router.dispose();
  pass('actual regroup editor splits selected pages, explicitly confirms metadata, and recovers a lost response without duplicate writes');

  committed = false; generation = '1';
  router = create(`/archives/regroup?batch=${batch}&client_request=${identity.request_id}`); cleanup = await mount(h(RouterProvider, { router })); await flush();
  assert.ok(button('核对后继续原请求'));
  const beforeWrites = calls.filter(call => call.path.endsWith('/archive-document-regroupings')).length;
  await act(async () => Simulate.click(button('核对后继续原请求'))); await flush();
  assert.equal(generation, '2');
  assert.equal(calls.filter(call => call.path.endsWith('/archive-document-regroupings')).length, beforeWrites + 1);
  assert.ok(shadow().textContent.includes('全部文书归属已保存'));
  await act(async () => window.dispatchEvent(new Event('pagehide')));
  assert.ok(!shadow().textContent.includes('全部文书归属已保存'));
  await cleanup(); router.dispose();
  pass('actual regroup recovery reuses immutable payload/request identity, advances only permitted generation, and clears private content on pagehide');
}
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_REACT_DOM', checks }, null, 2));
