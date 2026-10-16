// Actual drafts page/resource/schema/transport with synthetic fetch only.
// No browser, services, DB or pixel-layout claim; rectangles below only enable sheet focus handling.
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
  if (url.endsWith('/state/access.tsx')) return { format: 'module', source: 'export function useAccess(){return globalThis.__draftAccess;}', shortCircuit: true };
  if (url.startsWith('file:') && new URL(url).pathname.endsWith('.css')) {
    const path = fileURLToPath(new URL(url)), css = readFileSync(path, 'utf8');
    loadedCss.set(path, css);
    return { format: 'module', source: `export default ${JSON.stringify(css)};`, shortCircuit: true };
  }
  if (/\.(png|jpg|webp|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/drafts', pretendToBeVisual: true });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLButtonElement', 'Element', 'Node', 'ShadowRoot']) {
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
}
window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{ width: 1, height: 1 }]; };
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { UI2DraftsPage } = await import('../src/features/account/UI2DraftsPage.tsx');
const { validateFamilyRead } = await import('../src/api/familyReadSchema.ts');
const { navigationMemory } = await import('../src/state/navigation.ts');
const { readReturnContext } = await import('../src/components/ContextLink.tsx');

const patient = randomUUID(), principal = randomUUID(), recordId = randomUUID();
const categories = ['全部', '体温症状', '报告', '治疗', '记录更正'];
const labels = { observation: '体温症状', report: '报告', therapy: '治疗', correction: '记录更正' };
const drafts = Object.freeze(['observation', 'report', 'therapy', 'correction'].map((form_type, index) => Object.freeze({
  draft_id: randomUUID(), patient_id: patient, form_type, version: String(index + 1), status: 'editing',
  saved_at: '2026-10-13T12:34:56.123+08:00', payload: Object.freeze(form_type === 'correction' ? { recordId, version: 2 } : {}),
})));
const correction = drafts[3], originalDrafts = JSON.stringify(drafts);
validateFamilyRead('DraftList', { drafts });
const listPath = `/api/patients/${patient}/drafts`, abandonPath = `/api/drafts/${correction.draft_id}/abandon`;
const targetPath = draft => draft.form_type === 'correction' ? `/records/${recordId}/correct?draft=${draft.draft_id}`
  : `/register/${{ observation: 'observations', report: 'reports', therapy: 'therapies' }[draft.form_type]}?draft=${draft.draft_id}`;
const initialAccess = () => ({ principalId: principal, patientId: patient, csrfToken: 'synthetic-csrf', loading: false, error: null,
  relations: [{ patient_id: patient, display_id: '合成患儿原标识（仅测试）' }], refresh: async () => {} });
