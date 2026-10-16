// Synthetic shared-control DOM and author-declaration cascade regression only.
// This does not measure layout, clipping, tap geometry, browser pixels or accessibility.
// No routes, authentication, services, database, provider calls or network are used.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { parse } from 'postcss';
import { winningDeclaration } from './css-cascade-test.mjs';

// Override the generic test loader's empty CSS export with each actual file.
const loadedCss = new Map();
registerHooks({ load(url, context, nextLoad) {
  if (url.startsWith('file:') && new URL(url).pathname.endsWith('.css')) {
    const path = fileURLToPath(new URL(url));
    const css = readFileSync(path, 'utf8');
    loadedCss.set(path, css);
    return { format: 'module', source: `export default ${JSON.stringify(css)};`, shortCircuit: true };
  }
  if (/\.(png|jpg|webp|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/synthetic-registration-boundaries' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLSelectElement', 'HTMLButtonElement', 'Element', 'Node', 'ShadowRoot', 'Event', 'CustomEvent']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let systemDark = false, requests = 0;
window.matchMedia = query => ({ media: query, matches: query === '(prefers-color-scheme: dark)' && systemDark, addEventListener() {}, removeEventListener() {} });
globalThis.fetch = async () => { requests++; throw new Error('The isolated layout-boundary fixture may not fetch'); };
const { createElement: h, act, useState } = await import('react');
const { createRoot } = await import('react-dom/client');
const { UI2Frame } = await import('../src/components/UI2Frame.tsx');
const { RegistrationPatient } = await import('../src/features/registration/RichRegistration.tsx');
const { MedicalTimeInput, timePayload } = await import('../src/components/MedicalTimeInput.tsx');
const { TemperatureUnit } = await import('../src/components/TemperatureUnit.tsx');
const { richThemeStorageKey } = await import('../src/state/richTheme.ts');
const frameUrl = new URL('../src/components/UI2Frame.tsx', import.meta.url);
const cssFiles = [...readFileSync(frameUrl, 'utf8').matchAll(/import\s+\w+\s+from\s+['"]([^'"]+\.css\?inline)['"]/g)]
  .map(match => fileURLToPath(new URL(match[1], frameUrl)));
assert.ok(cssFiles.length >= 5, 'Read all actual UI2Frame inline stylesheet imports');
assert.deepEqual([...loadedCss.keys()], cssFiles, 'Actual UI2Frame CSS imports load in production order');
const applicationCss = cssFiles.map(path => readFileSync(path, 'utf8')).join('');
const registrationUrl = new URL('../src/styles/rich-registration.css', import.meta.url);
const registrationCss = readFileSync(registrationUrl, 'utf8');
const boundaryMarker = '/* S04–S06 narrow-screen input boundaries.';
const boundaryOffset = registrationCss.indexOf(boundaryMarker);
assert.ok(boundaryOffset >= 0, 'The scoped registration boundary block must be present');
const baselineCss = applicationCss.replace(registrationCss, registrationCss.slice(0, boundaryOffset));
const boundaryRules = parse(registrationCss.slice(boundaryOffset)).nodes.filter(node => node.type === 'rule');
assert.equal(boundaryRules.length, 6, 'Only the six scoped boundary rules are under regression');
const frozenPaths = [...cssFiles, fileURLToPath(frameUrl), ...['../src/features/registration/RichRegistration.tsx', '../src/components/MedicalTimeInput.tsx', '../src/components/TemperatureUnit.tsx'].map(path => fileURLToPath(new URL(path, import.meta.url)))];
const sha256 = path => createHash('sha256').update(readFileSync(path)).digest('hex');
const originalHashes = new Map(frozenPaths.map(path => [path, sha256(path)]));

const initialStart = Object.freeze({ precision: 'minute', value: '2026-10-01T08:30', timezone: 'Asia/Shanghai' });
const initialEnd = Object.freeze({ precision: 'second', value: '2026-10-12T17:42:53', timezone: 'Europe/London' });
const longValue = '9007199254740993.0000000000000000000000000000000000000000';
const longUnit = '原始单位 / SYNTHETIC-' + '单位不缩写'.repeat(16);
const longPatient = '合成患儿标识（仅测试，不是真实身份）' + '完整保留'.repeat(12);
let state, changes = [], patientClicks = 0, symptomClicks = 0;

// Markup mirrors the affected wrappers; the three shared controls and UI2Frame
// are actual production components. This fixture is not a full registration route.
function Fixture({ screenId, blocked = false, timeDisabled = false, displayId = longPatient, rawUnit, selection = '摄氏度（°C）', start = initialStart, end = initialEnd }) {
  const [startTime, setStartTime] = useState(() => ({ ...start }));
  const [endTime, setEndTime] = useState(() => ({ ...end }));
  const [temperature, setTemperature] = useState(longValue);
  state = { startTime, endTime, temperature };
  return h(UI2Frame, { title: '合成登记边界测试', screenId, audience: 'family', note: '仅组件与声明测试', onBack() {} },
    h(RegistrationPatient, { displayId, onClick: () => patientClicks++ }),
    h('fieldset', { className: 'registration-form', disabled: blocked },
      h('section', { className: 'card temperature registration-temperature' },
        h('label', { className: 'temperature-value' },
          h('input', { 'aria-label': '体温原值', inputMode: 'decimal', disabled: timeDisabled, value: temperature, onChange: event => { changes.push('temperature'); setTemperature(event.target.value); } }),
          h(TemperatureUnit, rawUnit === undefined ? { selection } : { rawUnit }))),
      h('fieldset', { className: 'registration-symptom', 'aria-label': '合成症状' },
        h('legend', null, '合成症状'), h('div', { className: 'segments' },
          ...['观察到', '未观察到', '无法确定'].map((label, index) => h('button', { key: label, type: 'button', disabled: timeDisabled, className: index === 0 ? 'selected' : '', onClick: () => symptomClicks++ }, label)))),
      h('details', { className: 'registration-inner-disclosure', open: true }, h('summary', null, '使用时间与补充说明'),
        h('div', { className: 'registration-detail-body' },
          h('div', { className: 'field-grid', 'data-fixture': 'direct-grid' },
            h(MedicalTimeInput, { ui2: true, precisionDetails: true, disabled: timeDisabled, label: '开始日期', value: startTime, onChange: value => { changes.push('start'); setStartTime(value); } }),
            h(MedicalTimeInput, { ui2: true, precisionDetails: true, disabled: timeDisabled, label: '结束日期', value: endTime, onChange: value => { changes.push('end'); setEndTime(value); } })),
          h('section', null, h('div', { className: 'field-grid', 'data-fixture': 'nested-grid' }, 'Nested scope-negative grid')))),
      h('div', { className: 'field-grid', 'data-fixture': 'outside-grid' }, 'Outside-detail scope-negative grid')));
}
const root = createRoot(document.getElementById('root'));
const page = () => document.querySelector('[data-screen-id]').shadowRoot;
const node = selector => { const result = page().querySelector(selector); assert.ok(result, `Missing ${selector}`); return result; };
let fixtureKey = 0, currentProps;
async function mount(props, theme = 'light') {
  window.localStorage.setItem(richThemeStorageKey, theme);
  currentProps = props;
  fixtureKey++;
  await act(async () => root.render(h(Fixture, { ...currentProps, key: fixtureKey })));
  assert.equal(node('.device').dataset.theme, theme);
  assert.equal(node('style').textContent, applicationCss, 'Real mounted ShadowRoot receives every actual stylesheet, in order');
}
async function render(changes) {
  currentProps = { ...currentProps, ...changes };
  await act(async () => root.render(h(Fixture, { ...currentProps, key: fixtureKey })));
}
async function change(input, value) {
  assert.equal(input.matches(':disabled'), false, 'Edit only enabled native controls');
  await act(async () => {
    const prototype = input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, 'value').set.call(input, value);
    input.dispatchEvent(new window.Event('input', { bubbles: true, composed: true }));
    input.dispatchEvent(new window.Event('change', { bubbles: true, composed: true }));
  });
}
function timeControls(label) {
  const fieldLabel = [...page().querySelectorAll('label.field')].find(item => item.firstElementChild?.textContent === label);
  assert.ok(fieldLabel, `Time label ${label}`);
  const input = fieldLabel.querySelector('input');
  assert.equal(fieldLabel.htmlFor, input.id);
  assert.equal(fieldLabel.control, input, 'Native label association is retained');
  const wrapper = fieldLabel.parentElement;
  return { input, precision: wrapper.querySelector('[aria-label="时间精度"]'), timezone: wrapper.querySelector('[aria-label="时区"]'), wrapper };
}

// This media evaluator deliberately fails on unsupported conditions. It supplies
// only query matching to the existing author-cascade helper, never fake geometry.
function mediaMatches({ width, theme, contrast = false, forced = false, reduced = false }) {
  return query => query.split(',').some(branch => {
    const trimmed = branch.trim();
    if (trimmed === 'print') return false;
    const terms = [...trimmed.matchAll(/\(([^)]+)\)/g)].map(match => match[1].replace(/\s/g, ''));
    assert.ok(terms.length && !trimmed.replace(/\([^)]*\)/g, '').replace(/\band\b|\s/g, ''), `Unsupported media query: ${query}`);
    return terms.every(term => {
      const bound = /^(min|max)-(width|height):(\d+)px$/.exec(term);
      if (bound) {
        const actual = bound[2] === 'width' ? width : 844;
        return bound[1] === 'min' ? actual >= Number(bound[3]) : actual <= Number(bound[3]);
      }
      if (term === 'orientation:landscape') return false;
      if (term === 'orientation:portrait') return true;
      if (term === 'prefers-contrast:more') return contrast;
      if (term === 'forced-colors:active') return forced;
      if (term === 'prefers-reduced-motion:reduce') return reduced;
      if (term === 'prefers-color-scheme:dark') return theme === 'dark';
      if (term === 'prefers-color-scheme:light') return theme === 'light';
      throw new Error(`Unsupported media condition: ${term}`);
    });
  });
}
const widths = [320, 360, 390, 430, 580];
const themes = ['light', 'dark'];
const modes = [
  { name: 'ordinary' }, { name: 'more-contrast', contrast: true },
  { name: 'forced-colors', forced: true }, { name: 'reduced-motion', reduced: true },
  { name: 'combined-preferences', contrast: true, forced: true, reduced: true },
];
const screens = ['UI2-02', 'UI2-03', 'UI2-04', 'REPORT-CONFIRMATION', 'UI2-05', 'UI2-15'];
let cascadeAssertions = 0, scenarios = 0, mutationChecks = 0;
const checks = [];
try {
  for (const theme of themes) for (const screenId of screens) {
    await mount({ screenId }, theme);
    const patient = node('.registration-patient'), buttons = [...page().querySelectorAll('.registration-symptom button')];
    const temperature = node('.temperature-value'), input = temperature.querySelector('input'), unit = temperature.querySelector('span');
    const direct = node('[data-fixture=direct-grid]'), nested = node('[data-fixture=nested-grid]'), outside = node('[data-fixture=outside-grid]');
    for (const width of widths) for (const mode of modes) {
      scenarios++;
      const matches = mediaMatches({ width, theme, ...mode });
      const context = `${screenId}/${theme}/${width}/${mode.name}`;
      const expect = (element, property, value) => {
        cascadeAssertions++;
        assert.equal(winningDeclaration(applicationCss, element, property, matches)?.value, value, `${context}: ${property} on ${element.className}`);
      };
      expect(patient, 'min-height', screens.indexOf(screenId) < 4 ? '44px' : '42px');
      for (const button of buttons) expect(button, 'min-height', screenId === 'UI2-02' ? '44px' : '40px');
      expect(direct, 'grid-template-columns', screenId === 'UI2-04' ? 'minmax(0,1fr)' : width <= 370 ? '1fr' : '1fr 1fr');
      expect(nested, 'grid-template-columns', width <= 370 ? '1fr' : '1fr 1fr');
      expect(outside, 'grid-template-columns', width <= 370 ? '1fr' : '1fr 1fr');
      const boundaries = [[temperature, 'flex-wrap', 'wrap'], [input, 'min-width', '0'], [input, 'max-width', '100%'], [unit, 'max-width', '100%']];
      for (const [element, property, value] of boundaries) {
        if (screenId === 'UI2-02') expect(element, property, value);
        else {
          cascadeAssertions++;
          assert.deepEqual(winningDeclaration(applicationCss, element, property, matches), winningDeclaration(baselineCss, element, property, matches), `${context}: temperature boundary must not leak outside observation registration`);
        }
      }
      // Existing form structure/declarations stay native and unhidden.
      expect(temperature, 'display', 'flex'); expect(direct, 'display', 'grid');
      expect(patient, 'display', 'flex');
    }
  }
  checks.push('300 author-cascade scenarios: six page scopes × light/dark × 320/360/390/430/580 × five preference-media modes; exact 44px winners and unchanged negative 42px/40px and existing grid breakpoints');

  // In-memory removal of each new rule must lose its targeted declaration.
  // No production file is modified to establish this regression sensitivity.
  for (const [index, screenId, selector, property, expected] of [
    [0, 'UI2-03', '.registration-patient', 'min-height', '44px'],
    [1, 'UI2-02', '.registration-symptom button', 'min-height', '44px'],
    [2, 'UI2-02', '.temperature-value', 'flex-wrap', 'wrap'],
    [3, 'UI2-02', '.temperature-value input', 'max-width', '100%'],
    [4, 'UI2-02', '.temperature-value span', 'max-width', '100%'],
    [5, 'UI2-04', '[data-fixture=direct-grid]', 'grid-template-columns', 'minmax(0,1fr)'],
  ]) {
    await mount({ screenId });
    const mutated = applicationCss.replace(boundaryRules[index].toString(), '');
    assert.notEqual(mutated, applicationCss);
    const matches = mediaMatches({ width: 430, theme: 'light' });
    assert.equal(winningDeclaration(applicationCss, node(selector), property, matches)?.value, expected);
    assert.notEqual(winningDeclaration(mutated, node(selector), property, matches)?.value, expected, `Removing rule ${index + 1} must be detected`);
    mutationChecks++;
  }
  checks.push('All six scoped rules have independent in-memory removal sensitivity; no production mutations');

  for (const theme of themes) {
    await mount({ screenId: 'UI2-04' }, theme);
    const patient = node('.registration-patient');
    assert.ok(patient instanceof HTMLButtonElement); assert.equal(patient.type, 'button');
    assert.equal(patient.querySelector('strong').textContent, longPatient);
    assert.equal(patient.querySelector('.registration-avatar').getAttribute('aria-hidden'), 'true');
    const beforeClick = patientClicks;
    await act(async () => patient.click()); assert.equal(patientClicks, beforeClick + 1);
    const start = timeControls('开始日期'), end = timeControls('结束日期');
    assert.notEqual(start.input.id, end.input.id);
    assert.equal(start.input.type, 'datetime-local'); assert.equal(end.input.type, 'datetime-local');
    assert.equal(end.input.step, '1');
    assert.equal(start.precision.value, 'minute'); assert.equal(end.precision.value, 'second');
    assert.equal(start.timezone.value, 'Asia/Shanghai'); assert.equal(end.timezone.value, 'Europe/London');
    const endBefore = structuredClone(state.endTime);
    await change(start.input, '2026-10-02T09:31');
    assert.equal(state.startTime.value, '2026-10-02T09:31');
    assert.equal(state.startTime.precision, 'minute'); assert.equal(state.startTime.timezone, 'Asia/Shanghai');
    assert.deepEqual(state.endTime, endBefore, 'Start edit leaves end value/precision/timezone intact');
    const startBefore = structuredClone(state.startTime);
    // jsdom's native datetime-local setter canonicalizes nonzero seconds with .000.
    const editedEnd = '2026-10-12T18:43:54.000';
    await change(end.input, editedEnd);
    assert.equal(state.endTime.value, editedEnd); assert.equal(state.endTime.precision, 'second');
    assert.equal(state.endTime.timezone, 'Europe/London'); assert.deepEqual(state.startTime, startBefore);
    await change(end.timezone, '');
    assert.equal(state.endTime.timezone, ''); assert.equal(state.endTime.value, editedEnd);
    assert.deepEqual(state.startTime, startBefore, 'End timezone remains independently editable');
    const endAfterZone = structuredClone(state.endTime);
    await change(start.precision, 'date');
    assert.deepEqual(state.startTime, { precision: 'date', value: '', timezone: 'Asia/Shanghai' });
    assert.deepEqual(state.endTime, endAfterZone, 'Precision change resets only its own value');
    await change(timeControls('开始日期').input, '2026-10-03');
    assert.equal(timePayload(state.startTime).local_date, '2026-10-03');
    assert.equal(timePayload(state.startTime).timezone_name, 'Asia/Shanghai');
    assert.equal(timePayload(state.endTime).local_datetime, editedEnd);
    assert.equal(timePayload(state.endTime).precision, 'second');
    assert.equal(timePayload(state.endTime).timezone_name, null);
    const beforeDisabled = JSON.stringify(state), callbacks = changes.length, symptoms = symptomClicks;
    await render({ blocked: true });
    for (const control of page().querySelectorAll('.registration-form input, .registration-form select, .registration-form button')) assert.equal(control.matches(':disabled'), true);
    await act(async () => node('.registration-symptom button').click());
    assert.equal(symptomClicks, symptoms); assert.equal(changes.length, callbacks); assert.equal(JSON.stringify(state), beforeDisabled);
    await render({ blocked: false, timeDisabled: true });
    for (const control of page().querySelectorAll('.registration-form input, .registration-form select, .registration-form button')) assert.equal(control.disabled, true, 'Component disabled props remain native');
    await render({ timeDisabled: false });
    assert.equal(timeControls('开始日期').input.matches(':disabled'), false);
    await act(async () => node('.registration-symptom button').click()); assert.equal(symptomClicks, symptoms + 1);
    await render({ displayId: null }); assert.equal(node('.registration-patient strong').textContent, '选择患儿');
    await render({ displayId: '' }); assert.equal(node('.registration-avatar').textContent, '档');
  }
  checks.push('Actual shared patient button/callback and associated native labels; independent start/end edits preserve opposite value/precision/timezone, exact time payloads, and fieldset/component disabled semantics in both themes');

  await mount({ screenId: 'UI2-04', start: { precision: 'unknown', value: '', timezone: '' }, end: initialEnd });
  assert.equal(timePayload(state.startTime), null);
  assert.equal(timeControls('开始日期').input.type, 'date');
  assert.equal(timeControls('开始日期').input.disabled, false, 'Unknown therapy date can be supplied explicitly');
  const untouchedEnd = structuredClone(state.endTime);
  await change(timeControls('开始日期').input, '2026-10-04');
  assert.equal(state.startTime.precision, 'date'); assert.equal(state.startTime.timezone, '');
  assert.deepEqual(state.endTime, untouchedEnd); assert.equal(timePayload(state.startTime).timezone_name, null);
  await change(timeControls('开始日期').precision, 'unknown');
  assert.equal(timePayload(state.startTime), null); assert.deepEqual(state.endTime, untouchedEnd);
  checks.push('Unknown start time stays null until explicit date entry; returning to unknown leaves the independent end unchanged');

  for (const theme of themes) {
    await mount({ screenId: 'UI2-02' }, theme);
    const input = node('[aria-label="体温原值"]');
    assert.equal(input.type, 'text'); assert.equal(input.inputMode, 'decimal');
    assert.equal(input.closest('label').control, input);
    for (const raw of [longValue, ' 038.200000000000000000 ', '', '未知 / 原始文字']) {
      await change(input, raw); assert.equal(state.temperature, raw); assert.equal(input.value, raw);
      for (const [selection, expected] of [['摄氏度（°C）', '°C'], ['华氏度（°F）', '°F'], ['无法确定', '未知'], ['尚未选择', '待选单位']]) {
        await render({ selection, rawUnit: undefined });
        assert.equal(node('.temperature-value span').textContent, expected);
        assert.equal(input.value, raw, 'Unit presentation never rewrites the raw temperature string');
      }
      for (const [rawUnit, expected] of [[longUnit, longUnit], ['C', 'C'], ['F', 'F'], [null, '未知'], ['', '未知']]) {
        await render({ rawUnit }); assert.equal(node('.temperature-value span').textContent, expected);
        assert.equal(input.value, raw); assert.equal(state.temperature, raw);
      }
    }
  }
  checks.push('Exact long/space-padded/empty/unknown raw temperature strings and long original/C/F/unknown/unselected units survive presentation in both themes; no clinical validation or submission claim');
  for (const [path, hash] of originalHashes) assert.equal(sha256(path), hash, `Production source unchanged: ${path}`);
  assert.equal(requests, 0);
  console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_SHARED_CONTROL_DOM_AND_AUTHOR_CASCADE_NOT_BROWSER_OR_PIXEL_PROOF', widths, themes, media: modes.map(mode => mode.name), scenarios, cascadeAssertions, mutationChecks, networkRequests: requests, registrationCssSha256: sha256(fileURLToPath(registrationUrl)), checks, limits: ['No rendered width, overflow, clipping, pixel, tap-target geometry or real-browser evidence', 'Shared-control fixture, not full registration routes; central route/business regressions remain separate'] }, null, 2));
} finally {
  await act(async () => root.unmount()); dom.window.close();
}
