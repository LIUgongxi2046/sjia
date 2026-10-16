// Actual CorrectionPage, editors and draft/submission hooks with synthetic HTTP.
// This checks DOM structure and behavior; it is not browser or visual evidence.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'https://localhost/' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'HTMLButtonElement', 'Event']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
// Explicit saves below are deterministic; do not run the 10-second autosave clock.
window.setInterval = () => 1;
window.clearInterval = () => {};
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { AccessProvider } = await import('../src/state/access.tsx');
const { CorrectionPage } = await import('../src/features/records/CorrectionPage.tsx');
const { validateRecordRead } = await import('../src/api/recordSearchSchema.ts');

const patient = randomUUID(), principal = randomUUID();
const typeLabels = { observation: '体温症状', report: '检验报告', therapy: '治疗资料' };
const day = value => ({ precision: 'date', raw_text: value, local_date: value, local_datetime: null, instant: null,
  timezone_name: null, resolution_status: 'local_only', local_year: null, local_month: null, end_year: null,
  end_month: null, relative_text: null, utc_offset_minutes: null });
const field = (field_code, original_name, extra = {}) => ({ field_id: randomUUID(), field_code, original_name,
  concept_code: null, value_type: 'text', value_state: 'known', raw_text: `SYNTHETIC ${original_name}`, raw_numeric: null,
  categorical_value: null, qualifier: null, raw_unit: null, medical_time_id: randomUUID(), medical_time: day('2026-10-01'),
  sample_time_origin: 'observation', method: 'SYNTHETIC METHOD', reference_interval: null, report_flag: null,
  verification_states: [], confirmation_id: null, source_anchor: null, ...extra });
function fixture(kind, allowed = true) {
  const core = kind === 'observation' ? [
    field('temp', '合成体温', { value_type: 'decimal', raw_numeric: '38.20', raw_text: '38.20', raw_unit: 'C', qualifier: 'eq' }),
    ...[['fever', '发热表现'], ['rash', '皮疹'], ['joint_discomfort', '关节疼痛或肿胀']].map(([code, label]) => field(code, label, { value_type: 'categorical', raw_text: null, categorical_value: 'yes' })),
    field('observation_note', '合成观察说明'),
  ] : kind === 'report' ? [
    field('report_item', '合成检验项目', { value_type: 'decimal', raw_numeric: '9007199254740993.000', raw_text: '9007199254740993.000', raw_unit: 'mg/L', qualifier: 'ge', sample_time_origin: 'item' }),
  ] : [
    field('medication_name', '合成原药名', { sample_time_origin: 'therapy' }),
    field('therapy_status', '合成使用状态', { value_type: 'categorical', raw_text: null, categorical_value: 'ongoing', sample_time_origin: 'therapy' }),
    field('therapy_start', '合成开始时间', { value_type: 'time', raw_text: null, sample_time_origin: 'therapy' }),
    field('therapy_end', '合成结束时间', { value_type: 'time', value_state: 'unknown', raw_text: null, medical_time_id: null, medical_time: null, sample_time_origin: 'unknown' }),
  ];
  // Duplicate codes with distinct field IDs must remain distinct, in original order.
  const fields = [field('synthetic_extra', '额外字段 A'), ...core, field('synthetic_extra', '额外字段 B'),
    field('synthetic_locked', '只读原字段'), field('synthetic_unconfigured', '未配置原字段')];
  const record = { patient_id: patient, record_id: randomUUID(), record_version_id: randomUUID(), version: 7, kind,
    origin_type: kind === 'report' ? 'report_transcription' : 'self_report', capture_method: 'manual', quality_status: 'pending',
    source_organization: null, correction_reason: null, corrected_at: null, recorded_at: '2026-10-01T00:00:00Z', report: null,
    attachments: [], allowed_actions: allowed ? ['correct_record'] : [], fields,
    fact_refs: fields.map(item => ({ field_id: item.field_id, fact_id: randomUUID(), version_id: randomUUID(), version: '1' })) };
  validateRecordRead('RecordView', record);
  return record;
}
const wireKeys = ['field_code', 'concept_code', 'original_name', 'value_type', 'value_state', 'raw_text', 'raw_numeric',
  'categorical_value', 'qualifier', 'raw_unit', 'method', 'reference_interval', 'report_flag', 'sample_time_origin', 'medical_time'];
