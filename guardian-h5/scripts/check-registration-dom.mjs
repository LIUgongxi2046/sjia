// Execute actual React forms/hooks with synthetic fetch and a deterministic
// 10-second autosave clock. This is DOM coverage, not visual/browser QA.
import './registration-test-loader.mjs';
import { syntheticBootstrap, syntheticSession, syntheticFieldConfig, syntheticIntent, fixtureTime } from './response-test-fixtures.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { randomUUID } from 'node:crypto';
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLTextAreaElement', 'HTMLSelectElement', 'HTMLButtonElement', 'HTMLAnchorElement', 'Event', 'FocusEvent']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
window.confirm = () => false;
let timers = new Map(), timerSequence = 0;
window.setTimeout = (callback, delay) => { const id = ++timerSequence; timers.set(id, { callback, delay }); return id; };
window.clearTimeout = id => timers.delete(id);
window.setInterval = (callback, delay) => { const id = ++timerSequence; timers.set(id, { callback, delay, interval: true }); return id; };
window.clearInterval = id => timers.delete(id);
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { AccessProvider, useAccess } = await import('../src/state/access.tsx');
const { TherapiesPage } = await import('../src/features/registration/TherapiesPage.tsx');
const { ObservationsPage } = await import('../src/features/registration/ObservationsPage.tsx');
const { restoreObservationDraft, separateObservationTimes } = await import('../src/features/registration/observationDraft.ts');
const { timePayload } = await import('../src/components/medicalTimeValue.ts');
const { ReportsPage } = await import('../src/features/registration/ReportsPage.tsx');
const { createRecordSubmission, clearPendingRecordRequests } = await import('../src/api/recordSubmission.ts');
let latestAccess;
function AccessCapture() { latestAccess = useAccess(); return null; }
const secondPatientId = '00000000-0000-4000-8000-000000000099';
const patientId = '00000000-0000-4000-8000-000000000001';
const photoId = '00000000-0000-4000-8000-000000000002';
const emptyTime = { precision: 'unknown', value: '', timezone: '' };
const codes = ['dose', 'dose_unit', 'concentration_text', 'route_text', 'frequency_text', 'interval_value', 'interval_unit'];
const labels = ['单次剂量', '剂量单位', '制剂浓度', '给药途径', '原文频次', '间隔数值', '间隔单位'];
const therapyPayload = { medication: '合成药物A', status: '无法确定', note: '合成A原文', itemDetails: Object.fromEntries(codes.map((code, i) => [code, ['5', 'mg', '10mg/ml', 'oral', 'daily', '12', 'hour'][i]])), startTime: emptyTime, endTime: emptyTime, photos: [{ file_id: photoId, name: 'synthetic.png' }], source: 'report_transcription', sourceReference: 'synthetic.png', candidates: [{ name: '合成药物A', source_text: '合成A原文', photo_id: photoId }, { name: '合成药物B', source_text: '合成B原文', photo_id: photoId }], selectedSource: { photo_id: photoId, source_text: '合成A原文' } };
const reportPayload = { candidates: [{ name: '合成项目', value: '1', unit: 'x', comparator: '=', reference: null, sampleTime: emptyTime }], photos: [], organization: '', reportRef: '', sampleTime: emptyTime, resultTime: emptyTime, reportStatus: 'unknown' };
const checks = [];
function pass(name) { checks.push(name); console.log(`PASS ${name}`); }
async function flush() { await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); }); }
async function click(element) { assert.ok(element, 'click target exists'); await act(async () => Simulate.click(element)); await flush(); }
async function nativeClick(element) { assert.ok(element, 'native click target exists'); await act(async () => element.click()); await flush(); }
async function change(element, value) { assert.ok(element, 'change target exists'); await act(async () => Simulate.change(element, { target: { value, checked: value === true } })); await flush(); }
async function advanceAutosave() { await act(async () => { const due = [...timers]; for (const [id, timer] of due) if (timer.delay <= 11000) { if (!timer.interval) timers.delete(id); timer.callback(); } }); await flush(); }
function shadow() { const page = document.querySelector('[data-screen-id]'); return page?.shadowRoot ?? page; }
function button(text) { return [...shadow().querySelectorAll('button')].find(node => node.textContent === text); }
function input(label) { return shadow().querySelector(`[aria-label="${label}"]`) || [...shadow().querySelectorAll('label')].find(node => node.querySelector('span')?.textContent === label)?.querySelector('input,textarea,select'); }

