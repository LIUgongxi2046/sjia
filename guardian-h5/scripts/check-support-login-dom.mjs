// Production support login with synthetic deferred requests; no browser or backend.
import './registration-test-loader.mjs';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
import { randomUUID } from 'node:crypto';
import { JSDOM } from 'jsdom';
import { syntheticBootstrap, fixtureTime } from './response-test-fixtures.mjs';
registerHooks({ load(url, context, nextLoad) {
  if (/\.(png|webp|jpg|svg)$/.test(url)) return { format: 'module', source: `export default ${JSON.stringify(url)};`, shortCircuit: true };
  return nextLoad(url, context);
} });
const dom = new JSDOM('<!doctype html><div id="root"></div>', { url: 'http://localhost/support/login' });
for (const key of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Event'])
  Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true });
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createElement: h, act } = await import('react');
const { createRoot } = await import('react-dom/client');
const { Simulate } = await import('react-dom/test-utils');
const { createMemoryRouter, RouterProvider } = await import('react-router');
const { SupportLogin } = await import('../src/features/support/SupportApp.tsx');
const { setSessionCsrfToken, getSessionCsrfToken } = await import('../src/api/client.ts');
const bootstrapPath = '/api/auth/bootstrap', loginPath = '/api/v2/support/session/login';
const session = () => ({ principal_id: randomUUID(), account_status: 'active', expires_at: fixtureTime,
  audience: 'support', capabilities: [], csrf_token: 'synthetic-authenticated' });
const failure = status => Response.json({ code: 'SYNTHETIC_FAILURE', message: '合成登录失败', retryable: false, recovery: 'none' }, { status });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const run = async fn => act(async () => { await fn(); await new Promise(done => setTimeout(done, 5)); });
let root, router, adopted, calls;
function server(replies = {}) {
  calls = []; setSessionCsrfToken(null);
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options, body: options.body && JSON.parse(options.body) });
    if (path in replies) return await replies[path];
    if (path === bootstrapPath) return Response.json(syntheticBootstrap('synthetic-bootstrap'));
    if (path === loginPath) return Response.json(session());
    throw new Error(`Unexpected request ${path}`);
  };
}
async function mount() {
  adopted = []; root = createRoot(document.getElementById('root'));
  router = createMemoryRouter([
    { path: '/support/login', element: h(SupportLogin, { onAuthenticated: value => adopted.push(value) }) },
    { path: '/support/data-use', element: h('p', null, 'Synthetic authenticated destination') },
    { path: '/account', element: h('p', null, 'Synthetic account destination') },
  ], { initialEntries: ['/support/login'] });
  await run(() => root.render(h(RouterProvider, { router })));
  await run(() => Simulate.change(page().querySelector('input[autocomplete=username]'), { target: { value: 'synthetic-support' } }));
  await run(() => Simulate.change(page().querySelector('input[type=password]'), { target: { value: 'synthetic-not-a-secret' } }));
}
const page = () => document.querySelector('[data-screen-id="UI2-17"]')?.shadowRoot;
async function dispose() { await run(() => root.unmount()); router.dispose(); document.getElementById('root').replaceChildren(); }
const checks = [];
for (const stage of ['bootstrap', 'login']) {
  server({ [stage === 'bootstrap' ? bootstrapPath : loginPath]: failure(stage === 'login' ? 401 : 503) }); await mount();
  await run(() => Simulate.submit(page().querySelector('form')));
  assert.equal(page().querySelector('input[type=password]').value, '');
  assert.equal(page().querySelector('input[type=password]').disabled, false);
  assert.equal(adopted.length, 0); assert.equal(router.state.location.pathname, '/support/login');
  assert.match(page().querySelector('[role=alert]').textContent, /合成登录失败/);
  if (stage === 'bootstrap') assert.equal(calls.some(call => call.path === loginPath), false);
  await dispose(); checks.push(`${stage} failure clears password without claiming authentication`);
}
{
  const gate = deferred(); server({ [loginPath]: gate.promise }); await mount();
  await run(() => { Simulate.submit(page().querySelector('form')); Simulate.submit(page().querySelector('form')); });
  assert.equal(page().querySelector('input[type=password]').value, '');
  assert.equal(page().querySelector('input[type=password]').disabled, true);
  assert.equal(calls.filter(call => call.path === bootstrapPath).length, 1);
  assert.equal(calls.filter(call => call.path === loginPath).length, 1);
  assert.equal(calls.find(call => call.path === loginPath).body.password, 'synthetic-not-a-secret');
  await run(() => gate.resolve(Response.json(session())));
  assert.equal(adopted.length, 1); assert.equal(router.state.location.pathname, '/support/data-use');
  assert.equal(getSessionCsrfToken(), 'synthetic-authenticated');
  await dispose(); checks.push('pending credentials clear immediately; duplicate submit is fenced; successful session navigates once');
}
for (const stage of ['bootstrap', 'login']) {
  const gate = deferred(); server({ [stage === 'bootstrap' ? bootstrapPath : loginPath]: gate.promise }); await mount();
  await run(() => Simulate.submit(page().querySelector('form')));
  await run(() => router.navigate('/account'));
  setSessionCsrfToken('synthetic-new-context');
  await run(() => gate.resolve(stage === 'bootstrap' ? Response.json(syntheticBootstrap('synthetic-old')) : Response.json(session())));
  assert.equal(adopted.length, 0); assert.equal(router.state.location.pathname, '/account');
  assert.equal(getSessionCsrfToken(), 'synthetic-new-context');
  if (stage === 'bootstrap') assert.equal(calls.some(call => call.path === loginPath), false);
  else assert.equal(calls.find(call => call.path === loginPath).options.signal.aborted, true);
  await dispose(); checks.push(`unmount during ${stage} rejects stale login and CSRF/navigation adoption`);
}
dom.window.close();
console.log(JSON.stringify({ status: 'PASS', mode: 'SYNTHETIC_NONVISUAL_DOM', checks }, null, 2));