const wireFields = fields => fields.map(item => Object.fromEntries(wireKeys.map(key => [key, item[key]])));
const page = () => document.querySelector('[data-screen-id]')?.shadowRoot;
const button = label => [...page().querySelectorAll('button')].find(node => node.textContent.trim() === label);
const editor = label => [...page().querySelectorAll('fieldset[aria-label]')].find(node => node.getAttribute('aria-label') === label);
const control = (parent, label) => [...parent.querySelectorAll('label')].find(node => node.textContent.startsWith(label))?.querySelector('input,textarea,select');
const reason = () => control(page(), '更正原因');
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
async function click(node) { assert.ok(node, 'click target exists'); assert.equal(node.matches(':disabled'), false); await act(async () => node.click()); await flush(); }
async function change(node, value) {
  assert.ok(node, 'input target exists'); assert.equal(node.matches(':disabled'), false);
  await act(async () => { node.value = value; Simulate.change(node, { target: { value, checked: value === true } }); });
  await flush();
}
async function openDetails(node) { assert.ok(node); await click(node.querySelector('summary')); assert.equal(node.open, true); }
let root, router;
async function mount(record, options = {}) {
  let draft = null, releaseRecord;
  const pendingRecord = new Promise(resolve => { releaseRecord = resolve; });
  const state = { record, writes: [], intents: [], draftWrites: [], configReads: 0, releaseRecord };
  const configFields = [...new Map(record.fields.filter(item => item.field_code !== 'synthetic_unconfigured').map(item => [item.field_code, {
    field_code: item.field_code, value_type: item.value_type, label: item.original_name,
    writable: item.field_code !== 'synthetic_locked', allowed_values: ['yes', 'no', 'ongoing', 'ended', 'not_started'],
  }])).values()];
  globalThis.fetch = async (path, request = {}) => {
    const url = new URL(path, 'https://localhost'), body = request.body ? JSON.parse(request.body) : null;
    if (url.pathname === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (url.pathname === '/api/session') return Response.json(syntheticSession(principal, patient));
    if (url.pathname === '/api/relations') return Response.json({ relations: [{ relation_id: randomUUID(), patient_id: patient,
      display_id: 'SYNTHETIC CHILD', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null,
      actions: ['read_raw', 'correct_record', 'write_record', 'view_request', 'manage_draft'] }] });
    if (url.pathname === `/api/records/${record.record_id}`) return options.delayRead ? pendingRecord : Response.json(state.record);
    if (url.pathname.endsWith('/field-config')) { state.configReads++; assert.equal(url.searchParams.get('kind'), record.kind); return Response.json(syntheticFieldConfig(patient, record.kind, configFields)); }
    if (url.pathname === `/api/patients/${patient}/requests`) return Response.json({ patient_id: patient, requests: [] });
    if (url.pathname === `/api/patients/${patient}/drafts` || draft && url.pathname === `/api/drafts/${draft.draft_id}`) {
      if (request.method === 'POST' || request.method === 'PUT') {
        state.draftWrites.push(body);
        draft = { draft_id: draft?.draft_id ?? randomUUID(), patient_id: patient, form_type: 'correction', status: 'editing',
          version: String(state.draftWrites.length), saved_at: '2026-10-13T12:00:00Z', payload: body.payload };
      }
      return Response.json(draft);
    }
    if (url.pathname === '/api/write-intents') {
      assert.equal(body.operation_id, 'record_correct'); state.intents.push(body); state.intent = syntheticIntent(randomUUID(), body.request_id);
      return Response.json(state.intent);
    }
    if (url.pathname === `/api/records/${record.record_id}/corrections`) {
      state.writes.push(body); assert.deepEqual(body, state.intents.at(-1).payload);
      assert.equal(request.headers['Idempotency-Key'], state.intent.request_id);
      if (options.conflict) {
        state.record = { ...record, version: 8, record_version_id: randomUUID() };
        return Response.json({ code: 'VERSION_CONFLICT', message: 'Synthetic newer version', retryable: false, recovery: 'reload', field_errors: [] }, { status: 409 });
      }
      return Response.json({ patient_id: patient, record_id: record.record_id, record_version_id: randomUUID(), version: record.version + 1 });
    }
    throw new Error(`Unexpected synthetic correction-layout request ${path}`);
  };
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([
    { path: '/records/:recordId/correct', element: h(AccessProvider, null, h(CorrectionPage)) },
    { path: '/records/:recordId', element: h('p', null, 'SYNTHETIC RECORD DESTINATION') },
  ], { initialEntries: [`/records/${record.record_id}/correct`] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
  if (!options.delayRead && record.allowed_actions.length) for (let wait = 0; wait < 30 && reason()?.disabled; wait++) await flush();
  return state;
}
async function unmount() { await act(async () => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
const checks = [];
try {
  for (const kind of ['observation', 'report', 'therapy']) {
    const record = fixture(kind), expected = structuredClone(record.fields), before = JSON.stringify(record);
    const state = await mount(record);
    const context = page().querySelector('.note.amber');
    assert.ok(context.textContent.includes(typeLabels[kind])); assert.ok(context.textContent.includes('当前版本 v7'));
    assert.equal(context.closest('details'), null); assert.ok(page().textContent.includes('SYNTHETIC CHILD'));
    assert.ok(reason().closest('label').textContent.includes('必填')); assert.equal(reason().getAttribute('aria-required'), 'true');
    assert.equal(button('保存更正').disabled, true, 'empty reason remains invalid');
    const editors = [...page().querySelectorAll('fieldset[aria-label]')];
    assert.deepEqual(editors.map(node => node.getAttribute('aria-label')), record.fields.map(item => item.original_name));
    if (kind === 'observation') {
      assert.ok(page().querySelector('.voice-entry')); assert.ok(page().querySelector('[aria-label="体温原值"]'));
      assert.equal(page().querySelectorAll('.symptoms [role=tablist]').length, 3);
      const details = editors[0].closest('details'); assert.ok(details); await openDetails(details);
      await change(page().querySelector('[aria-label="体温原值"]'), '37.90');
      Object.assign(expected.find(item => item.field_code === 'temp'), { raw_numeric: '37.90', raw_text: '37.90' });
      await click(page().querySelector('[role=tablist][aria-label="皮疹"] button:nth-child(2)'));
      expected.find(item => item.field_code === 'rash').categorical_value = 'no';
    } else {
      for (const selector of ['.voice-entry', '.temperature', '.symptoms', '[aria-label="体温原值"]', '[aria-label="测量方式"]']) assert.equal(page().querySelector(selector), null, `${kind} has no observation widget ${selector}`);
      assert.ok(!page().textContent.includes('测量时间'));
      for (const item of editors) assert.equal(item.closest('details'), null, 'all original fields are primary content');
    }
    for (const label of ['只读原字段', '未配置原字段']) {
      assert.equal(editor(label).disabled, true); assert.equal(control(editor(label), '原始内容').matches(':disabled'), true);
      assert.equal(editor(label).querySelector('[aria-label="时间精度"]').disabled, true);
    }
    await change(control(editor('额外字段 B'), '原始内容'), `SYNTHETIC EDITED ${kind}`);
    expected.find(item => item.original_name === '额外字段 B').raw_text = `SYNTHETIC EDITED ${kind}`;
    assert.equal(control(editor('额外字段 A'), '原始内容').value, expected[0].raw_text, 'duplicate code edit targets field ID only');
    const timeDetails = [...editor('额外字段 B').querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent.includes('更正医学时间'));
    await openDetails(timeDetails); await change(control(editor('额外字段 B'), '医学时间'), '2026-10-05');
    expected.find(item => item.original_name === '额外字段 B').medical_time = day('2026-10-05');
    await change(reason(), '   '); assert.equal(button('保存更正').disabled, true, 'whitespace reason remains invalid');
    await change(reason(), `SYNTHETIC REASON ${kind}`);
    if (kind === 'therapy') {
      const confirmed = () => page().querySelector('input[type=checkbox]');
      assert.equal(button('保存更正').disabled, true); await change(confirmed(), true); assert.equal(button('保存更正').disabled, false);
      await change(control(editor('合成原药名'), '原始内容'), 'SYNTHETIC MEDICATION EDIT');
      expected.find(item => item.field_code === 'medication_name').raw_text = 'SYNTHETIC MEDICATION EDIT';
      assert.equal(confirmed().checked, false, 'existing field edit invalidates family confirmation');
      await change(confirmed(), true);
    }
    assert.equal(button('保存更正').disabled, false);
    await click(page().querySelector('[aria-label="返回上级页面"]'));
    const dialog = document.querySelector('dialog'); assert.ok(dialog);
    // This is the hook's native onclick, not a React synthetic event.
    await click([...dialog.querySelectorAll('button')].find(node => node.textContent === '继续编辑'));
    assert.equal(document.querySelector('dialog'), null); assert.equal(router.state.location.pathname, `/records/${record.record_id}/correct`);
    assert.equal(reason().value, `SYNTHETIC REASON ${kind}`);
    assert.equal(control(editor('额外字段 B'), '原始内容').value, `SYNTHETIC EDITED ${kind}`);
    assert.equal(control(editor('额外字段 B'), '医学时间').value, '2026-10-05');
    assert.equal(state.writes.length, 0); assert.equal(state.draftWrites.length, 0);
    await click(button('保存为草稿')); assert.equal(state.draftWrites.length, 1);
    const saved = state.draftWrites[0].payload;
    assert.deepEqual(saved.fields, expected, 'draft retains full fields, exact order, IDs and metadata');
    assert.deepEqual(saved.fields.map(item => item.field_id), record.fields.map(item => item.field_id));
    assert.equal(saved.version, 7); assert.equal(saved.kind, kind);
    assert.equal(saved.timeInputs[expected.find(item => item.original_name === '额外字段 B').field_id].value, '2026-10-05');
    await click(button('保存更正')); assert.equal(state.writes.length, 1);
    assert.deepEqual(state.writes[0].fields, wireFields(expected), 'wire field projection and original order stay unchanged');
    assert.equal(state.writes[0].expected_version, 7); assert.equal(state.writes[0].reason, `SYNTHETIC REASON ${kind}`);
    assert.equal(state.writes[0].family_confirmation, kind === 'therapy' ? true : undefined);
    assert.equal(router.state.location.pathname, `/records/${record.record_id}`); assert.equal(JSON.stringify(record), before);
    await unmount(); checks.push(`${kind}: original field order, duplicate-code extra editor callbacks/time, read-only scope, required reason, back/cancel, draft IDs and full correction payload`);
  }
  {
    const record = fixture('report', false), state = await mount(record);
    assert.equal(state.configReads, 0); assert.ok(page().textContent.includes('当前未取得更正权限'));
    assert.ok(page().querySelector('.note.amber').textContent.includes('当前版本 v7'));
    assert.equal(page().querySelector('.temperature'), null);
    for (const item of page().querySelectorAll('fieldset[aria-label]')) assert.equal(item.disabled, true);
    assert.equal(reason().disabled, true); assert.equal(button('保存更正').disabled, true); assert.equal(button('保存为草稿').disabled, true);
    await click(page().querySelector('[aria-label="返回上级页面"]'));
    assert.equal(router.state.location.pathname, `/records/${record.record_id}`); assert.equal(document.querySelector('dialog'), null);
    assert.equal(state.writes.length, 0); await unmount(); checks.push('record-level permission remains read-only; clean back navigates without writes');
  }
  {
    const record = { ...fixture('report'), kind: 'professional' }, state = await mount(record);
    assert.equal(state.configReads, 0, 'professional record cannot fetch writable field configuration even with an allowed action');
    assert.ok(page().querySelector('.note.amber').textContent.includes('获准临床资料 · 当前版本 v7'));
    assert.ok(page().textContent.includes('当前未取得更正权限'));
    assert.equal(page().querySelector('.temperature'), null); assert.equal(page().querySelector('.voice-entry'), null);
    for (const item of page().querySelectorAll('fieldset[aria-label]')) assert.equal(item.disabled, true);
    assert.equal(reason().disabled, true); assert.equal(button('保存更正').disabled, true); assert.equal(button('保存为草稿').disabled, true);
    assert.equal(state.writes.length, 0); await unmount(); checks.push('professional record remains fail-closed despite a supplied correct_record action');
  }
  {
    const record = fixture('therapy'), state = await mount(record, { delayRead: true });
    assert.equal(page().querySelectorAll('fieldset[aria-label]').length, 0);
    assert.equal(page().querySelector('.temperature'), null); assert.equal(page().querySelector('.voice-entry'), null);
    assert.ok(!page().querySelector('.note.amber').textContent.includes('当前版本'));
    assert.ok(!page().querySelector('.note.amber').textContent.includes(typeLabels.therapy));
    await act(async () => state.releaseRecord(Response.json(record))); await flush(); await flush();
    assert.ok(page().querySelector('.note.amber').textContent.includes('当前版本 v7'));
    await unmount(); checks.push('loading does not invent record type, version or observation fields');
  }
  {
    const record = fixture('report'), state = await mount(record, { conflict: true });
    await change(control(editor('额外字段 B'), '原始内容'), 'SYNTHETIC CONFLICT INPUT');
    await change(reason(), 'SYNTHETIC CONFLICT REASON'); await click(button('保存更正')); await flush();
    assert.equal(state.writes.length, 1); assert.equal(state.writes[0].expected_version, 7);
    assert.ok(page().textContent.includes('原版本已有更新')); assert.ok(page().querySelector('.note.amber').textContent.includes('当前版本 v8'));
    assert.equal(button('保存更正').disabled, true); assert.equal(button('保存为草稿').disabled, true);
    assert.equal(state.intents.length, 1, 'conflict does not automatically merge or resubmit');
    assert.equal(state.writes[0].fields.find(item => item.original_name === '额外字段 B').raw_text, 'SYNTHETIC CONFLICT INPUT');
    await unmount(); checks.push('conflict shows actual reloaded record version and keeps stale edits blocked without automatic merge');
  }
  console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_ACTUAL_CORRECTION_LAYOUT_DOM', checks }, null, 2));
} finally { if (root?._internalRoot) await unmount(); dom.window.close(); }
