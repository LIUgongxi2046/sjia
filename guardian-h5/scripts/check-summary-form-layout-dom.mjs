// Actual summary-generation route with synthetic authorized API responses.
// Tests DOM, callbacks and author CSS cascade only; no browser pixels/services/DB.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { winningDeclaration } from './css-cascade-test.mjs';

const loadedCss = new Map();
registerHooks({ load(url, context, nextLoad) {
  if (url.endsWith('/state/access.tsx')) return { format: 'module', source: `import {useSyncExternalStore} from 'react'; export function useAccess(){return useSyncExternalStore(globalThis.__summarySubscribe,()=>globalThis.__summaryAccess);}`, shortCircuit: true };
  if (url.startsWith('file:') && new URL(url).pathname.endsWith('.css')) {
    const path = fileURLToPath(new URL(url)), css = readFileSync(path, 'utf8');
    loadedCss.set(path, css);
    return { format: 'module', source: `export default ${JSON.stringify(css)};`, shortCircuit: true };
  }
  if (/\.(png|jpg|webp|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/summaries', pretendToBeVisual: true });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Element', 'Node', 'ShadowRoot']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { UI2SummariesPage } = await import('../src/features/summaries/UI2SummariesPage.tsx');
const { navigationMemory } = await import('../src/state/navigation.ts');
const { eventTimeLabel } = await import('../src/components/recordTimePresentation.ts');
const { validateSummary } = await import('../src/api/summarySchema.ts');

const patient = randomUUID(), principal = randomUUID();
const listPath = `/api/patients/${patient}/summaries`;
const directoryPath = `/api/patients/${patient}/requests?operation_id=summary_create`;
const makeSummary = changes => ({ summary_id: randomUUID(), patient_id: patient, task_id: randomUUID(), from_date: '2026-09-20', to_date: '2026-09-27', validity: 'valid', generated_at: '2026-09-27T20:20:43.123+08:00', needs_regeneration: false, ...changes });
// Deliberately oldest-first: the UI must keep the returned order, not infer recency.
const summaries = Object.freeze([
  Object.freeze(makeSummary({ validity: 'invalid', needs_regeneration: true })),
  Object.freeze(makeSummary({ from_date: '2026-10-01', to_date: '2026-10-12', generated_at: '2026-10-13T05:06:07.987Z' })),
]);
const originalSummaries = JSON.stringify(summaries);
validateSummary('SummaryList', { patient_id: patient, summaries });
const pendingRequest = {
  patient_id: patient, intent_id: randomUUID(), request_id: randomUUID(), operation_id: 'summary_create', target_key: listPath,
  prepared_at: '2026-10-13T11:12:13+08:00', state: 'pending', may_retry: false, receipt: null, error_code: null,
  generation: '1', expires_at: '2026-10-14T11:12:13+08:00', payload_sha256: 'a'.repeat(64),
  summary_input: { from_date: '2026-10-01', to_date: '2026-10-12', scope_timezone: 'Asia/Shanghai', include_unknown_time: true, record_kinds: ['observation'], include_source_status: true },
};
validateSummary('SummaryRequestList', { patient_id: patient, requests: [pendingRequest] });
const listeners = new Set();
globalThis.__summarySubscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };
const initialAccess = () => ({ principalId: principal, patientId: patient, csrfToken: 'synthetic-csrf', loading: false, error: null, relations: [{ patient_id: patient, display_id: '合成患儿完整原标识（仅测试）' }], refresh: async () => {} });
let root, router, responder, calls;
const page = () => document.querySelector('[data-screen-id]')?.shadowRoot ?? document;
const section = () => [...page().querySelectorAll('section')].find(node => node.querySelector(':scope>h2')?.textContent === '已生成的摘要');
const dates = () => [...page().querySelectorAll('input[type=date]')];
const submit = () => page().querySelector('button[type=submit]');
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
const replyList = (items = summaries) => Response.json({ patient_id: patient, summaries: items });
const replyDirectory = (requests = []) => Response.json({ patient_id: patient, requests });
const failure = message => Response.json({ code: 'SYNTHETIC_UNAVAILABLE', message, retryable: true, recovery: 'reload' }, { status: 503 });
const normalResponse = path => path === listPath ? replyList() : replyDirectory();
globalThis.fetch = async (path, options = {}) => {
  assert.ok([listPath, directoryPath, '/api/write-intents'].includes(String(path)), `Unexpected synthetic request: ${path}`);
  calls.push({ path: String(path), method: options.method, body: options.body ? JSON.parse(options.body) : undefined });
  return responder(String(path), options);
};
async function mount(response = normalResponse, access = {}) {
  navigationMemory.clear(); calls = []; responder = response;
  globalThis.__summaryAccess = { ...initialAccess(), ...access };
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ path: '/summaries', element: h(UI2SummariesPage) }, { path: '*', element: h('p', null, 'Synthetic destination') }], { initialEntries: ['/summaries'] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
}
async function unmount() { await act(async () => root.unmount()); root = null; router.dispose(); document.getElementById('root').replaceChildren(); }
async function input(node, value) {
  assert.equal(node.disabled, false);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(node, value);
    node.dispatchEvent(new window.Event('input', { bubbles: true, composed: true }));
    node.dispatchEvent(new window.Event('change', { bubbles: true, composed: true }));
  });
}
async function click(node) { assert.ok(node); assert.equal(node.disabled ?? false, false); await act(async () => node.click()); await flush(); }
function assertVisibleList(items) {
  const list = section(); assert.ok(list, 'Generated summaries are a directly visible section with a heading');
  assert.equal(list.getAttribute('aria-labelledby'), list.querySelector(':scope>h2').id);
  assert.equal(list.closest('details:not([open])'), null);
  const links = [...list.querySelectorAll('a.list-row')];
  assert.deepEqual(links.map(link => link.getAttribute('href')), items.map(item => `/summaries/${item.summary_id}`));
  links.forEach((link, index) => {
    const item = items[index];
    assert.equal(link.closest('details:not([open])'), null, 'No summary needs an expand action');
    assert.equal(link.querySelector('strong').textContent, `${item.from_date} 至 ${item.to_date}`);
    assert.equal(link.querySelector('small').textContent, `${eventTimeLabel(item.generated_at)} · ${item.needs_regeneration ? '需要重新生成' : '查看摘要'}`);
  });
  assert.equal(JSON.stringify(summaries), originalSummaries);
}
// Match actual stylesheet media at the reference 390 × 844 portrait viewport.
// Ordinary light preferences; this is declaration resolution, never geometry.
function referenceMedia(query) {
  return query.split(',').some(branch => {
    const source = branch.trim();
    if (source === 'print') return false;
    assert.equal(source.replace(/\([^)]*\)|\band\b|\s/g, ''), '', `Unsupported media query: ${query}`);
    return [...source.matchAll(/\(([^)]+)\)/g)].every(([, condition]) => {
      const term = condition.replace(/\s/g, '');
      const bound = /^(min|max)-(width|height):(\d+)px$/.exec(term);
      if (bound) { const actual = bound[2] === 'width' ? 390 : 844; return bound[1] === 'min' ? actual >= Number(bound[3]) : actual <= Number(bound[3]); }
      if (term === 'orientation:portrait' || term === 'prefers-color-scheme:light') return true;
      if (['orientation:landscape', 'prefers-color-scheme:dark', 'prefers-reduced-motion:reduce', 'prefers-contrast:more', 'forced-colors:active'].includes(term)) return false;
      throw new Error(`Unsupported media condition: ${term}`);
    });
  });
}
try {
  await mount();
  assertVisibleList(summaries);
  assert.ok(section().textContent.includes('2026-09-27 20:20:43.123 UTC+08:00'));
  assert.ok(section().textContent.includes('2026-10-13 05:06:07.987 UTC'));
  assert.equal(page().querySelector('.rich-record-patient strong').textContent, initialAccess().relations[0].display_id);
  assert.deepEqual(dates().map(node => node.value), ['', ''], 'No dates inferred from the available summaries');
  assert.deepEqual(dates().map(node => node.parentElement.firstElementChild.textContent), ['开始日期', '结束日期']);
  for (const date of dates()) { assert.equal(date.parentElement.control, date); assert.equal(date.required, true); assert.equal(date.disabled, false); assert.equal(date.autocomplete, 'off'); }
  assert.equal(dates()[1].hasAttribute('min'), false); assert.equal(submit().disabled, true);
  assert.deepEqual([...page().querySelectorAll('input[type=checkbox]')].map(node => node.checked), [true, true, true, true, false]);
  assert.equal(page().querySelector('a[href="/summaries/structure"]').textContent, '查看摘要页面结构');
  assert.equal(page().textContent.includes('最近摘要'), false, 'Do not invent recency labels');

  // Read every real UI2Frame stylesheet in its production order and test declarations.
  const frameUrl = new URL('../src/components/UI2Frame.tsx', import.meta.url);
  const cssFiles = [...readFileSync(frameUrl, 'utf8').matchAll(/import\s+\w+\s+from\s+['"]([^'"]+\.css\?inline)['"]/g)].map(match => fileURLToPath(new URL(match[1], frameUrl)));
  assert.deepEqual([...loadedCss.keys()], cssFiles);
  const css = cssFiles.map(path => readFileSync(path, 'utf8')).join('');
  assert.equal(page().querySelector('style').textContent, css);
  const grid = page().querySelector('.field-grid');
  assert.equal(winningDeclaration(css, grid, 'display', referenceMedia)?.value, 'grid');
  assert.deepEqual(winningDeclaration(css, grid, 'grid-template-columns', referenceMedia), { value: '1fr', important: false, specificity: [0, 3, 0], selector: '.device[data-page=UI2-13] .field-grid' });
  const negativePage = page().querySelector('.device').cloneNode(true); negativePage.dataset.page = 'UI2-05';
  assert.equal(winningDeclaration(css, negativePage.querySelector('.field-grid'), 'grid-template-columns', referenceMedia)?.value, '1fr 1fr', 'The summary-date rule must not affect other pages');

  await input(dates()[0], '2026-09-20'); assert.equal(dates()[1].min, '2026-09-20'); assert.equal(submit().disabled, true);
  await input(dates()[1], '2026-09-19'); assert.equal(dates()[1].validity.rangeUnderflow, true); assert.equal(submit().disabled, true);
  await act(async () => page().querySelector('form').dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true, composed: true })));
  assert.equal(calls.filter(call => call.method !== 'GET').length, 0, 'Invalid range cannot request generation');
  await input(dates()[0], '2026-09-21'); await input(dates()[1], '2026-09-28');
  assert.deepEqual(dates().map(node => node.value), ['2026-09-21', '2026-09-28']); assert.equal(submit().disabled, false);
  // Follow the actual submission callback only into a synthetic failed intent.
  let releaseIntent;
  responder = (path, options) => options.method === 'POST' ? new Promise(resolve => { releaseIntent = resolve; }) : normalResponse(path);
  await click(submit()); assert.equal(submit().textContent, '正在核验生成结果'); assert.equal(submit().disabled, true); assert.ok(dates().every(node => node.disabled));
  const write = calls.find(call => call.method === 'POST');
  assert.equal(write.path, '/api/write-intents'); assert.equal(write.body.target_key, listPath);
  assert.deepEqual(write.body.payload, { from_date: '2026-09-21', to_date: '2026-09-28', scope_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone, include_unknown_time: false, record_kinds: ['observation', 'report', 'therapy'], include_source_status: true });
  await act(async () => releaseIntent(failure('合成生成失败，原请求尚待核验'))); await flush();
  assert.ok(page().textContent.includes('生成结果尚未确认')); assert.ok(page().textContent.includes('合成生成失败，原请求尚待核验'));
  assert.ok(page().textContent.includes('查询原请求并继续')); assert.ok(dates().every(node => node.disabled));
  assert.deepEqual(dates().map(node => node.value), ['2026-09-21', '2026-09-28']); assertVisibleList(summaries);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  await unmount();

  // Loading and empty states remain inside the visible generated-list section.
  let releaseList;
  await mount(path => path === listPath ? new Promise(resolve => { releaseList = resolve; }) : replyDirectory());
  assert.ok(section().textContent.includes('正在读取当前资料')); assert.equal(section().querySelectorAll('a').length, 0);
  await act(async () => releaseList(replyList([]))); await flush();
  assertVisibleList([]); assert.ok(section().textContent.includes('尚无已生成的摘要')); assert.equal(submit().disabled, true);
  await unmount();

  // Errors/retry stay available; a failed request directory cannot unlock generation.
  await mount(path => failure(path === listPath ? '合成摘要读取失败' : '合成请求目录读取失败'));
  assert.ok(section().textContent.includes('合成摘要读取失败')); assert.ok(page().textContent.includes('合成请求目录读取失败'));
  await input(dates()[0], '2026-10-01'); await input(dates()[1], '2026-10-12'); assert.equal(submit().disabled, true);
  responder = path => path === listPath ? replyList([]) : failure('合成请求目录读取失败');
  await click([...section().querySelectorAll('button')].find(node => node.textContent === '重新读取资料'));
  assertVisibleList([]); assert.ok(section().textContent.includes('尚无已生成的摘要')); assert.equal(submit().disabled, true);
  await unmount();

  // Existing saved-request lock is independent of the generated-list visibility.
  await mount(path => path === listPath ? replyList() : replyDirectory([pendingRequest]));
  assertVisibleList(summaries); assert.ok(dates().every(node => node.disabled)); assert.equal(submit().disabled, true);
  assert.deepEqual(dates().map(node => node.value), ['', ''], 'A pending request does not silently adopt its dates');
  const savedRequests = [...page().querySelectorAll('details')].find(node => node.querySelector(':scope>summary')?.textContent === '已保存的生成请求');
  assert.equal(savedRequests.open, true); assert.ok(savedRequests.textContent.includes(eventTimeLabel(pendingRequest.prepared_at)));
  assert.ok(savedRequests.textContent.includes('2026-10-01 至 2026-10-12 · Asia/Shanghai'));
  assert.ok(savedRequests.textContent.includes('查询原请求并继续'));
  await unmount();

  await mount(normalResponse, { principalId: null, patientId: null, csrfToken: null, relations: [] });
  assert.equal(calls.length, 0); assert.equal(section(), undefined); assert.ok(dates().every(node => node.disabled)); assert.equal(submit().disabled, true);
  assert.ok(page().textContent.includes('当前访问关系需要核验')); assert.equal(page().querySelector('.rich-record-patient'), null);
  console.log('PASS S12-A actual-route DOM: visible generated rows in exact service order, immutable summary links/full offset times/stale labels; empty date defaults and both callbacks/invalid-range guard; unchanged generation payload, busy/error/recovery lock; list loading/empty/error/retry, saved-request lock and denied access; actual stylesheet one-column cascade at ordinary reference 390 and negative-page scope. Synthetic fetch only; no browser-pixel claim.');
} finally {
  if (root) await unmount();
  dom.window.close();
}
