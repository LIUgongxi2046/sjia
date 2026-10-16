// Home-only presentation regression. Actual component with synthetic props;
// no app server, browser, backend, record selection, or clinical interpretation.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { JSDOM } from 'jsdom';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|jpg|webp|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/home' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLButtonElement']) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let requests = 0;
globalThis.fetch = async () => { requests++; throw new Error('Presentation checks must not send requests'); };
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { HomeCompanion, HomeQuickActions } = await import('../src/components/HomeVisual.tsx');
const root = createRoot(document.getElementById('root'));
const labels = ['体温', '皮疹', '关节表现'];
const context = '最近有明确时间的记录';
const time = '2026-10-09T08:30:00.123456+08:00';
const values = ['≥38.456789 C', '观察到', '无法确定'];
const common = labels.map((label, index) => Object.freeze({ label, value: values[index], time, context, notice: `另有 ${[8, 8, 3][index]} 条记录时间待确认` }));
Object.freeze(common);
const before = JSON.stringify(common);
let viewed = [], observed = 0, archive = 0, reports = 0, therapies = 0;
const props = observations => ({ name: 'SYNTHETIC ONLY', observations, onArchive: () => archive++, onViewObservation: index => viewed.push(index), patientPicker: h('p', null, 'SYNTHETIC PICKER') });
const cards = () => [...document.querySelectorAll('.symptom-columns>button')];
const pins = () => [...document.querySelectorAll('.symptom-pin')];
const text = () => document.querySelector('.home-twin').textContent;
const describedBy = node => (node.getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean).map(id => {
  const target = document.getElementById(id);
  assert.ok(target, `description must resolve: ${id}`);
  return target;
});
const render = async (observations, extra = {}) => act(async () => root.render(h(HomeCompanion, { ...props(observations), ...extra }, h('p', null, 'SYNTHETIC CHILD CONTENT'))));
const click = async node => act(async () => node.click());
const checks = [];
try {
  await render(common);
  const shared = document.querySelector('.home-observation-time');
  assert.ok(shared);
  assert.equal(shared.lastElementChild.textContent, time, 'full supplied time must remain visible');
  for (const label of labels) assert.ok(shared.firstElementChild.textContent.includes(label));
  assert.ok(shared.firstElementChild.textContent.includes(context));
  assert.equal(text().split(context).length - 1, 1);
  assert.equal(text().split(time).length - 1, 1);
  assert.equal(shared.closest('details'), null, 'subset scope and time cannot be collapsed');
  assert.deepEqual(cards().map(card => card.querySelector('.home-observation-value').textContent), values);
  assert.ok(cards().every(card => !card.querySelector('.home-observation-context, .home-observation-card-time')));
  const disclosure = document.querySelector('.home-observation-notices');
  assert.equal(disclosure.open, false);
  assert.equal(disclosure.firstElementChild.tagName, 'SUMMARY');
  assert.ok(disclosure.firstElementChild.textContent.includes('时间待确认'));
  for (const label of labels) assert.ok(disclosure.firstElementChild.textContent.includes(label), 'closed disclosure names every affected category');
  assert.deepEqual([...disclosure.querySelectorAll('dt')].map(node => node.textContent), labels);
  assert.deepEqual([...disclosure.querySelectorAll('dd')].map(node => node.textContent), common.map(item => item.notice));
  assert.ok(!disclosure.textContent.includes('19'), 'category counts must never be summed');
  for (let index = 0; index < 3; index++) {
    for (const entry of [cards()[index], pins()[index]]) {
      const descriptions = describedBy(entry);
      assert.equal(descriptions.length, 2);
      assert.equal(descriptions[0], shared);
      assert.equal(descriptions[1].querySelector('dt').textContent, labels[index]);
      assert.equal(descriptions[1].querySelector('dd').textContent, common[index].notice);
    }
  }
  await click(disclosure.firstElementChild); assert.equal(disclosure.open, true);
  await click(disclosure.firstElementChild); assert.equal(disclosure.open, false);
  assert.deepEqual(viewed, [], 'disclosure does not invoke a record destination');
  assert.ok(text().includes('SYNTHETIC CHILD CONTENT'));
  assert.ok(text().includes('SYNTHETIC PICKER'));
  checks.push('Identical enabled-card scope/time shown once, exact values intact, visible named scope, per-category notices preserved without totals, native disclosure and accessible descriptions');

  const mixedTimes = common.map((item, index) => ({ ...item, time: index === 1 ? '2026-10-08' : item.time }));
  await render(mixedTimes);
  assert.equal(document.querySelector('.home-observation-time'), null);
  for (let index = 0; index < 3; index++) {
    assert.equal(cards()[index].querySelector('.home-observation-context').textContent, context);
    assert.equal(cards()[index].querySelector('.home-observation-card-time').textContent, mixedTimes[index].time);
  }
  const mixedScopes = common.map((item, index) => ({ ...item, context: index === 1 ? undefined : context }));
  await render(mixedScopes);
  assert.equal(document.querySelector('.home-observation-time'), null, 'same time with different scope stays card-local');
  assert.equal(cards()[1].querySelector('.home-observation-context'), null);
  assert.ok(cards()[0].textContent.includes(context)); assert.ok(cards()[2].textContent.includes(context));
  await render(common.map((item, index) => ({ ...item, time: index === 2 ? undefined : item.time })));
  assert.equal(document.querySelector('.home-observation-time'), null, 'a card without a supplied time prevents grouping');
  checks.push('Mixed times, missing times, and mixed subset scope remain correctly attributed on each card');

  await render(labels.map(label => ({ label, value: '无法确定', time: '时间待补充' })));
  assert.equal(document.querySelector('.home-observation-time'), null);
  assert.equal(document.querySelector('.home-observation-notices'), null);
  assert.equal(text().includes(context), false);
  for (const card of cards()) assert.ok(card.textContent.includes('无法确定时间待补充'));
  await render(labels.map((label, index) => ({ label, value: ['记录时间无法确定先后，请查看', '读取失败', '需要核验权限'][index], disabled: index === 1 })));
  assert.deepEqual(cards().map(card => card.querySelector('.home-observation-value').textContent), ['记录时间无法确定先后，请查看', '读取失败', '需要核验权限']);
  assert.equal(cards()[1].disabled, true); assert.equal(pins()[1].disabled, true);
  viewed = []; await click(cards()[1]); await click(pins()[1]); assert.deepEqual(viewed, []);
  await render(common.map((item, index) => ({ ...item, disabled: index === 1 })));
  assert.equal(document.querySelector('.home-observation-time'), null, 'disabled state does not inherit a shared time');
  checks.push('All-unknown, unresolved chronology, read failure, permission state, and disabled actions stay distinguishable');

  await render(common.map(({ notice, context, ...item }) => item));
  assert.equal(document.querySelector('.home-observation-notices'), null);
  assert.ok(document.querySelector('.home-observation-time').firstElementChild.textContent.endsWith('记录时间'));
  assert.equal(text().includes(context), false, 'grouping never invents a latest/subset claim');
  assert.ok(cards().every(card => describedBy(card).length === 1));
  await render(common.map((item, index) => ({ ...item, notice: index === 1 ? item.notice : undefined })));
  assert.equal(document.querySelector('.home-observation-notice-scope').textContent, '涉及：皮疹');
  assert.deepEqual([...document.querySelectorAll('.home-observation-notices dt')].map(node => node.textContent), ['皮疹']);
  assert.equal(describedBy(cards()[0]).length, 1); assert.equal(describedBy(cards()[1]).length, 2);
  await render(common.slice(0, 2)); assert.equal(pins()[2].hasAttribute('aria-describedby'), false, 'unrepresented pins cannot claim the shared card scope');
  await render([common[0]]); assert.equal(document.querySelector('.home-observation-time'), null);
  await render([]); assert.equal(document.querySelector('.home-observation-time'), null); assert.equal(document.querySelector('.home-observation-notices'), null);
  checks.push('No empty disclosure or invented qualification; partial notices name only their categories; singleton/empty card lists remain safe');

  await render(common); viewed = [];
  for (const card of cards()) await click(card);
  for (const pin of pins()) await click(pin);
  assert.deepEqual(viewed, [0, 1, 2, 0, 1, 2], 'card/pin index destinations must remain unchanged');
  await click(document.querySelector('.home-twin-heading>button')); assert.equal(archive, 1);
  await render(common, { onViewObservation: undefined, onObserve: () => observed++ });
  for (const entry of [...cards(), ...pins()]) await click(entry);
  assert.equal(observed, 6, 'existing observation fallback remains operable');
  await act(async () => root.render(h(HomeQuickActions, { onObserve: () => observed++, onReports: () => reports++, onTherapies: () => therapies++ })));
  for (const entry of document.querySelectorAll('.home-entry-grid>button')) await click(entry);
  assert.deepEqual([observed, reports, therapies], [7, 1, 1]);
  checks.push('Every existing card, pin, archive, fallback, and quick-action handler retained');

  const long = '合成较长文字ABCDEFGHIJ'.repeat(18);
  const longTime = `${time}（${long}）`;
  const longItems = common.map((item, index) => ({ ...item, label: `${item.label}${long}`, value: `${index}${long}`, time: longTime, notice: `${item.notice}；${long}` }));
  await render(longItems);
  assert.equal(document.querySelector('.home-observation-time').lastElementChild.textContent, longTime);
  assert.equal(document.querySelector('.home-observation-time').closest('[hidden], [aria-hidden="true"], details'), null);
  for (let index = 0; index < 3; index++) {
    assert.equal(cards()[index].querySelector('.home-observation-value').textContent, longItems[index].value);
    assert.equal(cards()[index].querySelector('strong').textContent, `${index + 1}${longItems[index].label}`);
    assert.equal(describedBy(cards()[index])[1].querySelector('dd').textContent, longItems[index].notice);
  }
  await render(longItems.map((item, index) => ({ ...item, time: `${item.time}${index}` })));
  for (let index = 0; index < 3; index++) assert.equal(cards()[index].querySelector('.home-observation-card-time').textContent, `${longTime}${index}`);
  await act(async () => root.render(h('div', null, h(HomeCompanion, props(common)), h(HomeCompanion, props(common)))));
  const ids = [...document.querySelectorAll('[id]')].map(node => node.id);
  assert.equal(new Set(ids).size, ids.length, 'repeated components need independent description IDs');
  for (const entry of document.querySelectorAll('[aria-describedby]')) describedBy(entry);
  const css = readFileSync(new URL('../src/styles/rich-ui.css', import.meta.url), 'utf8');
  assert.match(css, /\.home-twin \.symptom-columns>button\{[^}]*overflow-wrap:anywhere/);
  assert.match(css, /\.home-twin \.home-observation-notices>summary\{[^}]*min-height:44px/);
  assert.match(css, /\.home-twin \.home-observation-notices>summary:focus-visible\{/);
  assert.equal(JSON.stringify(common), before, 'the caller data must not be changed');
  assert.equal(requests, 0);
  checks.push('Long labels/values/full times/notices survive without truncation in DOM; unique accessible descriptions; wrapping/focus/touch-target CSS contracts; source props immutable; no network');
} finally {
  await act(async () => root.unmount());
  dom.window.close();
}
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM_NOT_PIXEL_EVIDENCE', checks }, null, 2));
