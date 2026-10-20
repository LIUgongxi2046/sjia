import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, statfsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';

const { values } = parseArgs({ options: { report: { type: 'string' }, dist: { type: 'string' },
  credentials: { type: 'string' }, scope: { type: 'string' }, source: { type: 'string' } } });
for (const key of ['report', 'dist', 'credentials', 'scope', 'source']) assert.ok(values[key], key);
const root = resolve(import.meta.dirname, '../..');
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const space = statfsSync(root);
assert.ok(space.bavail * space.bsize >= 2 * 1024 ** 3 + 24 * 1024 ** 2);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const scope = JSON.parse(await readFile(resolve(values.scope), 'utf8'));
const source = JSON.parse(await readFile(resolve(values.source), 'utf8'));
assert.equal(source.result, 'PASS'); assert.equal(source.source_kind, 'USER_APPROVED_ORIGINAL');
assert.equal(scope.actor_id, source.actor_id);
const origin = 'http://127.0.0.1:5178';
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), origin,
  checks: [], page_errors: [], new_model_calls: 0, new_clinical_writes: 0,
  visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disk-cache-size=8388608'] });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const main = await context.newPage();
main.setDefaultTimeout(30000);
main.on('pageerror', error => report.page_errors.push(error.message));
const browserSession = await browser.newBrowserCDPSession();
let csrf;
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function record(name, details = {}) { report.checks.push({ name, result: 'PASS', ...details }); await save(); }
async function api(method, path, expected, data) {
  const response = await context.request.fetch(origin + path, { method, data,
    headers: { Origin: origin, ...(csrf ? { 'X-CSRFToken': csrf } : {}) }, timeout: 60000 });
  assert.equal(response.status(), expected, `${method} ${path}`);
  return response.json();
}
async function nativePeer(path) {
  const session = await context.newCDPSession(main);
  const info = await session.send('Target.getTargetInfo');
  const created = context.waitForEvent('page');
  const target = await browserSession.send('Target.createTarget', { url: origin + path,
    browserContextId: info.targetInfo.browserContextId, newWindow: true });
  const page = await created;
  page.setDefaultTimeout(30000);
  page.on('pageerror', error => report.page_errors.push(error.message));
  const first = await browserSession.send('Browser.getWindowForTarget', { targetId: info.targetInfo.targetId });
  const second = await browserSession.send('Browser.getWindowForTarget', { targetId: target.targetId });
  assert.notEqual(first.windowId, second.windowId);
  return page;
}
async function familyLogin(page) {
  await page.goto(origin + '/login');
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL('**/home');
  const session = await api('GET', '/api/session', 200);
  assert.equal(session.principal_id, source.actor_id); assert.equal(session.audience, 'family');
  csrf = session.csrf_token;
  return session;
}
async function documentIds(page) {
  return page.evaluate(() => [...new Set([...document.querySelectorAll('[data-screen-id]')]
    .flatMap(host => [...host.shadowRoot.querySelectorAll('a[href]')]).map(node => new URL(node.href))
    .filter(url => url.pathname === '/archives/source').map(url => url.searchParams.get('document')))].sort());
}
async function patientLabel(page) { return page.locator('.rich-record-patient strong').innerText(); }
async function waitForPatient(page, label) {
  await page.locator('.rich-record-patient strong').filter({ hasText: label }).waitFor();
  assert.equal(await patientLabel(page), label);
}
async function select(page, relation) {
  await page.goto(origin + '/associations');
  const card = page.locator('.rich-child-identity').filter({ has: page.getByRole('heading', { name: relation.display_id, exact: true }) });
  await card.waitFor();
  const button = card.getByRole('button', { name: '选择此患儿' });
  if (await button.count()) {
    await button.click(); await page.waitForURL('**/home');
  } else await card.getByText('当前患儿', { exact: true }).waitFor();
  const actual = await api('GET', '/api/session', 200);
  assert.equal(actual.selected_patient_id, relation.patient_id); csrf = actual.csrf_token;
}
async function archive(page, directory) {
  await page.goto(origin + '/archives');
  await page.getByText(`符合条件 ${directory.matched_count} 份文书；本次读取 ${directory.items.length} 份。`, { exact: true }).waitFor();
  assert.deepEqual(await documentIds(page), directory.items.map(item => item.document.document_id).sort());
}
try {
  const response = await context.request.get(origin + '/home');
  assert.equal(response.status(), 200);
  const bytes = await response.body();
  assert.deepEqual(bytes, await readFile(resolve(values.dist, 'index.html')));
  report.index_sha256 = createHash('sha256').update(bytes).digest('hex');
  await familyLogin(main);
  const relations = (await api('GET', '/api/relations', 200)).relations;
  const directories = [];
  for (const patient of scope.patient_ids) directories.push(await api('POST',
    `/api/v2/patients/${patient}/archives/search`, 200, { category: 'all', limit: 20 }));
  const populated = directories.find(item => item.items.length > 0); assert.ok(populated);
  const populatedRelation = relations.find(item => item.patient_id === populated.patient_id); assert.ok(populatedRelation);
  const otherRelation = relations.find(item => scope.patient_ids.includes(item.patient_id) && item.patient_id !== populated.patient_id);
  assert.ok(otherRelation);
  await select(main, populatedRelation);
  await archive(main, populated);
  const ids = await documentIds(main); assert.ok(ids.length > 0);
  let peer = await nativePeer('/account');
  await peer.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  assert.equal(await main.evaluate(() => document.visibilityState), 'visible');
  assert.equal(await peer.evaluate(() => document.visibilityState), 'visible');
  const started = performance.now();
  const logout = peer.waitForResponse(item => new URL(item.url()).pathname === '/api/session/logout');
  await peer.getByRole('button', { name: '退出登录', exact: true }).click();
  assert.equal((await logout).status(), 200);
  await peer.waitForURL('**/home');
  await waitForPatient(main, '选择患儿');
  assert.deepEqual(await documentIds(main), []);
  await api('GET', '/api/session', 401);
  await record('TWO_VISIBLE_WINDOWS_LOGOUT_CLEARS_REAL_ARCHIVE', { elapsed_ms: performance.now() - started });

  await peer.goto(origin + '/archives');
  await waitForPatient(peer, '选择患儿');
  await familyLogin(main);
  await peer.waitForFunction(() => [...document.querySelectorAll('[data-screen-id]')]
    .some(host => { const value = host.shadowRoot.querySelector('.rich-record-patient strong')?.textContent;
      return value && value !== '选择患儿'; }));
  assert.notEqual(await patientLabel(peer), '选择患儿');
  await record('PEER_ADOPTS_REAL_RELOGIN_WITHOUT_RELOAD');
  await select(main, populatedRelation);
  await archive(main, populated);
  await main.evaluate(() => {
    window.__qaSessionEvents = [];
    addEventListener('storage', event => {
      if (event.key === 'sjia.session.boundary.v1') window.__qaSessionEvents.push(JSON.parse(event.newValue));
    });
  });
  await select(peer, otherRelation);
  await waitForPatient(main, otherRelation.display_id);
  assert.equal((await documentIds(main)).some(id => ids.includes(id)), false);
  await record('REAL_PATIENT_SELECTION_INVALIDATES_PEER_ARCHIVE', { selected_patient_id: otherRelation.patient_id });

  await select(peer, populatedRelation); await archive(main, populated);
  await peer.goto(origin + '/support/login');
  await peer.locator('input[autocomplete="username"]').fill(credentials.username);
  await peer.locator('input[type="password"]').fill(credentials.password);
  const supportLogin = peer.waitForResponse(item => new URL(item.url()).pathname === '/api/v2/support/session/login');
  await peer.getByRole('button', { name: '登录账号', exact: true }).click();
  assert.equal((await supportLogin).status(), 200);
  await peer.waitForURL('**/support/data-use');
  const support = await api('GET', '/api/v2/support/session', 200);
  assert.equal(support.principal_id, source.actor_id);
  await main.waitForFunction(() => ![...document.querySelectorAll('[data-screen-id]')]
    .flatMap(host => [...host.shadowRoot.querySelectorAll('a[href]')])
    .some(node => new URL(node.href).pathname === '/archives/source'));
  assert.deepEqual(await documentIds(main), []);
  await record('SUPPORT_AUDIENCE_CHANGE_CLEARS_FAMILY_CONTENT');
  await peer.getByRole('button', { name: '退出受控会话', exact: true }).click();
  await peer.waitForURL('**/support/login');
  await api('GET', '/api/session', 401);
  await waitForPatient(main, '选择患儿');
  await record('SUPPORT_LOGOUT_UPDATES_SHARED_ANONYMOUS_SESSION');

  await familyLogin(main); await select(main, populatedRelation); await archive(main, populated);
  await peer.close(); peer = await nativePeer('/account');
  await peer.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  const throttle = await context.newCDPSession(peer);
  await throttle.send('Network.enable');
  await throttle.send('Network.emulateNetworkConditions', { offline: false, latency: 6000,
    downloadThroughput: -1, uploadThroughput: -1 });
  const bootstrapRequest = peer.waitForRequest(item => new URL(item.url()).pathname === '/api/auth/bootstrap');
  await peer.getByRole('button', { name: '退出登录', exact: true }).click();
  await bootstrapRequest;
  await main.getByRole('heading', { name: '正在同步登录状态', exact: true }).waitFor();
  assert.deepEqual(await documentIds(main), []);
  const held = await main.evaluate(() => navigator.locks.query());
  assert.equal(held.held.filter(item => item.name === 'sjia.session.mutation.v1' && item.mode === 'exclusive').length, 1);
  await peer.close();
  await main.getByText(`符合条件 ${populated.matched_count} 份文书；本次读取 ${populated.items.length} 份。`, { exact: true }).waitFor();
  assert.deepEqual(await documentIds(main), ids);
  const recovered = await api('GET', '/api/session', 200);
  assert.equal(recovered.principal_id, source.actor_id); assert.equal(recovered.selected_patient_id, populated.patient_id);
  await record('CLOSED_MUTATION_WINDOW_RECOVERS_FROM_ACTUAL_SERVER_SESSION');

  const storage = await main.evaluate(() => ({ local: Object.fromEntries(Object.entries(localStorage)), session: Object.keys(sessionStorage) }));
  assert.deepEqual(storage.session, []);
  assert.deepEqual(Object.keys(storage.local).sort(), ['sjia.session.boundary.v1']);
  const metadata = JSON.parse(storage.local['sjia.session.boundary.v1']);
  assert.deepEqual(Object.keys(metadata).sort(), ['owner', 'phase', 'version']);
  assert.equal(metadata.phase, 'settled');
  await main.reload();
  await main.getByText(`符合条件 ${populated.matched_count} 份文书；本次读取 ${populated.items.length} 份。`, { exact: true }).waitFor();
  assert.deepEqual(await documentIds(main), ids);
  await record('RELOAD_USES_REAL_SESSION_AND_ONLY_NONSENSITIVE_SYNC_METADATA');
  assert.equal(report.page_errors.length, 0);
  report.browser_version = browser.version(); report.result = 'PASS'; report.finished_at = new Date().toISOString();
  await save(); console.log(JSON.stringify({ result: report.result, checks: report.checks.length, page_errors: report.page_errors.length }));
} catch (failure) {
  report.result = 'FAIL'; report.finished_at = new Date().toISOString(); report.failure_name = failure.name;
  report.failure_context = await main.evaluate(async () => ({ url: location.pathname,
    synchronization: localStorage.getItem('sjia.session.boundary.v1'), events: window.__qaSessionEvents,
    locks: await navigator.locks.query(), screens: [...document.querySelectorAll('[data-screen-id]')].map(host => ({
      screen: host.getAttribute('data-screen-id'), patient: host.shadowRoot.querySelector('.rich-record-patient strong')?.textContent,
      status: [...host.shadowRoot.querySelectorAll('[role="alert"],[role="status"]')].map(node => node.textContent),
    })), status: [...document.querySelectorAll('[role="alert"],[role="status"]')].map(node => node.textContent) }));
  await save(); throw failure;
} finally { await save(); await browser.close(); }
