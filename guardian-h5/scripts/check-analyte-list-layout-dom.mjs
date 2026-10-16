// Actual S09 route/resource/schema, synthetic HTTP only. No browser or backend.
// Native details focus/activation structure is checked, not real keyboard defaults.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === 'chart.js/auto') return { url: 'data:text/javascript,export default class Chart { constructor(canvas, config) { globalThis.__analyteCharts.push({canvas, config}); } destroy() {} }', shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.endsWith('/state/access.tsx')) return { format: 'module', source: `import {useSyncExternalStore} from 'react'; export function useAccess(){return useSyncExternalStore(globalThis.__analyteSubscribe,()=>globalThis.__analyteAccess);}`, shortCircuit: true };
    if (url.endsWith('/AnalytePage.css?inline')) return { format: 'module', source: `export default ${JSON.stringify(readFileSync(fileURLToPath(new URL(url)), 'utf8'))};`, shortCircuit: true };
    if (/\.(png|jpg|webp|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
    return nextLoad(url, context);
  },
});
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/records/analytes/synthetic-lab', pretendToBeVisual: true });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Element', 'Node', 'ShadowRoot']) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
HTMLElement.prototype.getClientRects = function () { return [{ x: 0, y: 0, width: 100, height: 30 }]; };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => '#123456' });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.__analyteCharts = [];
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { AnalytePage } = await import('../src/features/records/AnalytePage.tsx');
const { medicalSourceTimeLabel } = await import('../src/components/recordTimePresentation.ts');
const { originLabels } = await import('../src/api/records.ts');
const { validateAnalyteRead } = await import('../src/api/recordSearchSchema.ts');
const { navigationMemory } = await import('../src/state/navigation.ts');
const patient = randomUUID(), otherPatient = randomUUID(), principal = randomUUID(), concept = 'synthetic-lab';
const time = { precision: 'minute', raw_text: '原报告时间完整原文：2026年10月7日18时00分', local_date: null, local_datetime: '2026-10-07T18:00:00', instant: '2026-10-07T10:00:00Z', timezone_name: 'Asia/Shanghai', resolution_status: 'resolved', local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null, utc_offset_minutes: 480 };
const item = (changes = {}) => ({ field_id: randomUUID(), record_version_id: randomUUID(), original_name: '合成原项目名称（完整保留）', value_state: 'known', raw_numeric: '9007199254740993.0000000000000001', raw_text: '原报告附带原文，数值存在时仍完整可展开', qualifier: 'eq', raw_unit: '原单位 / 完整保留很长单位名称', method: '原检测方法完整文字', medical_time_id: randomUUID(), medical_time: time, direction: null, comparison: null, not_comparable_reason: null, comparison_reasons: [], origin_type: 'report_transcription', source_organization: '合成来源机构（完整原文）', verification_states: [{ purpose: 'synthetic', status: 'pending', version: '1' }, { purpose: 'synthetic-other', status: 'verified', version: '2' }], ...changes });
const fixtures = [
  item(),
  item({ raw_numeric: '0.00000000000000000000000009', qualifier: 'le', comparison_reasons: ['qualified_value', 'method_mismatch'], medical_time: { ...time, raw_text: '较早的原报告日期，保持服务器顺序', local_datetime: '2026-09-01T18:00:00', instant: '2026-09-01T10:00:00Z' } }),
  item({ raw_numeric: null, raw_text: '完整原始字符串 / 未转为数值 / 不截断也不替换其中符号 ≤ > = 000.0100', qualifier: null, raw_unit: null, comparison_reasons: ['value_unknown', 'unit_unknown'] }),
  item({ value_state: 'unknown', raw_numeric: null, raw_text: null, qualifier: null, raw_unit: null, method: null, medical_time_id: null, medical_time: null, comparison_reasons: ['value_unknown', 'unit_unknown', 'method_unknown', 'time_unknown'], verification_states: [] }),
  item({ value_state: 'invalid', raw_numeric: '-0.00000000000000003000', qualifier: 'gt', raw_unit: '原单位？（待核对）', medical_time: { ...time, raw_text: '当地无效时刻原文：2026-03-08 02:30', local_datetime: '2026-03-08T02:30:00', instant: null, timezone_name: 'America/New_York', utc_offset_minutes: null, resolution_status: 'invalid' }, comparison_reasons: ['time_invalid', 'unit_mismatch'], source_organization: null, verification_states: [{ purpose: 'synthetic', status: 'needs_correction', version: '1' }] }),
  item({ raw_numeric: '13.07000', qualifier: 'ge', raw_unit: '', medical_time: { ...time, raw_text: '原文时间尚待核对', instant: null, timezone_name: null, utc_offset_minutes: null, resolution_status: 'ambiguous' }, comparison_reasons: ['time_ambiguous', 'time_unresolved', 'precision_mismatch', 'same_time', 'timezone_unconfirmed'] }),
  item({ raw_numeric: '24.000', qualifier: 'lt', raw_unit: '原单位', raw_text: null }),
];
function freeze(value) { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
freeze(fixtures); const fixtureSnapshot = JSON.stringify(fixtures);
const envelope = (trend, patientId = patient, conceptCode = concept) => ({ patient_id: patientId, concept_code: conceptCode, trend });
validateAnalyteRead(envelope(fixtures));
const expectedComparisons = ['没有更早的可比较结果', '包含限定值，不能作为精确结果比较；检测方法不同', '原值无法确定；单位待补充', '原值无法确定；单位待补充；检测方法待补充；医学时间待补充', '当地时间无效，不能比较；原始单位不同', '当地时间存在歧义，无法确定先后；准确时间尚未解析，不能比较；医学时间精度不同；同一医学时间的结果需要核对；时区尚未确认一致', '不满足比较要求'];
const qualifiers = { eq: '=', lt: '<', le: '≤', gt: '>', ge: '≥' };
const rawValue = value => `${qualifiers[value.qualifier] ?? ''}${value.raw_numeric ?? value.raw_text ?? '原值未提供'}${value.raw_unit ? ` ${value.raw_unit}` : '（单位待补充）'}`;
const listeners = new Set(); globalThis.__analyteSubscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };
const initialAccess = () => ({ principalId: principal, patientId: patient, loading: false, error: null, relations: [{ patient_id: patient, display_id: '合成患儿标识' }], refresh: async () => {} });
let root, router, requests = [], responder;
const page = () => document.querySelector('[data-screen-id]')?.shadowRoot ?? document;
const content = () => page().querySelector('.page-content')?.textContent ?? '';
const rows = () => [...page().querySelectorAll('.rich-analyte-results>.rich-analyte-result')];
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
const button = label => [...page().querySelectorAll('button')].find(node => node.textContent.trim() === label || node.querySelector('strong')?.textContent === label || node.getAttribute('aria-label') === label);
const click = async node => { assert.ok(node); assert.equal(node.disabled ?? false, false); await act(async () => node.click()); await flush(); };
const reply = (trend, patientId = patient, conceptCode = concept) => Response.json(envelope(trend, patientId, conceptCode));
globalThis.fetch = async (path, options = {}) => {
  const match = /^\/api\/patients\/([^/]+)\/analytes\/([^/]+)\/search$/.exec(String(path));
  assert.ok(match, `Unexpected synthetic request ${path}`); assert.equal(options.method, 'POST'); assert.deepEqual(JSON.parse(options.body), {}); assert.equal(options.credentials, 'same-origin');
  requests.push({ patientId: match[1], concept: decodeURIComponent(match[2]), signal: options.signal });
  return responder(match[1], decodeURIComponent(match[2]), options);
};
async function mount(response = () => reply(fixtures)) {
  navigationMemory.clear(); requests = []; responder = response; globalThis.__analyteAccess = initialAccess(); globalThis.__analyteCharts = [];
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ path: '/records/analytes/:analyteId', element: h(AnalytePage) }, { path: '*', element: h('p', null, 'Synthetic destination') }], { initialEntries: [`/records/analytes/${concept}`] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
}
async function unmount() { await act(async () => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
async function access(changes) { await act(async () => { globalThis.__analyteAccess = { ...globalThis.__analyteAccess, ...changes }; listeners.forEach(fn => fn()); }); await flush(); }
try {
  await mount(); assert.equal(rows().length, fixtures.length); assert.equal(requests.length, 1); assert.ok(content().includes('尚无可比较的结果'));
  const css = readFileSync(new URL('../src/features/records/AnalytePage.css', import.meta.url), 'utf8');
  assert.ok([...page().querySelectorAll('style')].some(node => node.textContent === css), 'page-scoped CSS is actually inside the frame shadow root');
  assert.match(css, /minmax\(0,1fr\)/); assert.match(css, /overflow-wrap:anywhere/); assert.match(css, /summary:focus-visible/); assert.match(css, /min-height:54px/); assert.doesNotMatch(css, /text-overflow|line-clamp|overflow:hidden|white-space:nowrap/);
  for (const [index, row] of rows().entries()) {
    const value = fixtures[index], summary = row.firstElementChild;
    assert.equal(row.tagName, 'DETAILS'); assert.equal(row.open, false); assert.equal(summary.tagName, 'SUMMARY');
    assert.equal(summary.getAttribute('role'), null); assert.equal(summary.getAttribute('tabindex'), null); assert.equal(summary.querySelector('button,a,input,[tabindex]'), null);
    assert.equal(summary.querySelector('.rich-analyte-result-time').textContent, medicalSourceTimeLabel(value.medical_time));
    assert.equal(summary.querySelector('.rich-analyte-result-value').textContent, rawValue(value));
    assert.equal(summary.querySelector('.rich-analyte-result-comparison').textContent, expectedComparisons[index]);
    assert.equal(summary.querySelector('.rich-analyte-result-state')?.textContent ?? null, value.value_state === 'known' ? null : `原值状态：${value.value_state === 'unknown' ? '无法确定' : '需要更正'}`);
    assert.equal(summary.querySelector('svg').getAttribute('aria-hidden'), 'true');
    summary.focus(); assert.equal(page().activeElement, summary, 'native summary accepts focus in jsdom');
    for (const key of ['Enter', ' ']) { const event = new window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, composed: true }); summary.dispatchEvent(event); assert.equal(event.defaultPrevented, false, 'native keyboard default is not intercepted'); }
    await click(summary); assert.equal(row.open, true, 'native disclosure activation reveals the full content');
    const pairs = new Map([...row.querySelectorAll(':scope>.pair')].map(node => [node.firstElementChild.textContent, node.lastElementChild.textContent]));
    assert.equal(pairs.get('原值状态'), { known: '已提供', unknown: '无法确定', invalid: '需要更正' }[value.value_state]); assert.equal(pairs.get('原项目名称'), value.original_name ?? '未提供'); assert.equal(pairs.get('原文'), value.raw_text ?? undefined);
    assert.equal(pairs.get('医学时间'), medicalSourceTimeLabel(value.medical_time)); assert.equal(pairs.get('检测方法'), value.method ?? '待补充'); assert.equal(pairs.get('资料来源'), originLabels[value.origin_type]); assert.equal(pairs.get('来源机构'), value.source_organization ?? '未提供');
    assert.equal(pairs.get('专业核对'), value.verification_states.length ? value.verification_states.map(state => ({ pending: '待核对', verified: '已核对', needs_correction: '需补正' }[state.status])).join('、') : '未核对');
    assert.equal(row.querySelector(':scope>a').getAttribute('href'), `/records/versions/${value.record_version_id}`);
    await click(summary); assert.equal(row.open, false);
  }
  assert.equal(requests.length, 1, 'expansion does not fetch or reorder');
  await click(button('查看原始资料')); const dialog = page().querySelector('[role=dialog]'); assert.ok(dialog); assert.equal(page().querySelector('.phone-scroll').hasAttribute('inert'), true);
  assert.deepEqual([...dialog.querySelectorAll('a')].map(node => node.getAttribute('href')), fixtures.map(value => `/records/versions/${value.record_version_id}`));
  await click(button('关闭查看原始资料')); assert.equal(page().querySelector('[role=dialog]'), null);
  await click(rows()[2].firstElementChild); await click(rows()[2].querySelector(':scope>a')); assert.equal(router.state.location.pathname, `/records/versions/${fixtures[2].record_version_id}`); await unmount();

  await mount(() => reply([fixtures[3]]));
  assert.deepEqual([...rows()[0].firstElementChild.querySelectorAll('.rich-analyte-result-comparison')].map(node => node.textContent), ['没有更早的可比较结果', expectedComparisons[3]], 'first-row supplied reasons remain visible beside original insufficiency text'); await unmount();

  // Production AnalyteChart stays mounted; only Chart.js canvas rendering is stubbed.
  const previous = item({ raw_numeric: '2.000', raw_unit: 'large', raw_text: null });
  const comparison = { kind: 'approved_mapping', previous_field_id: previous.field_id, previous_value: '20.000', current_value: '25.000', unit: 'small', method: '原检测方法完整文字', config_id: randomUUID(), config_checksum: 'a'.repeat(64), previous_mapping_id: randomUUID(), current_mapping_id: randomUUID(), previous_source_revision_id: randomUUID(), current_source_revision_id: randomUUID() };
  const mapped = item({ raw_numeric: '25.000', raw_unit: 'small', raw_text: null, direction: 'up', comparison });
  await mount(() => reply([previous, mapped])); assert.equal(rows()[1].firstElementChild.querySelector('.rich-analyte-result-comparison').textContent, '较前次上升'); assert.ok(globalThis.__analyteCharts.length > 0); assert.equal(page().querySelectorAll('canvas').length, 1);
  const mappedRow = rows()[1]; await click(mappedRow.firstElementChild); const mapping = mappedRow.querySelector(':scope>details'); assert.equal(mapping.open, false); await click(mapping.firstElementChild); assert.equal(mapping.open, true);
  for (const text of ['本次 25.000 small；前次 20.000 small。原值仍按报告保存。', comparison.config_id, comparison.config_checksum, comparison.current_mapping_id, comparison.previous_mapping_id]) assert.ok(mapping.textContent.includes(text));
  assert.equal(mappedRow.firstElementChild.querySelector('.rich-analyte-result-value').textContent, '=25.000 small'); await unmount();

  await mount(() => reply([])); assert.equal(rows().length, 0); assert.ok(content().includes('尚无可比较的结果')); assert.equal(button('查看原始资料').disabled, true); await unmount();
  await mount(() => Response.json({ code: 'SYNTHETIC_READ_FAILED', message: '合成读取错误', retryable: true, recovery: 'reload', field_errors: [] }, { status: 500 })); assert.equal(rows().length, 0); assert.ok(content().includes('暂时无法取得资料')); responder = () => reply(fixtures); await click(button('重新读取资料')); assert.equal(rows().length, fixtures.length); await unmount();
  await mount(() => Response.json({ code: 'CASE_ACTION_DENIED', message: '合成权限拒绝', retryable: false, recovery: 'none', field_errors: [] }, { status: 403 })); assert.equal(rows().length, 0); assert.ok(content().includes('当前访问关系需要核验')); await unmount();
  await mount(() => reply(fixtures, otherPatient)); assert.equal(rows().length, 0); assert.ok(content().includes('历次检验所属患儿或项目与本次请求不一致')); await unmount();
  let releaseOld; const oldRead = new Promise(resolve => { releaseOld = resolve; }); const other = item({ raw_numeric: '123.0000000089', original_name: '另一合成患儿结果', raw_text: null });
  await mount(p => p === patient ? oldRead : reply([other], otherPatient)); assert.equal(rows().length, 0); assert.ok(content().includes('正在读取当前资料')); const oldRequest = requests[0];
  await access({ patientId: otherPatient, relations: [{ patient_id: otherPatient, display_id: '另一合成患儿' }] }); assert.equal(oldRequest.signal.aborted, true); assert.equal(rows().length, 1);
  await act(async () => releaseOld(reply(fixtures))); await flush(); assert.equal(rows().length, 1); assert.ok(!content().includes(fixtures[0].raw_numeric));
  await click(button('查看原始资料')); assert.ok(page().querySelector('[role=dialog]')); await access({ patientId: null }); assert.equal(rows().length, 0); assert.equal(page().querySelector('[role=dialog]'), null); assert.ok(!content().includes(other.raw_numeric)); assert.equal(button('查看原始资料').disabled, true); assert.equal(page().querySelector('input[type=search]').disabled, true); await unmount();
  assert.equal(JSON.stringify(fixtures), fixtureSnapshot);
  console.log('PASS S09 compact history DOM: exact order/raw strings/qualifiers/units/full medical times; collapsed-visible unknown/invalid and supplied comparison reasons; native summary focus, non-intercepted keys and activation; full metadata/mapping/exact-version navigation; source overlay; empty/loading/error retry/denial/mismatched scope/stale patient response/revocation. Actual page/resource/schema; synthetic HTTP, stubbed canvas renderer. No browser/backend/visual, touch or real keyboard-default proof.');
} finally { if (root?._internalRoot) await unmount(); dom.window.close(); }
