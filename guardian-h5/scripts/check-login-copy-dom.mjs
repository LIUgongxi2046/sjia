// Actual login page and info sheet; no browser, backend, or authentication requests.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { JSDOM } from 'jsdom';

registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });

const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/login' });
for (const name of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Event', 'KeyboardEvent']) {
  Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true });
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let requests = 0;
globalThis.fetch = async () => { requests += 1; throw new Error('The login info sheet must not make requests.'); };

const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { LoginPage } = await import('../src/features/account/LoginPage.tsx');
const root = createRoot(document.getElementById('root'));
const router = createMemoryRouter([{ path: '/login', element: h(LoginPage) }], { initialEntries: ['/login'] });
const expectedNote = '使用账号和密码完成身份核验。登录后仍需取得患儿关联与相应授权，才能访问患儿资料。';

function page() { return document.querySelector('[data-screen-id="UI2-17"]')?.shadowRoot; }
async function click(selector) {
  const button = page().querySelector(selector);
  assert.ok(button, selector);
  await act(async () => button.click());
}
function checkNote(formLabel) {
  const dialog = page().querySelector('[role="dialog"][aria-label="页面说明"]');
  assert.ok(dialog);
  assert.equal(dialog.querySelector('p').textContent, expectedNote);
  assert.ok(!dialog.textContent.includes('身份方式尚需配置'));
  assert.ok(page().querySelector(`form[aria-label="${formLabel}"]`));
  assert.ok(!page().textContent.includes('知道患儿标识不代表已取得资料访问权。'));
  assert.equal(page().querySelector('.header-kicker'), null);
  assert.equal(page().querySelector('.header-subtitle'), null);
  assert.ok(!page().textContent.includes('当前账号已登录'));
}

try {
  await act(async () => root.render(h(RouterProvider, { router })));
  assert.equal(page().querySelector('[role="dialog"]'), null);
  await click('button[aria-label="页面说明"]');
  checkNote('登录');
  await click('button[aria-label="关闭说明"]');
  assert.equal(page().querySelector('[role="dialog"]'), null);
  assert.equal(page().querySelector('.phone-scroll').hasAttribute('inert'), false);
  await click('form button[type="button"]');
  await click('button[aria-label="页面说明"]');
  checkNote('注册账号');
  await act(async () => page().querySelector('[role="dialog"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
  assert.equal(page().querySelector('[role="dialog"]'), null);
  assert.equal(page().querySelector('.phone-scroll').hasAttribute('inert'), false);
  assert.equal(router.state.location.pathname, '/login');
  assert.equal(requests, 0);
} finally {
  await act(async () => root.unmount());
  dom.window.close();
}

console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks: [
  'login and registration info describes password authentication and separate patient authorization',
  'default permission warning removed; page explanation preserved without fabricated login success',
  'close and Escape dismiss the info sheet without navigation or authentication requests',
] }, null, 2));