function syntheticServer({ kind, payload, lose = false, reject = false, delayDraft = false, initialPatientId = patientId, failDraftRead = false }) {
  let draftWrites = 0, draftReads = 0, dropped = false, rejected = false, releaseDraft;
  let principal = patientId, selectedPatient = initialPatientId;
  const signals = [];
  const draftId = randomUUID(), intents = new Map(), committed = new Map(), requests = [];
  let draft = payload ? { draft_id: draftId, patient_id: initialPatientId, form_type: kind, version: '1', status: 'editing', saved_at: new Date().toISOString(), payload } : null;
  globalThis.fetch = async (path, options = {}) => {
    const url = new URL(path, 'http://localhost'), body = options.body ? JSON.parse(options.body) : null;
    path = url.pathname;
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap('synthetic-csrf'));
    if (path === '/api/session') return Response.json(syntheticSession(principal, selectedPatient, 'synthetic-csrf'));
    if (path === '/api/patient-selection') { selectedPatient = body.patient_id; return Response.json({ selected_patient_id: selectedPatient }); }
    if (path === '/api/relations') return Response.json({ relations: [patientId, secondPatientId].map(id => ({ authorization_scope_fingerprint: 'a'.repeat(64), authorization_scope_valid_until: null, relation_id: id, patient_id: id, display_id: '合成测试资料', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null, actions: ['read_raw', 'read_file', 'write_record', 'manage_draft'] })) });
    if (path.endsWith('/requests')) return Response.json({ patient_id: path.split('/')[3], requests: [] });
    if (path.endsWith('/field-config')) {
      const type = url.searchParams.get('kind');
      const specs = type === 'therapy' ? [['medication_name', 'text'], ['therapy_note', 'text'], ['therapy_status', 'categorical'], ['therapy_start', 'time'], ['therapy_end', 'time'], ['therapy_source', 'text'], ...codes.map(code => [code, ['dose', 'interval_value'].includes(code) ? 'decimal' : code === 'interval_unit' ? 'categorical' : 'text'])] : type === 'observation' ? [['temp', 'decimal'], ['observation_note', 'text'], ['fever', 'categorical'], ['rash', 'categorical'], ['joint_discomfort', 'categorical'], ['other_symptom', 'categorical']] : [['report_item', 'decimal']];
      return Response.json(syntheticFieldConfig(path.split('/')[3], type, specs.map(([field_code, value_type]) => ({ field_code, value_type, label: field_code === 'fever' ? '发热表现' : field_code, writable: true, allowed_values: type === 'observation' && value_type === 'categorical' ? ['yes', 'no'] : field_code === 'interval_unit' ? ['hour', 'day'] : ['ongoing', 'ended', 'not_started'] }))));
    }
    if (path === '/api/write-intents') {
      if (intents.has(body.request_id)) assert.deepEqual(intents.get(body.request_id).payload, body.payload);
      else intents.set(body.request_id, { id: randomUUID(), payload: body.payload });
      return Response.json(syntheticIntent(intents.get(body.request_id).id, body.request_id, intents.get(body.request_id).generation ?? '1'));
    }
    if (path.endsWith('/records') && options.method === 'POST') {
      signals.push(options.signal);
      const key = options.headers['Idempotency-Key']; requests.push({ key, body });
      assert.deepEqual(intents.get(key).payload, body);
      if (reject && !rejected) { rejected = true; return Response.json({ code: 'INPUT_INVALID', message: '合成字段校验失败' }, { status: 422 }); }
      if (!committed.has(key)) committed.set(key, { patient_id: patientId, record_id: randomUUID(), record_version_id: randomUUID(), version: 1 });
      if (lose && !dropped) { dropped = true; throw new TypeError('Synthetic lost response after commit'); }
      return Response.json(committed.get(key));
    }
    if (path === `/api/drafts/${draftId}` && options.method === 'GET') { draftReads++; if (failDraftRead) { failDraftRead = false; return Response.json({ code: 'UNAVAILABLE', message: '合成草稿读取失败' }, { status: 503 }); } return Response.json(draft); }
    if ((path.endsWith('/drafts') || path === `/api/drafts/${draftId}`) && ['POST', 'PUT'].includes(options.method)) {
      draftWrites += 1;
      if (delayDraft) await new Promise(resolve => { releaseDraft = resolve; });
      draft = { draft_id: draftId, patient_id: patientId, form_type: kind, version: String(Number(draft?.version || 0) + 1), status: 'editing', saved_at: new Date().toISOString(), payload: body.payload };
      return Response.json(draft);
    }
    if (path.endsWith('/records/search')) return Response.json({ records: [], next_cursor: null });
    return Response.json({ code: 'OPTIONAL_FEATURE_NOT_CONFIGURED', message: '合成测试未配置可选功能' }, { status: 404 });
  };
  return { requests, committed, draftId, signals, get draft() { return draft; }, setPrincipal: value => { principal = value; }, get draftWrites() { return draftWrites; }, get draftReads() { return draftReads; }, releaseDraft: () => releaseDraft?.() };
}
async function mount(kind, options = {}) {
  timers.clear();
  const server = syntheticServer({ kind, ...options });
  const path = kind === 'therapy' ? '/register/therapies' : kind === 'report' ? '/register/reports/confirm' : '/register/observations';
  const Page = kind === 'therapy' ? TherapiesPage : kind === 'report' ? ReportsPage : ObservationsPage;
  const element = h(AccessProvider, null, h(AccessCapture), h(Page, { ui2: options.ui2 !== false }));
  const formRoute = kind === 'report' ? { element, children: [{ path: '/register/reports', element: null }, { path: '/register/reports/confirm', element: null }] } : { path, element };
  const router = createMemoryRouter([formRoute, { path: '/records', element: h('p', null, 'saved') }], { initialEntries: [path + (options.payload ? '?draft=' + server.draftId : '')] });
  const root = createRoot(document.getElementById('root'));
  await act(async () => root.render(h(RouterProvider, { router })));
  await flush();
  assert.ok(shadow(), 'actual form is mounted');
  return { server, router, get access() { return latestAccess; }, unmount: async () => { await act(async () => root.unmount()); router.dispose(); } };
}

