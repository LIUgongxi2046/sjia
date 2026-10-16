// Production page/provider, synthetic responses only; no browser or backend.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { syntheticSession, syntheticBootstrap } from './response-test-fixtures.mjs';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/login' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Event'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { createMemoryRouter, RouterProvider, Outlet } = await import('react-router');
const { AccessProvider } = await import('../src/state/access.tsx');
const { LoginPage } = await import('../src/features/account/LoginPage.tsx');
const { UI2FamilyFrame } = await import('../src/components/UI2FamilyFrame.tsx');
const actor = randomUUID(), patient = randomUUID();
const failure = status => Response.json({ code: 'SYNTHETIC_FAILURE', message: `合成访问失败 ${status}`, retryable: status === 503, recovery: status === 401 ? 'reauthenticate' : 'retry_after' }, { status });
const run = async fn => act(async () => { await fn(); await new Promise(done => setTimeout(done, 5)); });
let root, router;
const page = () => document.querySelector('[data-screen-id]')?.shadowRoot;
async function mount(state) {
  globalThis.fetch = async path => {
    if (path === '/api/auth/bootstrap') return Response.json(syntheticBootstrap());
    if (path === '/api/session') return state === 'session-error' ? failure(503) : state === 'anonymous' ? failure(401) : Response.json(syntheticSession(actor, null));
    if (path === '/api/relations') return state === 'denied' ? failure(403) : state === 'relation-error' ? failure(503) : Response.json({ relations: state === 'linked' ? [{
      relation_id: randomUUID(), patient_id: patient, display_id: '合成患儿', status: 'active', version: '1', valid_from: '2020-01-01T00:00:00Z', valid_until: null,
      authorization_scope_fingerprint: 'a'.repeat(64), authorization_scope_valid_until: null, actions: ['read_raw'],
    }] : [] });
    throw new Error(`Unexpected request ${path}`);
  };
  root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([{ element: h(AccessProvider, null, h(Outlet)), children: [
    { path: '/login', element: h(LoginPage) },
    { path: '/other', element: h(UI2FamilyFrame, { title: '患儿管理', screenId: 'UI2-18', note: '合成说明', backTo: '/login' }, h('p', null, '合成页面')) },
    { path: '/account/data-use', element: h('p', null, '合成资料使用说明') },
  ] }], { initialEntries: ['/login'] });
  await run(() => root.render(h(RouterProvider, { router })));
  await run(() => new Promise(done => setTimeout(done, 30)));
}
async function dispose() { await run(() => root.unmount()); router.dispose(); }
const checks = [];
try {
  await mount('anonymous');
  for (const mode of ['login', 'register', 'login']) {
    const title = mode === 'login' ? '登录' : '注册账号';
    assert.equal(page().querySelector('h1').textContent, title);
    assert.ok(page().querySelector('form h3'), page().textContent);
    assert.equal(page().querySelector('form h3').textContent, title);
    assert.equal(page().querySelector('form').getAttribute('aria-label'), title);
    assert.equal(page().querySelector('button[type=submit]').textContent, mode === 'login' ? '登录' : '注册并登录');
    assert.equal(page().querySelector('.header-kicker'), null);
    assert.equal(page().querySelector('.header-subtitle'), null);
    assert.equal(page().querySelector('input[type=password]').getAttribute('minlength'), mode === 'register' ? '8' : null);
    assert.equal(page().querySelector('input[type=password]').autocomplete, mode === 'register' ? 'new-password' : 'current-password');
    assert.equal(page().querySelectorAll('.login-brand img').length, 1);
    assert.equal(page().querySelector('.login-brand h2').textContent, 'sJIA 守护者联盟');
    assert.equal(page().querySelector('.note.blue'), null);
    await run(() => page().querySelector('form button[type=button]').click());
  }
  checks.push('login/register/repeated switch has concise titles, no default kicker/subtitle/warning, preserved brand and eight-character registration requirement');
  await run(() => router.navigate('/other'));
  assert.equal(page().querySelector('.header-kicker').textContent, '家庭记录');
  assert.equal(page().querySelector('.header-subtitle').textContent, '查看本人关联');
  checks.push('other family pages retain their default kicker and subtitle');
  await run(() => router.navigate('/login'));
  await run(() => page().querySelector('.text-action').click());
  assert.equal(router.state.location.pathname, '/account/data-use');
  checks.push('data-use explanation remains reachable');
  await dispose();
  for (const state of ['linked', 'unlinked', 'session-error', 'relation-error', 'denied']) {
    await mount(state);
    assert.equal(page().querySelector('form'), null);
    assert.equal(page().querySelector('.header-kicker').textContent, '家庭记录');
    assert.equal(page().querySelector('.header-subtitle').textContent, '查看当前登录身份');
    if (state === 'linked') assert.match(page().querySelector('.login-ready').textContent, /当前账号已登录.*可以访问本人获准的患儿资料/);
    else if (state === 'unlinked') {
      assert.match(page().querySelector('.login-ready').textContent, /当前尚无可访问的患儿资料，请先核验患儿关联/);
      assert.match(page().querySelector('.form-card').textContent, /尚无有效关联.*尚无患儿访问权限/);
    } else {
      assert.match(page().querySelector('[role=alert]').textContent, /合成访问失败/);
      assert.match(page().querySelector('[role=alert]').textContent, state === 'session-error' ? /账号会话暂未核验/ : /当前账号已登录.*患儿关联暂时无法读取/);
      assert.ok([...page().querySelectorAll('button')].some(button => button.textContent === '重新核验访问权限'));
    }
    checks.push(`${state}: original state explanation and recovery preserved`);
    await dispose();
  }
} finally { dom.window.close(); }
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks }, null, 2));
