import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, statfsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: {
  report: { type: 'string' }, dist: { type: 'string' }, credentials: { type: 'string' },
  source: { type: 'string' }, port: { type: 'string', default: '9231' },
} });
for (const key of ['report', 'dist', 'credentials', 'source']) assert.ok(values[key], key);
const root = resolve(import.meta.dirname, '../..');
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const space = statfsSync(root);
assert.ok(space.bavail * space.bsize >= 1024 ** 3);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const source = JSON.parse(await readFile(resolve(values.source), 'utf8'));
assert.equal(source.result, 'PASS');
const origin = 'http://127.0.0.1:5178';
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), origin,
  available_bytes: space.bavail * space.bsize, checks: [], page_errors: [],
  new_model_calls: 0, new_clinical_writes: 0, visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: Number(values.port), headless: true });
const { browser, context } = native;
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on('pageerror', failure => report.page_errors.push(failure.message));
const reads = [];
page.on('request', request => {
  if (/\/api\/(?:v2\/)?patients\//.test(request.url())) reads.push(new URL(request.url()).pathname);
});
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function record(name, details = {}) { report.checks.push({ name, result: 'PASS', ...details }); await save(); }
async function login() {
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  const response = page.waitForResponse(item => new URL(item.url()).pathname === '/api/auth/login');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  assert.equal((await response).status(), 200);
  await page.waitForURL('**/home');
  await page.getByRole('button', { name: '查看服务状态与资料依据', exact: true }).waitFor();
  const session = await context.request.get(origin + '/api/session');
  assert.equal(session.status(), 200);
  const body = await session.json();
  assert.equal(body.principal_id, source.actor_id);
  assert.equal(body.audience, 'family');
  return body;
}
try {
  const response = await context.request.get(origin + '/home');
  assert.equal(response.status(), 200);
  const bytes = await response.body();
  assert.deepEqual(bytes, await readFile(resolve(values.dist, 'index.html')));
  report.index_sha256 = createHash('sha256').update(bytes).digest('hex');
  for (const path of ['/home', '/', '/records', '/alerts/current', '/archives/confirm?draft=private']) {
    await page.goto(origin + path);
    await page.waitForURL('**/login');
    await page.getByRole('form', { name: '登录', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '返回上级页面', exact: true }).count(), 0);
    assert.equal(await page.locator('.bottom-nav').count(), 0);
    assert.equal(await page.getByRole('heading', { name: '请先登录账号', exact: true }).count(), 0);
  }
  assert.deepEqual(reads, []);
  await record('ANONYMOUS_ROUTES_REQUIRE_STANDALONE_LOGIN_WITHOUT_PATIENT_READS');
  await page.getByRole('button', { name: '了解资料如何使用', exact: true }).click();
  await page.waitForURL('**/account/data-use');
  await page.getByRole('heading', { name: '资料使用说明', exact: true }).waitFor();
  await record('PUBLIC_DATA_USE_REMAINS_ACCESSIBLE');
  await page.goto(origin + '/login');
  const session = await login();
  await record('REAL_LOGIN_OPENS_AUTHORIZED_HOME', { principal_id: session.principal_id });
  await page.reload();
  await page.getByRole('button', { name: '查看服务状态与资料依据', exact: true }).waitFor();
  const restored = await context.request.get(origin + '/api/session');
  assert.equal(restored.status(), 200);
  assert.equal((await restored.json()).selected_patient_id, session.selected_patient_id);
  await record('RELOAD_RESTORES_ACTUAL_SESSION');

  const browserSession = await browser.newBrowserCDPSession();
  const targetSession = await context.newCDPSession(page);
  const info = await targetSession.send('Target.getTargetInfo');
  const created = context.waitForEvent('page');
  await browserSession.send('Target.createTarget', { url: origin + '/account',
    browserContextId: info.targetInfo.browserContextId, newWindow: true });
  const peer = await created;
  peer.setDefaultTimeout(15000);
  peer.on('pageerror', failure => report.page_errors.push(failure.message));
  await peer.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  const logout = peer.waitForResponse(item => new URL(item.url()).pathname === '/api/session/logout');
  await peer.getByRole('button', { name: '退出登录', exact: true }).click();
  assert.equal((await logout).status(), 200);
  await peer.waitForURL('**/login'); await page.waitForURL('**/login');
  await page.getByRole('form', { name: '登录', exact: true }).waitFor();
  assert.equal(await page.locator('.patient').count(), 0);
  await record('LOGOUT_REDIRECTS_BOTH_WINDOWS_AND_CLEARS_PATIENT');
  await peer.close();

  await login();
  const cookies = await context.cookies(origin);
  assert.ok(cookies.some(cookie => cookie.name === 'sjia_real_validation_session'));
  await context.clearCookies({ name: 'sjia_real_validation_session' });
  const denied = page.waitForResponse(item => /\/api\/patients\/[^/]+\/home$/.test(new URL(item.url()).pathname));
  await page.getByRole('button', { name: '查看服务状态与资料依据', exact: true }).click();
  assert.equal((await denied).status(), 401);
  await page.waitForURL('**/login');
  await page.getByRole('form', { name: '登录', exact: true }).waitFor();
  assert.equal(await page.locator('.patient').count(), 0);
  await record('ACTUAL_RESOURCE_401_CLEARS_SESSION_AND_OPENS_LOGIN');
  assert.deepEqual(report.page_errors, []);
  report.result = 'PASS'; report.browser_version = browser.version();
} catch (failure) {
  report.result = 'FAIL'; report.failure = { name: failure.name, message: failure.message, pathname: new URL(page.url()).pathname };
  throw failure;
} finally {
  report.finished_at = new Date().toISOString(); await save(); await native.close();
  console.log(JSON.stringify({ result: report.result, checks: report.checks.length, page_errors: report.page_errors.length }));
}