// Exact export of tools/demo/enrichment_plan.py:draft_plan(2026-10-12T07:00:00Z),
// using its Asia/Shanghai day. Keep the fixture identical to the Python plan;
// these assertions inspect actual ReportsPage/TherapiesPage + useFormDraft DOM.
const enrichmentDrafts = JSON.parse(readFileSync(new URL('./fixtures/enrichment-drafts.synthetic.json', import.meta.url), 'utf8'));
assert.equal(enrichmentDrafts.length, 4);
for (const fixture of enrichmentDrafts) {
  const payload = structuredClone(fixture.payload);
  const fixtureBefore = JSON.stringify(payload);
  const initialPatientId = fixture.patient_key === 'child_b' ? secondPatientId : patientId;
  const test = await mount(fixture.form_type, { payload, initialPatientId });
  assert.equal(test.access.patientId, initialPatientId);
  assert.equal(test.server.draftReads, 1, 'useFormDraft must restore from the saved draft endpoint');
  if (fixture.form_type === 'report') {
    const items = [...shadow().querySelectorAll('.registration-candidate')];
    assert.equal(items.length, payload.candidates.length);
    for (const [index, candidate] of payload.candidates.entries()) {
      const controls = [...items[index].querySelectorAll('label')];
      for (const [key, label] of Object.entries({ name:'原项目名称', comparator:'原报告比较符', value:'原始数值', unit:'原始单位', reference:'参考区间原文' })) {
        assert.equal(controls.find(node => node.querySelector('span')?.textContent === label)?.querySelector('input')?.value, candidate[key] ?? '', `${fixture.key}: restored ${key}`);
      }
      assert.equal(items[index].querySelector('[aria-label="时间精度"]').value, candidate.sampleTime.precision);
      assert.equal(input(`项目 ${index + 1} 采样时间`).value, candidate.sampleTime.value);
      assert.equal(input(`项目 ${index + 1} 采样时间`).disabled, true, 'unknown item time must not inherit the report date');
    }
    assert.equal(input('报告标识或来源引用').value, payload.reportRef);
    assert.equal(input('报告机构').value, payload.organization);
    assert.equal(input('报告结果状态').value, payload.reportStatus);
    assert.equal(input('报告记载的采样时间').value, payload.sampleTime.value);
    assert.equal(input('报告出具时间').value, payload.resultTime.value);
    assert.equal(input('报告出具时间').disabled, true);
    assert.equal(button('确认并保存').disabled, true);
  } else {
    assert.equal(input('原药名').value, payload.medication);
    assert.equal(input('实际使用状态').value, payload.status);
    assert.equal(input('资料说明').value, payload.note);
    assert.equal(input('开始日期').value, payload.startTime.value);
    assert.equal(input('结束日期').value, payload.endTime.value);
    assert.deepEqual([...shadow().querySelectorAll('[aria-label="时间精度"]')].map(node => node.value), [payload.startTime.precision, payload.endTime.precision]);
    assert.equal(input('资料来源').value, payload.source);
    assert.equal(input('来源描述或引用').value, payload.sourceReference);
    assert.equal(shadow().querySelectorAll('[aria-label^="治疗候选 "]').length, payload.candidates.length);
    for (const label of labels) assert.equal(input(label).value, '', `${fixture.key}: unprovided ${label} stays unknown`);
    assert.ok(!shadow().textContent.includes('采用的来源原文：'));
    assert.equal(button('确认并保存资料').disabled, true);
  }
  const confirm = shadow().querySelector('.registration-confirmation input');
  assert.ok(confirm); assert.equal(confirm.checked, false, 'draft restoration must never confirm on behalf of the family');
  await advanceAutosave();
  assert.equal(test.server.draftWrites, 0, 'restoring an unchanged draft does not create another draft version');
  assert.equal(test.server.requests.length, 0, 'restoration must not submit a clinical record');
  assert.equal(JSON.stringify(payload), fixtureBefore);
  await test.unmount();
  pass(`exact enrichment ${fixture.key} restores all rendered fields and independent unknown times, with confirmation unchecked and no writes`);
}

// Submit the actual manual observation form, not a hand-built record payload.
for (const [unit, rawUnit, rawNumeric] of [['摄氏度（°C）', 'C', '38.6'], ['华氏度（°F）', 'F', '101.5']]) {
  const test = await mount('observation');
  await change(input('体温单位'), unit);
  for (const invalid of ['Infinity', '-Infinity', 'NaN', '发热', '>38.0', '<38.0', '=38.0']) {
    await change(input('体温原值'), invalid);
    assert.equal(button('确认并保存').disabled, true, `non-finite/non-numeric temperature is blocked: ${invalid}`);
    await click(button('确认并保存'));
    assert.equal(test.server.requests.length, 0);
  }
  await change(input('体温原值'), ` ${rawNumeric} `);
  const measured = '2026-10-12T09:06', observed = '2026-10-12T08:45';
  for (const [index, label, value] of [[0, '测量时间', measured], [5, '补充说明发生时间', observed]]) {
    await change(shadow().querySelectorAll('[aria-label="时间精度"]')[index], 'minute');
    const timeInput = input(label);
    timeInput.value = value;
    await change(timeInput, value);
    await change(timeInput.closest('label').parentElement.querySelector('[aria-label="时区"]'), 'Asia/Shanghai');
  }
  await change(input('测量方式'), '腋下');
  await change(input('补充说明'), '合成观察说明');
  await click(shadow().querySelector('[aria-label="皮疹"] button'));
  assert.equal(button('确认并保存').disabled, false);
  await click(button('确认并保存'));
  assert.equal(test.router.state.location.pathname, '/records');
  assert.equal(test.server.requests.length, 1);
  const payload = test.server.requests[0].body;
  assert.equal(payload.kind, 'observation');
  assert.equal(payload.capture_method, 'manual');
  assert.equal(payload.fields.length, 3);
  const temperature = payload.fields.find(field => field.field_code === 'temp');
  assert.equal(temperature.qualifier, 'eq', 'manual numeric temperature is an exact reading');
  assert.equal(temperature.value_type, 'decimal');
  assert.equal(temperature.value_state, 'known');
  assert.equal(temperature.raw_numeric, rawNumeric);
  assert.equal(temperature.raw_unit, rawUnit);
  assert.equal(temperature.method, '腋下');
  const expectedTime = value => ({ precision: 'minute', raw_text: value,
    local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null,
    local_date: null, local_datetime: value, timezone_name: 'Asia/Shanghai',
    utc_offset_minutes: null, instant: null, resolution_status: 'local_only' });
  assert.deepEqual(temperature.medical_time, expectedTime(measured));
  for (const field of payload.fields.filter(field => field.field_code !== 'temp')) {
    assert.equal(Object.hasOwn(field, 'qualifier'), false);
    assert.equal(Object.hasOwn(field, 'raw_numeric'), false);
    assert.equal(Object.hasOwn(field, 'raw_unit'), false);
    assert.deepEqual(field.medical_time, field.field_code === 'observation_note' ? expectedTime(observed) : null);
  }
  assert.equal(payload.fields.find(field => field.field_code === 'rash').categorical_value, 'yes');
  assert.equal(payload.fields.find(field => field.field_code === 'observation_note').raw_text, '合成观察说明');
  await test.unmount();
  pass(`actual manual ${rawUnit} temperature submits exact qualifier, source value/unit and separate minute/Asia-Shanghai note time with symptom time unknown; invalid numbers are blocked and symptoms gain no numeric fields`);
}
{
  const test = await mount('observation');
  await click(shadow().querySelector('[aria-label="皮疹"] button'));
  await click(button('确认并保存'));
  assert.equal(test.router.state.location.pathname, '/records');
  assert.equal(test.server.requests.length, 1);
  assert.deepEqual(test.server.requests[0].body.fields, [{ field_code: 'rash', original_name: 'rash',
    value_type: 'categorical', value_state: 'known', categorical_value: 'yes',
    sample_time_origin: 'observation', medical_time: null }]);
  await test.unmount();
  pass('actual symptom-only submission keeps unknown time and does not fabricate a temperature or comparator');
}


