// Actual record-list route with synthetic access and HTTP responses. No browser or backend.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { registerHooks } from 'node:module';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (url.endsWith('/state/access.tsx')) return { format: 'module', source: `import {useSyncExternalStore} from 'react'; export function useAccess(){return useSyncExternalStore(globalThis.__recordsSubscribe,()=>globalThis.__recordsAccess);}`, shortCircuit: true };
  if (/\.(png|jpg|webp|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/records', pretendToBeVisual: true });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Element', 'Node', 'ShadowRoot']) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
HTMLElement.prototype.getClientRects = function () { return [{ x: 0, y: 0, width: 100, height: 30 }]; };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { UI2RecordsPage } = await import('../src/features/records/UI2RecordsPage.tsx');
const { navigationMemory } = await import('../src/state/navigation.ts');
const { fieldValue, timeLabel } = await import('../src/api/records.ts');
const { validateRecordSearchPage } = await import('../src/api/recordSearchSchema.ts');
const patient = randomUUID(), otherPatient = randomUUID(), principal = randomUUID();
const timestamp = '2026-10-07T10:01:00Z';
const time = { precision: 'minute', raw_text: '原报告：2026年10月7日18:00（保留完整时间）', local_date: null, local_datetime: '2026-10-07T18:00:00', instant: '2026-10-07T10:00:00Z', timezone_name: 'Asia/Shanghai', resolution_status: 'resolved', local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null, utc_offset_minutes: 480 };
const field = (code, changes = {}) => ({ field_id: randomUUID(), field_code: code, concept_code: null, original_name: null, value_type: 'decimal', value_state: 'known', raw_numeric: '9007199254740993.0000', raw_unit: '原单位 / 保留完整很长单位', raw_text: null, categorical_value: null, qualifier: 'ge', medical_time_id: randomUUID(), medical_time: time, sample_time_origin: 'observation', method: null, reference_interval: null, report_flag: null, verification_states: [], confirmation_id: null, source_anchor: null, ...changes });
function record(kind, fields, changes = {}) {
  const id = randomUUID();
  return { record_id: id, kind, status: 'active', current_version: 1, created_at: timestamp, detail: { patient_id: patient, record_id: id, record_version_id: randomUUID(), kind, version: 1, origin_type: 'self_report', capture_method: 'manual', quality_status: 'valid', source_organization: '合成机构原文（仅测试）', correction_reason: null, corrected_at: null, recorded_at: timestamp, fields, attachments: [], report: null, fact_refs: fields.map(f => ({ field_id: f.field_id, fact_id: randomUUID(), version_id: randomUUID(), version: '1' })), allowed_actions: [], ...changes } };
}
const observation = record('observation', [field('temp'), field('rash', { value_type: 'categorical', raw_numeric: null, raw_unit: null, categorical_value: 'no', qualifier: null })]);
const unknown = record('observation', [field('temp', { value_state: 'unknown', raw_numeric: null, raw_unit: null, qualifier: null, medical_time_id: null, medical_time: null })]);
const report = record('report', Array.from({ length: 8 }, (_, i) => field(`lab_${i}`, { original_name: `原项目${i}`, raw_numeric: `${i}.000`, raw_unit: 'mg/L', qualifier: 'eq' })), { origin_type: 'report_transcription', report: { external_ref: null, report_status: 'final', sample_time: time, result_time: null } });
const therapy = record('therapy', [field('therapy_status', { value_type: 'categorical', raw_numeric: null, raw_unit: null, categorical_value: 'ended', qualifier: null })]);
const fixtures = [unknown, therapy, report, observation];
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
freeze(fixtures); const fixtureSnapshot = JSON.stringify(fixtures);
validateRecordSearchPage({ records: fixtures, next_cursor: null });
const listeners = new Set(); globalThis.__recordsSubscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };
const initialAccess = () => ({ principalId: principal, patientId: patient, loading: false, error: null, relations: [{ patient_id: patient, display_id: '合成患儿原标识（完整长名字）' }], refresh: async () => {} });
let root, router, requests = [], responder;
const page = () => document.querySelector('[data-screen-id]')?.shadowRoot ?? document;
const text = () => page().textContent;
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
const button = label => [...page().querySelectorAll('button')].find(node => node.textContent.trim() === label || node.querySelector('strong')?.textContent === label);
const click = async node => { assert.ok(node); assert.equal(node.disabled ?? false, false); await act(async () => node.click()); await flush(); };
const rows = () => [...page().querySelectorAll('.rich-record-card')];
const row = item => rows().find(node => node.querySelector('a')?.getAttribute('href') === `/records/${item.record_id}`);
const reply = (records, next_cursor = null) => Response.json({ records, next_cursor });
const defaultResponder = (_patientId, query) => reply(fixtures.filter(item => !query.kind || item.kind === query.kind));
globalThis.fetch = async (path, options = {}) => {
  const match = /^\/api\/patients\/([^/]+)\/records\/search$/.exec(String(path)); assert.ok(match, `Unexpected synthetic request ${path}`); assert.equal(options.method, 'POST');
  const query = JSON.parse(options.body); requests.push({ patientId: match[1], query, signal: options.signal });
  return responder(match[1], query, options);
};
async function mount(response = defaultResponder) {
  navigationMemory.clear(); requests = []; responder = response; globalThis.__recordsAccess = initialAccess();
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ path: '/records', element: h(UI2RecordsPage) }, { path: '*', element: h('p', null, 'Synthetic destination') }], { initialEntries: ['/records'] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
}
async function unmount() { await act(async () => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
async function input(node, value) { assert.ok(node); await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(node, value); node.dispatchEvent(new window.Event('input', { bubbles: true, composed: true })); node.dispatchEvent(new window.Event('change', { bubbles: true, composed: true })); }); }
async function access(changes) { await act(async () => { globalThis.__recordsAccess = { ...globalThis.__recordsAccess, ...changes }; listeners.forEach(fn => fn()); }); await flush(); }
try {
  await mount(); assert.equal(rows().length, 4); assert.equal(requests.length, 1);
  assert.deepEqual([...page().querySelectorAll('.feature-grid strong')].map(n => n.textContent), ['就医摘要', '我的草稿']);
  for (const label of ['长期档案', '观察时段', '就医摘要', '我的草稿']) assert.equal([...page().querySelectorAll('button')].filter(n => n.querySelector('strong')?.textContent === label).length, 1);
  assert.deepEqual([...page().querySelectorAll('.rich-record-group h2')].map(n => n.textContent), ['已有医学时间', '时间待补充']);
  assert.deepEqual(rows().map(n => n.querySelector('a').getAttribute('href')), [therapy, report, observation, unknown].map(item => `/records/${item.record_id}`), 'existing known/unknown grouping and input order are retained');
  assert.equal(row(observation).querySelector('.rich-record-value').textContent, fieldValue(observation.detail.fields[0]));
  assert.equal(row(observation).querySelector('.rich-record-unit').textContent, ` ${observation.detail.fields[0].raw_unit}`);
  assert.equal(row(therapy).querySelector('.rich-record-value').textContent, '已结束'); assert.equal(row(therapy).querySelector('.rich-record-unit'), null);
  assert.equal(row(unknown).querySelector('.rich-record-value').textContent, '无法确定'); assert.equal(row(unknown).querySelector('.rich-record-unit'), null);
  assert.equal(row(report).querySelector('.rich-record-value').textContent, '8 项结果'); assert.ok(row(report).textContent.includes('另有 5 项'));
  assert.ok(row(report).textContent.includes(`报告采样时间 · ${timeLabel(time)}`));
  assert.ok(row(observation).textContent.includes(`体温时间 · ${timeLabel(time)}`)); assert.ok(row(observation).textContent.includes('合成机构原文（仅测试）')); assert.ok(row(observation).textContent.includes('未核对'));
  assert.ok(text().includes('没有新增资料，不代表当前健康状况正常。')); assert.ok(!text().includes('母亲记录')); assert.ok(!text().includes('2 份等待继续'));
  const filter = button('筛选'); assert.equal(filter.getAttribute('aria-expanded'), 'false'); const filterId = filter.getAttribute('aria-controls'); assert.ok(filterId); await click(filter);
  assert.equal(button('筛选').getAttribute('aria-expanded'), 'true'); assert.equal(page().getElementById(filterId).getAttribute('aria-label'), '筛选记录');
  assert.equal(requests.length, 1, 'opening filter is not a new query');
  await input(page().querySelector('input[placeholder="输入医院名称"]'), '合成医院输入');
  const inputs = page().querySelectorAll('input[type=date]'); await input(inputs[0], '2026-10-10'); await input(inputs[1], '2026-10-01');
  assert.ok(text().includes('结束日期不能早于开始日期')); const beforeInvalid = requests.length;
  await act(async () => inputs[1].dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, composed: true }))); await flush(); assert.equal(requests.length, beforeInvalid);
  await input(page().querySelectorAll('input[type=date]')[1], '2026-10-12');
  await act(async () => page().querySelectorAll('input[type=date]')[1].dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, composed: true }))); await flush();
  assert.equal(requests.at(-1).query.source_organization, '合成医院输入'); assert.equal(requests.at(-1).query.from_date, '2026-10-10'); assert.equal(requests.at(-1).query.to_date, '2026-10-12');
  await click(button('清除筛选')); assert.equal(requests.at(-1).query.source_organization, undefined); assert.equal(requests.at(-1).query.from_date, undefined); assert.equal(button('筛选').getAttribute('aria-expanded'), 'false');
  await click([...page().querySelectorAll('[role=tab]')].find(n => n.textContent === '检验报告')); assert.equal(requests.at(-1).query.kind, 'report'); assert.equal(rows().length, 1);
  const link = row(report).querySelector('a'), beforeLocation = router.state.location;
  page().querySelector('.phone-scroll').scrollTop = 123;
  await act(async () => page().querySelector('.phone-scroll').dispatchEvent(new window.Event('scroll')));
  await click(link); assert.equal(router.state.location.pathname, `/records/${report.record_id}`);
  const context = navigationMemory.readReturnContext(router.state.location.state.returnContextKey); assert.equal(context.pathname, '/records'); assert.equal(context.key, beforeLocation.key); assert.equal(context.scroll, 123);
  await act(async () => router.navigate(-1)); await flush(); assert.equal(router.state.location.pathname, '/records'); assert.equal(page().querySelector('[aria-selected=true]').textContent, '检验报告'); assert.equal(rows().length, 1); await unmount();
  for (const [label, target] of [['就医摘要', '/summaries'], ['我的草稿', '/drafts'], ['长期档案', '/archives'], ['观察时段', '/records/period']]) { await mount(); await click(button(label)); assert.equal(router.state.location.pathname, target); await unmount(); }
  let releaseMore; const delayedMore = new Promise(resolve => { releaseMore = resolve; });
  await mount((_p, query) => query.cursor ? delayedMore : query.kind ? reply([report]) : reply([observation], 'next-page'));
  const more = button('读取更多记录'); await act(async () => { more.click(); more.click(); }); await flush();
  assert.equal(requests.filter(x => x.query.cursor).length, 1, 'duplicate load-more clicks share the existing in-flight guard');
  const moreRequest = requests.at(-1); await click([...page().querySelectorAll('[role=tab]')].find(n => n.textContent === '检验报告')); assert.equal(moreRequest.signal.aborted, true);
  await act(async () => releaseMore(reply([observation, unknown]))); await flush(); assert.equal(rows().length, 1); assert.equal(row(report)?.querySelector('.rich-record-value').textContent, '8 项结果'); assert.equal(row(observation), undefined); await unmount();
  let releaseOld; const oldRead = new Promise(resolve => { releaseOld = resolve; });
  const other = record('observation', [field('temp', { raw_numeric: '36.222', raw_unit: 'C', qualifier: 'eq' })], { patient_id: otherPatient });
  await mount(p => p === patient ? oldRead : reply([other])); const initialRequest = requests[0];
  await access({ patientId: otherPatient, relations: [{ patient_id: otherPatient, display_id: '另一合成患儿' }] }); assert.equal(initialRequest.signal.aborted, true); assert.ok(text().includes('36.222 C'));
  await act(async () => releaseOld(reply([observation]))); await flush(); assert.ok(!text().includes('9007199254740993.0000')); assert.equal(rows().length, 1);
  await access({ patientId: null }); assert.equal(rows().length, 0); assert.ok(!text().includes('36.222')); await unmount();
  await mount(() => Response.json({ code: 'CASE_ACTION_DENIED', message: 'Synthetic denied', retryable: false, recovery: 'none', field_errors: [] }, { status: 403 })); assert.equal(rows().length, 0); assert.ok(text().includes('当前访问关系需要核验')); await unmount();
  await mount(() => reply([])); assert.ok(text().includes('记录从这里开始')); await click([...page().querySelectorAll('[role=tab]')].find(n => n.textContent === '检验报告')); assert.ok(text().includes('没有符合筛选条件的记录')); await unmount();
  assert.equal(JSON.stringify(fixtures), fixtureSnapshot);
  console.log('PASS S07 actual list DOM: original tool placement with unchanged destinations; exact raw values/units/report count; full source/time/status; unchanged known/unknown order; filter inputs/date validation/clear; memory-router return context; guarded duplicate pagination and stale response; cross-patient and revoked-access hiding; distinct empty states. Synthetic HTTP only, no browser/backend/visual proof.');
} finally { if (root?._internalRoot) await unmount(); dom.window.close(); }