const replyList = (items = drafts) => Response.json({ drafts: items });
const failure = message => Response.json({ code: 'SYNTHETIC_UNAVAILABLE', message, retryable: true, recovery: 'reload' }, { status: 503 });
let root, router, responder, calls;
const page = () => document.querySelector('[data-screen-id]')?.shadowRoot;
const tabs = () => [...page().querySelectorAll('[role=tablist][aria-label="草稿类别"] [role=tab]')];
const tab = text => tabs().find(node => node.textContent === text);
const cards = () => [...page().querySelectorAll('section.card.form-card')];
const dialog = () => page().querySelector('[role=dialog][aria-label="放弃草稿"]');
const button = (text, scope = page()) => [...scope.querySelectorAll('button')].find(node => node.textContent === text);
const flush = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
const writes = () => calls.filter(call => call.method !== 'GET');
globalThis.fetch = async (path, options = {}) => {
  assert.ok([listPath, abandonPath].includes(String(path)), `Unexpected synthetic request: ${path}`);
  calls.push({ path: String(path), method: options.method, body: options.body ? JSON.parse(options.body) : undefined, headers: options.headers, signal: options.signal, credentials: options.credentials });
  return responder(String(path), options);
};
async function mount({ response = () => replyList(), access = {}, path = '/drafts' } = {}) {
  navigationMemory.clear(); responder = response; calls = []; globalThis.__draftAccess = { ...initialAccess(), ...access };
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ path: '/drafts', element: h(UI2DraftsPage) }, { path: '*', element: h('p', null, 'Synthetic destination') }], { initialEntries: ['/records', path] });
  await act(async () => root.render(h(RouterProvider, { router }))); await flush(); await flush();
}
async function unmount() { await act(async () => root.unmount()); root = null; router.dispose(); document.getElementById('root').replaceChildren(); }
async function click(node) { assert.ok(node, 'Expected actual DOM control'); assert.equal(node.disabled ?? false, false); await act(async () => node.click()); await flush(); }
function assertCards(items) {
  assert.deepEqual(cards().map(card => card.querySelector('h2').textContent), items.map(item => `${labels[item.form_type]}草稿`));
  assert.deepEqual(cards().map(card => card.querySelector('a').getAttribute('href')), items.map(targetPath));
  for (const [index, card] of cards().entries()) {
    assert.equal(card.querySelector('a').textContent, '继续编辑'); assert.equal(button('放弃草稿', card).disabled, false);
    assert.ok(card.textContent.includes(`保存版本${items[index].version}`));
    assert.ok(card.textContent.includes('保存时间2026-10-13 12:34:56.123 UTC+08:00'));
  }
  assert.equal(JSON.stringify(drafts), originalDrafts);
}
function assertTabs(selected = '全部') {
  assert.deepEqual(tabs().map(node => node.textContent), categories);
  assert.equal(page().querySelectorAll('[role=tablist][aria-label="草稿类别"]').length, 1);
  assert.equal(page().querySelectorAll('select').length, 0, 'No split bottom category selector');
  for (const node of tabs()) {
    assert.equal(node.tagName, 'BUTTON'); assert.equal(node.disabled, false); assert.equal(node.tabIndex, 0);
    assert.equal(node.closest('details'), null, 'All five categories are directly accessible');
    assert.equal(node.getAttribute('aria-selected'), String(node.textContent === selected));
  }
  const help = [...page().querySelectorAll('details')].find(node => node.querySelector('summary')?.textContent === '恢复草稿时需要注意什么');
  assert.ok(help); assert.equal(help.querySelectorAll('p').length, 2); assert.equal(help.querySelectorAll('input,select,button').length, 0);
  assert.ok(help.textContent.includes('选择草稿后，系统检查本人身份、患儿权限、保存状态和记录版本。'));
  assert.ok(help.textContent.includes('已提交、已放弃或到期的草稿不能恢复。'));
  const content = page().querySelector('.page-content');
  assert.equal(content.querySelector('[role=tablist]').parentElement, content);
  if (cards().length) assert.ok(content.querySelector('[role=tablist]').compareDocumentPosition(cards()[0]) & Node.DOCUMENT_POSITION_FOLLOWING);
}
// Resolve ordinary portrait CSS declarations at the reference viewport, not pixels.
function referenceMedia(query) {
  return query.split(',').some(branch => {
    const source = branch.trim(); if (source === 'print') return false;
    assert.equal(source.replace(/\([^)]*\)|\band\b|\s/g, ''), '', `Unsupported media query: ${query}`);
    return [...source.matchAll(/\(([^)]+)\)/g)].every(([, condition]) => {
      const term = condition.replace(/\s/g, ''), bound = /^(min|max)-(width|height):(\d+)px$/.exec(term);
      if (bound) { const actual = bound[2] === 'width' ? 390 : 844; return bound[1] === 'min' ? actual >= Number(bound[3]) : actual <= Number(bound[3]); }
      if (['orientation:portrait', 'prefers-color-scheme:light'].includes(term)) return true;
      if (['orientation:landscape', 'prefers-color-scheme:dark', 'prefers-reduced-motion:reduce', 'prefers-contrast:more', 'forced-colors:active'].includes(term)) return false;
      throw new Error(`Unsupported media condition: ${term}`);
    });
  });
}
try {
  await mount(); assertTabs(); assertCards(drafts);
  const frameUrl = new URL('../src/components/UI2Frame.tsx', import.meta.url);
  const cssFiles = [...readFileSync(frameUrl, 'utf8').matchAll(/import\s+\w+\s+from\s+['"]([^'"]+\.css\?inline)['"]/g)].map(match => fileURLToPath(new URL(match[1], frameUrl)));
  assert.deepEqual([...loadedCss.keys()], cssFiles);
  const css = cssFiles.map(path => readFileSync(path, 'utf8')).join(''); assert.equal(page().querySelector('style').textContent, css);
  for (const node of tabs()) {
    for (const [property, value] of [['flex', '1'], ['min-width', '0'], ['min-height', '44px'], ['white-space', 'normal']]) {
      assert.equal(winningDeclaration(css, node, property, referenceMedia)?.value, value, `${node.textContent}: ${property}`);
    }
    assert.notEqual(winningDeclaration(css, node, 'text-overflow', referenceMedia)?.value, 'ellipsis');
  }
  for (const label of ['记录更正', '报告', '体温症状', '治疗', '全部', '记录更正', '全部']) {
    await click(tab(label)); assertTabs(label); assertCards(label === '全部' ? drafts : drafts.filter(item => labels[item.form_type] === label));
  }
  assert.equal(calls.length, 1, 'Category switches neither refetch nor write');
  await click(tab('记录更正')); const link = cards()[0].querySelector('a');
  const originKey = router.state.location.key; page().querySelector('.phone-scroll').scrollTop = 173;
  await click(link);
  assert.equal(router.state.location.pathname + router.state.location.search, targetPath(correction));
  assert.deepEqual(readReturnContext(router.state.location.state), { pathname: '/drafts', search: '', key: originKey, scroll: 173 });
  await act(async () => router.navigate(-1)); await flush(); await flush();
  assert.equal(router.state.historyAction, 'POP'); assert.equal(router.state.location.pathname, '/drafts');
  assertTabs(); assertCards(drafts); // Existing page-local category resets on native-history remount.
  await click(tab('记录更正')); await click(button('放弃草稿', cards()[0]));
  assert.ok(dialog()); assert.ok(dialog().textContent.includes(`保存版本${correction.version}`));
  assert.equal(page().querySelector('.phone-scroll').hasAttribute('inert'), true);
  await click(button('保留草稿', dialog())); assert.equal(dialog(), null); assertTabs('记录更正'); assertCards([correction]);
  assert.equal(page().querySelector('.phone-scroll').hasAttribute('inert'), false);
  await click(button('放弃草稿', cards()[0]));
  await act(async () => dialog().dispatchEvent(new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
  assert.equal(dialog(), null); assertCards([correction]); assert.equal(writes().length, 0, 'Both cancel paths make no writes');
  await click(page().querySelector('button[aria-label="返回上级页面"]')); assert.equal(router.state.location.pathname, '/records');
  await unmount();

  await mount({ path: `/drafts?draft=${correction.draft_id}` }); assertCards([correction]);
  await click(tab('报告')); assertCards([]); assert.ok(page().textContent.includes('当前类别没有可恢复的草稿'));
  assert.equal(page().textContent.includes('当前目标草稿无法恢复'), false);
  await click(tab('记录更正')); assertCards([correction]); await click(tab('全部')); assertCards([correction]);
  assert.equal(router.state.location.search, `?draft=${correction.draft_id}`); await unmount();
  await mount({ path: `/drafts?draft=${randomUUID()}` }); assertCards([]);
  assert.ok(page().textContent.includes('当前目标草稿无法恢复')); assert.equal(page().querySelector('a[href="/drafts"]').textContent, '查看全部获准草稿');
  await click(page().querySelector('a[href="/drafts"]')); assertCards(drafts); await unmount();

  let releaseList;
  await mount({ response: () => new Promise(resolve => { releaseList = resolve; }) });
  assertTabs(); assertCards([]); assert.ok(page().textContent.includes('正在读取当前资料')); assert.equal(page().textContent.includes('没有可恢复的草稿'), false);
  await act(async () => releaseList(replyList([]))); await flush();
  assert.ok(page().textContent.includes('没有可恢复的草稿')); await click(tab('记录更正')); assert.ok(page().textContent.includes('当前类别没有可恢复的草稿')); await unmount();
  await mount({ response: () => failure('合成草稿读取失败') }); assertTabs(); assertCards([]);
  assert.ok(page().textContent.includes('合成草稿读取失败')); assert.equal(page().textContent.includes('没有可恢复的草稿'), false);
  responder = () => replyList(); await click(button('重新读取资料')); assertCards(drafts); await unmount();

  // Abandon still sends the precise draft/version, waits while busy, and locks retry after a newer version is read.
  let releaseAbandon; let list = drafts;
  await mount({ response: (path, options) => options.method === 'POST' ? new Promise(resolve => { releaseAbandon = resolve; }) : replyList(list) });
  await click(tab('记录更正')); await click(button('放弃草稿', cards()[0])); await click(button('确认放弃', dialog()));
  assert.equal(button('正在提交放弃操作', dialog()).disabled, true); assert.equal(button('保留草稿', dialog()).disabled, true);
  assert.equal(writes().length, 1); const write = writes()[0];
  assert.equal(write.path, abandonPath); assert.deepEqual(write.body, { expected_version: correction.version });
  assert.equal(write.headers['X-CSRFToken'], 'synthetic-csrf'); assert.equal(write.credentials, 'same-origin'); assert.ok(write.signal instanceof AbortSignal);
  await click(dialog().querySelector('[aria-label="关闭放弃草稿"]')); assert.ok(dialog(), 'Busy close remains blocked');
  list = drafts.map(item => item === correction ? { ...item, version: '5' } : item);
  await act(async () => releaseAbandon(failure('合成放弃回执失败'))); await flush(); await flush();
  assert.ok(dialog().textContent.includes('放弃结果需要核验')); assert.ok(dialog().textContent.includes('草稿版本已经改变'));
  assert.equal(button('确认放弃', dialog()).disabled, true); assert.equal(writes().length, 1);
  await click(button('关闭并查看当前草稿', dialog())); assertTabs('记录更正'); assertCards([list[3]]); await unmount();

  await mount({ access: { principalId: null, patientId: null, csrfToken: null, relations: [] } });
  assertTabs(); assertCards([]); assert.equal(calls.length, 0); assert.ok(page().textContent.includes('当前访问关系需要核验'));
  assert.equal(page().querySelector('.rich-record-patient'), null); assert.equal(button('放弃草稿'), undefined); await unmount();
  console.log('PASS S14 actual-page DOM: all five top categories, no split select, retained recovery text; repeated exact category/reset and requestedId intersection; precise four form routes/correction ID and return context, native-history remount/header back; abandon keep/Escape/busy/version retry guards and unchanged POST body; loading/empty/error/retry and denied-access gates; real stylesheet wrapping/flex/44px declarations. Synthetic fetch only, no browser-pixel claim.');
} finally {
  if (root) await unmount();
  dom.window.close();
}