// Observation-specific draft compatibility and independent time contracts.
const legacyObservation = { temp: '', tempUnit: '尚未选择', description: '合成旧说明', method: '',
  measuredTime: emptyTime, observedTime: { precision: 'minute', value: '2026-09-30T23:45', timezone: 'Asia/Shanghai' } };
const symptomTime = code => shadow().querySelector(`[data-symptom-time="${code}"]`);
async function editSymptomTime(code, precision, value = '', timezone = '') {
  await change(symptomTime(code).querySelector('[aria-label="时间精度"]'), precision);
  if (precision !== 'unknown') {
    const target = symptomTime(code).querySelector('input:not([aria-label])');
    target.value = value; await change(target, value);
    if (timezone) await change(symptomTime(code).querySelector('[aria-label="时区"]'), timezone);
  }
}
const sharedConfirmation = () => shadow().querySelector('[aria-label="旧草稿共用时间"] input[type="checkbox"]');
const byCode = request => Object.fromEntries(request.body.fields.map(field => [field.field_code, field]));
{
  const preserved = timePayload(legacyObservation.observedTime);
  preserved.raw_text = '合成原文：9月30日晚 23:45';
  const values = [legacyObservation, { ...legacyObservation, symptoms: {}, voiceApplied: false },
    { ...legacyObservation, observedTime: { precision: 'month', value: '', timezone: '' } },
    { ...legacyObservation, observedTime: { ...legacyObservation.observedTime, utcOffsetMinutes: null, preserved } }];
  for (const value of values) {
    const before = structuredClone(value);
    assert.equal(restoreObservationDraft(value), value, 'restore returns the exact object, without normalization');
    assert.deepEqual(value, before);
  }
  for (const invalid of [{ ...legacyObservation, symptoms: [] }, { ...legacyObservation, symptoms: { rash: null } },
    { ...legacyObservation, observedTime: null }, { ...legacyObservation, observedTime: { ...emptyTime, precision: 'future' } },
    { ...legacyObservation, observedTime: { ...emptyTime, precision: ['minute'] } },
    { ...legacyObservation, observedTime: { ...emptyTime, preserved: { ...preserved, precision: ['minute'] } } },
    { ...legacyObservation, observedTime: { ...emptyTime, preserved: { ...preserved, resolution_status: ['local_only'] } } },
    { ...legacyObservation, symptomTimes: [] }, { ...legacyObservation, version: 99 },
    { ...legacyObservation, observedTime: { ...emptyTime, preserved: {} } }]) assert.throws(() => restoreObservationDraft(invalid), /草稿格式/);
  const converted = separateObservationTimes({ ...legacyObservation, symptoms: { rash: 'yes' } });
  assert.equal(separateObservationTimes(converted), converted, 'conversion is idempotent');
  assert.notEqual(converted.symptomTimes.rash, legacyObservation.observedTime);
  assert.deepEqual(converted.symptomTimes.rash, legacyObservation.observedTime);
  pass('observation draft validation preserves exact optional presence, original time fields and incomplete input; invalid/unknown shapes fail safely');
}
{
  const test = await mount('observation', { payload: structuredClone(legacyObservation) });
  assert.ok(sharedConfirmation()); assert.equal(sharedConfirmation().checked, false);
  assert.equal(button('确认并保存').disabled, true);
  assert.equal(symptomTime('rash'), null, 'legacy remains explicitly shared until conversion');
  await advanceAutosave();
  await act(async () => input('补充说明').dispatchEvent(new FocusEvent('focusout', { bubbles: true, composed: true })));
  await change(sharedConfirmation(), true); await advanceAutosave();
  assert.equal(test.server.draftWrites, 0); assert.equal(test.server.requests.length, 0);
  assert.deepEqual(test.server.draft.payload, legacyObservation);
  await act(async () => test.router.navigate('/records')); await flush();
  assert.equal(test.router.state.location.pathname, '/records'); assert.equal(document.querySelector('dialog'), null);
  await act(async () => test.router.navigate(-1)); await flush();
  assert.equal(sharedConfirmation().checked, false, 'returning restores no prior UI acknowledgment');
  await advanceAutosave(); assert.equal(test.server.draftWrites, 0);
  await test.unmount();
  pass('legacy restore/blur/10-second autosave/acknowledgment/back navigation preserve payload and version with zero writes');
}
{
  const payload = { ...structuredClone(legacyObservation), symptoms: { rash: 'yes', fever: '' } };
  payload.observedTime.preserved = timePayload(payload.observedTime);
  payload.observedTime.preserved.raw_text = '合成原时间原文';
  const test = await mount('observation', { payload, lose: true });
  await advanceAutosave(); assert.equal(test.server.draftWrites, 0); assert.deepEqual(test.server.draft.payload, payload);
  await change(sharedConfirmation(), true);
  await change(input('原草稿共用发生时间').closest('label').parentElement.querySelector('[aria-label="时区"]'), 'Asia/Shanghai');
  assert.equal(sharedConfirmation().checked, false, 'editing shared time invalidates acknowledgment');
  const expectedSharedTime = timePayload({ ...payload.observedTime, preserved: undefined });
  await change(sharedConfirmation(), true);
  await change(input('补充说明'), '合成说明已修改');
  assert.equal(sharedConfirmation().checked, false, 'editing shared scope invalidates acknowledgment');
  await change(sharedConfirmation(), true);
  await click(shadow().querySelector('[aria-label="皮疹"] button:nth-child(2)'));
  assert.equal(sharedConfirmation().checked, false, 'changing selected symptom invalidates acknowledgment');
  await change(sharedConfirmation(), true);
  await advanceAutosave(); const writes = test.server.draftWrites;
  await click(button('确认并保存'));
  assert.ok(button('重试本次保存')); assert.equal(sharedConfirmation().disabled, true);
  assert.equal(button('改为逐项填写时间').disabled, true);
  await advanceAutosave(); assert.equal(test.server.draftWrites, writes);
  await click(button('重试本次保存'));
  assert.equal(test.server.requests.length, 2); assert.deepEqual(test.server.requests[0], test.server.requests[1]);
  assert.equal(test.server.committed.size, 1);
  for (const field of test.server.requests[0].body.fields) assert.deepEqual(field.medical_time, expectedSharedTime, 'explicit legacy acknowledgment retains original shared semantics');
  await test.unmount();
  pass('legacy shared submission requires fresh acknowledgment and preserves original shared time through frozen uncertain retry');
}
{
  const payload = { ...structuredClone(legacyObservation), symptoms: { rash: 'yes', fever: '' } };
  const test = await mount('observation', { payload });
  await click(button('改为逐项填写时间'));
  assert.equal(sharedConfirmation(), null); assert.equal(button('改为逐项填写时间'), undefined);
  assert.equal(symptomTime('rash').querySelector('input').value, payload.observedTime.value);
  assert.ok(symptomTime('rash').textContent.includes('时间沿用转换前的共用时间'));
  assert.equal(symptomTime('joint_discomfort').querySelector('[aria-label="时间精度"]').value, 'unknown');
  await advanceAutosave(); assert.equal(test.server.draftWrites, 1); assert.equal(test.server.requests.length, 0);
  const converted = structuredClone(test.server.draft.payload);
  assert.deepEqual(converted.symptomTimes, { rash: payload.observedTime, fever: payload.observedTime });
  assert.deepEqual(converted.inheritedSymptomTimes, ['rash', 'fever']);
  assert.equal(Object.hasOwn(converted, 'voiceApplied'), false);
  await editSymptomTime('rash', 'date', '2026-10-02');
  assert.equal(symptomTime('rash').textContent.includes('时间沿用转换前的共用时间'), false);
  assert.equal(symptomTime('fever').querySelector('input').value, payload.observedTime.value);
  assert.equal(input('补充说明发生时间').value, payload.observedTime.value);
  await act(async () => test.router.navigate('/records')); await flush();
  const dialog = document.querySelector('dialog'); assert.ok(dialog);
  await nativeClick([...dialog.querySelectorAll('button')].find(node => node.textContent === '继续编辑'));
  assert.equal(test.router.state.location.pathname, '/register/observations');
  assert.equal(document.querySelector('dialog'), null, 'native continue-editing action dismisses the dialog');
  assert.equal(symptomTime('rash').querySelector('input').value, '2026-10-02');
  await test.unmount();
  const restored = await mount('observation', { payload: converted });
  await advanceAutosave(); assert.equal(restored.server.draftWrites, 0);
  assert.ok(symptomTime('rash').textContent.includes('时间沿用转换前的共用时间'));
  assert.deepEqual(restored.server.draft.payload, converted);
  await restored.unmount();
  pass('explicit conversion imports only selected legacy symptoms, persists lightweight provenance, preserves isolated edits/cancel and restores without auto-migration');
}
{
  const test = await mount('observation', { lose: true });
  for (const code of ['fever', 'rash', 'joint_discomfort', 'other_symptom']) assert.equal(symptomTime(code).querySelector('[aria-label="时间精度"]').value, 'unknown');
  await click(shadow().querySelector('[aria-label="发热表现"] button'));
  await click(shadow().querySelector('[aria-label="皮疹"] button:nth-child(3)'));
  await click(shadow().querySelector('[aria-label="关节疼痛或肿胀"] button:nth-child(2)'));
  await editSymptomTime('fever', 'minute', '2026-10-02T00:15', 'Asia/Shanghai');
  await editSymptomTime('rash', 'date', '2026-09-30');
  await editSymptomTime('joint_discomfort', 'month', '2026-08');
  await editSymptomTime('other_symptom', 'date', '2026-07-01');
  const submitButton = button('确认并保存');
  await act(async () => { Simulate.click(submitButton); Simulate.click(submitButton); }); await flush();
  assert.equal(test.server.requests.length, 1, 'double click uses one frozen submission');
  assert.equal(symptomTime('fever').querySelector('input').disabled, true);
  await click(button('重试本次保存'));
  assert.deepEqual(test.server.requests[0], test.server.requests[1]);
  assert.equal(test.server.committed.size, 1);
  const fields = byCode(test.server.requests[0]);
  assert.equal(Object.keys(fields).length, 3, 'time input alone does not select an untouched symptom');
  assert.equal(fields.fever.medical_time.local_datetime, '2026-10-02T00:15');
  assert.equal(fields.fever.medical_time.timezone_name, 'Asia/Shanghai');
  assert.equal(fields.rash.value_state, 'unknown'); assert.equal(fields.rash.medical_time.local_date, '2026-09-30');
  assert.equal(fields.joint_discomfort.medical_time.local_month, 8); assert.equal(fields.joint_discomfort.medical_time.precision, 'month');
  await test.unmount();
  pass('new forms keep every configured symptom independent, mixing dates/months/unknown state without selecting untouched symptoms; double-click/lost-response retry stays frozen');
}
{
  const payload = { ...structuredClone(legacyObservation), symptoms: { fever: 'yes', rash: 'no', joint_discomfort: '' },
    symptomTimes: { fever: { precision: 'minute', value: '2026-10-02T00:15', timezone: 'Asia/Tokyo' },
      rash: { precision: 'second', value: '2026-09-30T21:04:09', timezone: 'America/New_York' }, joint_discomfort: emptyTime } };
  const test = await mount('observation', { payload, ui2: false });
  assert.equal(shadow().getAttribute('data-screen-id'), 'S04');
  await advanceAutosave(); assert.equal(test.server.draftWrites, 0);
  await click(button('保存本次记录'));
  const fields = byCode(test.server.requests[0]);
  for (const code of Object.keys(payload.symptoms)) assert.deepEqual(fields[code].medical_time, timePayload(payload.symptomTimes[code]));
  assert.deepEqual(fields.observation_note.medical_time, timePayload(payload.observedTime));
  await test.unmount();
  pass('legacy page rendering also restores and submits per-symptom mixed dates/timezones/second/unknown precision independently');
}
{
  const payload = { ...structuredClone(legacyObservation), observedTime: { precision: 'month', value: '', timezone: '' } };
  const test = await mount('observation', { payload });
  assert.equal(input('原草稿共用发生时间').value, '');
  assert.equal(input('原草稿共用发生时间').disabled, false);
  await change(sharedConfirmation(), true); assert.equal(button('确认并保存').disabled, true);
  await advanceAutosave(); assert.equal(test.server.draftWrites, 0); assert.deepEqual(test.server.draft.payload, payload);
  await test.unmount();
  pass('incomplete but structurally valid legacy time restores for editing without guessing, autosaving or allowing incomplete submission');
}
{
  const test = await mount('observation', { payload: structuredClone(legacyObservation) });
  assert.equal(input('补充说明').disabled, false);
  await change(input('补充说明'), '合成草稿 A 尚未保存的修改');
  await act(async () => test.router.navigate(`/register/observations?draft=${randomUUID()}`)); await flush();
  assert.equal(input('补充说明').disabled, true, 'failed draft B read must not unlock the retained draft A form');
  assert.equal(button('确认并保存').disabled, true); assert.equal(button('保存为草稿').disabled, true);
  assert.ok(button('重新读取草稿'));
  await advanceAutosave(); assert.equal(test.server.draftWrites, 0); assert.equal(test.server.requests.length, 0);
  assert.deepEqual(test.server.draft.payload, legacyObservation);
  await act(async () => test.router.navigate('/records')); await flush();
  const dialog = document.querySelector('dialog'); assert.ok(dialog);
  await nativeClick([...dialog.querySelectorAll('button')].find(node => node.textContent === '保存草稿后离开'));
  assert.equal(test.server.draftWrites, 0, 'the hook-owned leave dialog cannot save retained draft A under target B');
  assert.equal(test.router.state.location.pathname, '/register/observations');
  assert.ok(dialog.textContent.includes('草稿尚未保存成功'));
  await test.unmount();
  pass('same-route draft A to unreadable draft B never uses retained A for editing, autosave, submission or save-and-leave');
}
{
  const test = await mount('observation');
  await change(input('补充说明'), '合成新草稿第一次输入'); await advanceAutosave();
  assert.equal(test.server.draftWrites, 1); assert.equal(test.server.draft.version, '1');
  assert.equal(new URLSearchParams(test.router.state.location.search).get('draft'), test.server.draftId);
  await change(input('补充说明'), '合成新草稿第二次输入'); await advanceAutosave();
  assert.equal(test.server.draftWrites, 2); assert.equal(test.server.draft.version, '2');
  assert.equal(test.server.draft.payload.description, '合成新草稿第二次输入');
  assert.equal(test.server.requests.length, 0);
  await test.unmount();
  pass('new observation first-save URL binding still permits a second draft save against the same draft ID');
}
for (const options of [{ payload: { ...legacyObservation, symptoms: [] } }, { payload: structuredClone(legacyObservation), failDraftRead: true }]) {
  const test = await mount('observation', options);
  assert.equal(input('补充说明').disabled, true); assert.equal(button('确认并保存').disabled, true);
  assert.equal(button('保存为草稿').disabled, true); assert.ok(button('重新读取草稿'));
  await advanceAutosave(); assert.equal(test.server.draftWrites, 0); assert.equal(test.server.requests.length, 0);
  await click(button('重新读取草稿'));
  if (options.failDraftRead) { assert.equal(input('补充说明').disabled, false); assert.ok(sharedConfirmation()); }
  else assert.equal(input('补充说明').disabled, true);
  await test.unmount();
}
pass('malformed or unreadable observation draft cannot silently become a new form, save or submit; explicit read retry remains available');

// API-level nested mutation and duplicate click regression.
{
  const server = syntheticServer({ kind: 'therapy', lose: true });
  const payload = { kind: 'therapy', fields: [{ raw_text: '合成A' }], draft_version: '1' };
  const submission = createRecordSubmission(patientId, payload);
  const first = submission.submit(), duplicate = submission.submit();
  assert.equal(first, duplicate);
  await Promise.allSettled([first, duplicate]);
  payload.fields[0].raw_text = '合成B'; payload.draft_version = '2';
  await submission.submit(); await submission.submit();
  assert.equal(server.requests.length, 2);
  assert.deepEqual(server.requests[0], server.requests[1]);
  await createRecordSubmission(patientId, { kind: 'therapy', fields: [{ raw_text: '合成A' }], draft_version: '1' }).submit();
  assert.equal(server.committed.size, 2);
  assert.notEqual(server.requests[0].key, server.requests[2].key);
  clearPendingRecordRequests();
  await assert.rejects(submission.submit(), /登录身份或当前患儿已改变/);
  pass('frozen payload, duplicate clicks, lost response retry, success cache, new identical action, logout invalidation');
}
{
  const test = await mount('therapy', { payload: therapyPayload });
  const adopt = index => shadow().querySelector(`[aria-label="治疗候选 ${index}"] button`);
  await click(adopt(2));
  assert.equal(input('原药名').value, '合成药物B');
  for (const label of labels) assert.equal(input(label).value, '');
  await change(input('原文频次'), '合成B稀疏频次');
  await click(adopt(1));
  assert.equal(input('原药名').value, '合成药物A');
  for (const label of labels) assert.equal(input(label).value, '');
  await change(shadow().querySelector('input[type="checkbox"]'), true);
  await click(button('确认并保存资料'));
  assert.equal(test.router.state.location.pathname, '/records');
  for (const code of codes) assert.equal(test.server.requests[0].body.fields.find(field => field.field_code === code).value_state, 'unknown');
  await test.unmount();
  pass('actual therapy A→B→A resets all seven details and sparse edits; submitted values remain unknown');
}
{
  const test = await mount('therapy', { payload: therapyPayload });
  await click(shadow().querySelector('[aria-label="移除 synthetic.png"]'));
  assert.equal(input('原药名').value, '');
  for (const label of labels) assert.equal(input(label).value, '');
  assert.equal(input('实际使用状态').value, '无法确定');
  await test.unmount(); pass('removing the adopted source clears all per-drug fields');
}
for (const kind of ['observation', 'therapy', 'report']) {
  const test = await mount(kind, { payload: kind === 'therapy' ? therapyPayload : kind === 'report' ? reportPayload : undefined, lose: true });
  if (kind === 'observation') await change(input('补充说明'), '合成观察内容');
  else await change(shadow().querySelector('input[type="checkbox"]'), true);
  await click(button(kind === 'therapy' ? '确认并保存资料' : '确认并保存'));
  assert.ok(shadow().textContent.includes('尚未确认是否保存成功'));
  assert.ok(button('重试本次保存'));
  const edit = input(kind === 'observation' ? '补充说明' : kind === 'therapy' ? '原药名' : '原项目名称');
  assert.equal(edit.matches(':disabled'), true);
  const unload = new Event('beforeunload', { cancelable: true }); window.dispatchEvent(unload);
  assert.equal(unload.defaultPrevented, true, 'unknown outcome must protect refresh even if a draft was already fully saved');
  const writes = test.server.draftWrites;
  await advanceAutosave();
  assert.equal(test.server.draftWrites, writes);
  if (kind === 'observation') {
    assert.equal(writes, 0);
    await act(async () => test.access.refresh()); await flush();
    assert.ok(button('重试本次保存'), 'same-identity refresh preserves the pending intent');
  }
  if (kind === 'report') {
    await act(async () => test.router.navigate('/register/reports')); await flush();
    assert.ok(button('重试本次保存'), 'report photo step still offers recovery after back navigation');
  }
  await click(button('重试本次保存'));
  assert.equal(test.router.state.location.pathname, '/records');
  assert.equal(test.server.requests.length, 2);
  assert.deepEqual(test.server.requests[0], test.server.requests[1]);
  assert.equal(test.server.committed.size, 1);
  await test.unmount();
  pass(`actual ${kind}: committed response lost, 11-second autosave timer, same-key retry, one record`);
}
{
  const test = await mount('observation', { reject: true });
  await change(input('补充说明'), '合成输入一'); await click(button('确认并保存'));
  assert.ok(shadow().textContent.includes('合成字段校验失败'));
  assert.equal(input('补充说明').matches(':disabled'), false);
  await change(input('补充说明'), '合成输入二'); await click(button('确认并保存'));
  assert.equal(test.router.state.location.pathname, '/records');
  assert.notEqual(test.server.requests[0].key, test.server.requests[1].key);
  assert.equal(test.server.committed.size, 1);
  await test.unmount(); pass('definitive 422 rejection unlocks editing for corrected submission');
}
{
  const test = await mount('observation', { delayDraft: true });
  await change(input('补充说明'), '合成自动草稿'); await advanceAutosave();
  assert.equal(test.server.draftWrites, 1);
  assert.equal(button('确认并保存').disabled, true);
  await act(async () => test.server.releaseDraft()); await flush();
  assert.equal(button('确认并保存').disabled, false);
  await click(button('确认并保存'));
  assert.equal(test.server.requests[0].body.draft_id, test.server.draftId);
  assert.equal(test.server.requests[0].body.draft_version, '1');
  await test.unmount(); pass('in-flight autosave prevents submit and then supplies the exact saved draft version');
}
// Expiry recovery uses the exact server intent query, never a blind fresh key.
for (const queryState of ['prepared', 'unknown', 'unavailable', 'committed']) {
  let now = 0, dropped = false;
  const intents = new Map(), records = new Map(), attempts = [], queries = [];
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    if (path === '/api/write-intents') {
      if (!intents.has(body.request_id)) intents.set(body.request_id, { id: randomUUID(), requestId: body.request_id, payload: body.payload, generation: '1', expires: now + 300000 });
      return Response.json(syntheticIntent(intents.get(body.request_id).id, body.request_id, intents.get(body.request_id).generation ?? '1'));
    }
    if (path.startsWith('/api/requests/')) {
      queries.push(path);
      if (queryState === 'unavailable') return Response.json({ code: 'AUTH_REQUIRED', message: 'Synthetic authorization required' }, { status: 401 });
      const intent = [...intents.values()].find(item => path.endsWith(item.id));
      return Response.json({ patient_id: patientId, intent_id: intent.id, request_id: intent.requestId, operation_id: 'record_create', target_key: `/api/patients/${patientId}/records`, status: queryState, resource_id: null, receipt: null, may_retry: queryState === 'prepared', generation: '1', prepared_at: '2026-10-11T00:00:00Z', expires_at: fixtureTime, record_input: intent.payload });
    }
    if (path.endsWith('/retry')) {
      const intent = [...intents.values()].find(item => path.includes(item.id));
      assert.ok(intent); assert.equal(body.expected_generation, '1'); intent.expires = now + 300000; intent.generation = '2';
      return Response.json(syntheticIntent(intent.id, intent.requestId, '2'));
    }
    const key = options.headers['Idempotency-Key']; attempts.push({ key, body });
    if (!dropped) {
      dropped = true;
      if (queryState === 'committed') records.set(key, { patient_id: patientId, record_id: randomUUID(), record_version_id: randomUUID(), version: 1 });
      throw new TypeError('Synthetic response lost');
    }
    if (records.has(key)) return Response.json(records.get(key));
    if (intents.get(key).expires <= now) return Response.json({ code: 'WRITE_INTENT_EXPIRED', message: 'Synthetic expired intent' }, { status: 409 });
    records.set(key, { patient_id: patientId, record_id: randomUUID(), record_version_id: randomUUID(), version: 1 }); return Response.json(records.get(key));
  };
  const submission = createRecordSubmission(patientId, { kind: 'observation', fields: [{ raw_text: 'TTL合成数据' }] });
  await assert.rejects(submission.submit());
  now = 301000;
  if (queryState === 'prepared' || queryState === 'committed') {
    await submission.submit(); assert.equal(records.size, 1);
    if (queryState === 'prepared') {
      assert.equal(queries.length, 1); assert.equal(attempts.length, 3);
      assert.equal(attempts[0].key, attempts[1].key); assert.equal(attempts[1].key, attempts[2].key);
      assert.deepEqual(attempts[0].body, attempts[2].body);
    } else { assert.equal(queries.length, 0); assert.equal(attempts[0].key, attempts[1].key); }
  } else {
    await assert.rejects(submission.submit()); assert.equal(submission.uncertain, true);
    assert.equal(intents.size, 1); assert.equal(records.size, 0);
  }
}
pass('301-second TTL: proven expired/uncommitted intent renews once; lost committed result retains key; unknown/failed query never renews');

for (const switchKind of ['patient', 'account', 'unmount']) {
  const test = await mount('observation', { lose: true });
  await change(input('补充说明'), '合成切换测试'); await click(button('确认并保存'));
  assert.ok(button('重试本次保存'));
  const oldSignal = test.server.signals[0];
  if (switchKind === 'patient') await act(async () => test.access.selectPatient(secondPatientId));
  else if (switchKind === 'account') { test.server.setPrincipal(secondPatientId); await act(async () => test.access.refresh()); }
  else await test.unmount();
  await flush();
  assert.equal(oldSignal.aborted, true);
  if (switchKind !== 'unmount') {
    assert.equal(input('补充说明').value, ''); assert.equal(button('重试本次保存'), undefined);
    assert.equal(input('补充说明').matches(':disabled'), false);
    await advanceAutosave(); assert.equal(test.server.requests.length, 1);
    await test.unmount();
  }
}
pass('actual patient switch, account refresh/switch, and navigation unmount cancel the old submission; no old-patient retry survives');

// An identity change between intent preparation and record POST must stop the POST.
{
  let current = true, resume, recordPosts = 0;
  globalThis.fetch = async (path, options) => {
    if (path === '/api/write-intents') { await new Promise(resolve => { resume = resolve; }); return Response.json(syntheticIntent(randomUUID(), JSON.parse(options.body).request_id)); }
    recordPosts += 1; return Response.json({ record_id: 'unexpected' });
  };
  const submission = createRecordSubmission(patientId, { kind: 'observation' }, () => current);
  const started = submission.submit(); await new Promise(resolve => setTimeout(resolve, 0));
  current = false; resume(); await assert.rejects(started);
  assert.equal(recordPosts, 0);
  await assert.rejects(submission.submit()); assert.equal(recordPosts, 0);
}
pass('identity change while intent preparation is in flight prevents a subsequent record POST');

// V3 candidate times remain review-only until distinct user adoption actions.
{
  const month = { raw_text: 'SYNTHETIC February 2026', precision: 'month', local_year: 2026, local_month: 2,
    end_year: null, end_month: null, relative_text: null, local_date: null, local_datetime: null,
    timezone_name: null, utc_offset_minutes: null, instant: null, resolution_status: 'local_only' };
  const minute = { ...month, raw_text: 'SYNTHETIC 2026-02-03 12:34 +08:00', precision: 'minute',
    local_year: null, local_month: null, local_datetime: '2026-02-03T12:34', utc_offset_minutes: 480 };
  const payload = structuredClone(reportPayload);
  payload.photos = [{ file_id: photoId, name: 'synthetic.png' }];
  payload.candidates[0] = { ...payload.candidates[0], photo_id: photoId, source_text: month.raw_text,
    timeCandidates: { item: month, reportSample: minute, reportResult: null, sourceText: minute.raw_text } };
  const test = await mount('report', { payload });
  assert.equal(test.server.requests.length, 0);
  assert.equal(input('项目 1 采样时间').closest('label').nextElementSibling.querySelector('select').value, 'unknown');
  assert.equal(input('报告记载的采样时间').closest('label').nextElementSibling.querySelector('select').value, 'unknown');
  await click(button('采用报告采样时间候选'));
  assert.equal(input('项目 1 采样时间').closest('label').nextElementSibling.querySelector('select').value, 'unknown', 'report time must not overwrite item time');
  await click(button('采用项目 1 采样时间候选'));
  const confirm = [...shadow().querySelectorAll('input[type="checkbox"]')].find(node => node.closest('label')?.textContent.includes('已对照原图'));
  await change(confirm, true); await click(button('确认并保存'));
  assert.equal(test.server.requests.length, 1);
  assert.deepEqual(test.server.requests[0].body.fields[0].medical_time, month);
  assert.deepEqual(test.server.requests[0].body.report.sample_time, minute);
  assert.equal(test.server.requests[0].body.report.result_time, null);
  await test.unmount();
}
pass('report v3 time candidates require explicit per-scope adoption and preserve precision, source quote and UTC offset in the posted record');

// Source-aware acknowledgment never invents a photograph or professional review.
{
  const test = await mount('report', { payload: structuredClone(reportPayload) });
  const confirm = shadow().querySelector('.registration-confirmation input');
  assert.ok(confirm.closest('label').textContent.includes('已逐项核对手动填写内容'));
  assert.ok(!confirm.closest('label').textContent.includes('已对照原图'));
  assert.ok(shadow().textContent.includes('不声称已经核对原图'));
  assert.ok(button('确认并保存').disabled, 'explicit acknowledgment still required');
  await change(confirm, true); await click(button('确认并保存'));
  assert.equal(test.server.requests.length, 1);
  assert.equal(test.server.requests[0].body.capture_method, 'manual');
  assert.equal(test.server.requests[0].body.fields[0].source_file_id, null);
  assert.deepEqual(test.server.requests[0].body.file_ids, []);
  await test.unmount();
}
pass('manual report without item images requires truthful explicit acknowledgment and retains manual/no-image payload');
{
  const payload = structuredClone(reportPayload);
  payload.photos = [{ file_id: photoId, name: 'synthetic.png' }];
  payload.candidates[0] = { ...payload.candidates[0], photo_id: photoId, source_text: '' };
  const test = await mount('report', { payload });
  const confirm = shadow().querySelector('.registration-confirmation input');
  assert.ok(confirm.disabled); assert.ok(confirm.closest('label').textContent.includes('原图来源不完整'));
  assert.ok(button('确认并保存').disabled); assert.equal(test.server.requests.length, 0);
  await test.unmount();
}
pass('image candidate missing source quote cannot be acknowledged or submitted');
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_REACT_DOM', checks }, null, 2));
dom.window.close();
