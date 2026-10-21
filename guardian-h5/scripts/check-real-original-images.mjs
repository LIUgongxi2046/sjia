import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, statfsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, source: { type: 'string' },
  credentials: { type: 'string' }, build: { type: 'string' }, dist: { type: 'string' }, background: { type: 'boolean', default: false } } });
for (const key of ['report', 'source', 'credentials', 'build', 'dist']) assert.ok(values[key], key);
const root = resolve(import.meta.dirname, '../..');
const output = resolve(values.report);
assert.equal(output.startsWith(resolve(root, '.qa-work/QA60') + '/'), true);
assert.equal(existsSync(output), false);
const digest = data => createHash('sha256').update(data).digest('hex');
const sourceBytes = await readFile(resolve(values.source)), source = JSON.parse(sourceBytes);
const buildBytes = await readFile(resolve(values.build)), build = JSON.parse(buildBytes);
const scriptHash = digest(await readFile(import.meta.filename));
assert.equal(source.result, 'PASS'); assert.equal(build.result, 'PASS');
for (const [name, checksum] of Object.entries(build.source_sha256)) assert.equal(digest(await readFile(resolve(root, name))), checksum);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const documents = source.documents.filter(item => item.pages.every(page => page.media_type.startsWith('image/')));
assert.equal(documents.length, 1); assert.equal(source.records.length, 1);
const targets = [
  ...documents.map(item => ({ kind: 'document', patient: item.patient_id,
    route: `/archives/source?document=${item.document_id}`, files: item.pages.map(page => source.files[page.file_id]) })),
  ...source.records.map(item => ({ kind: 'record', patient: item.patient_id,
    route: `/archives/source?version=${item.version_id}`, files: item.originals.map(file => source.files[file.file_id]) })),
];
assert.equal(targets.length, 2);
for (const target of targets) {
  assert.equal(target.files.length, 1);
  assert.ok(target.files.every(file => file.approved_source_ids.length && file.media_type.startsWith('image/') && file.size_bytes < 2 * 1024 ** 2));
}
function spaceCheck(reserve = 0) {
  const space = statfsSync(root);
  const available = space.bavail * space.bsize;
  assert.ok(available >= 2 * 1024 ** 3 + reserve, `available_bytes=${available}`);
  return available;
}
spaceCheck(16 * 1024 ** 2);
const origin = 'http://127.0.0.1:5178';
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), origin, checks: [], page_errors: [], transfers: [],
  requests: [], failed_requests: [],
  source_sha256: digest(sourceBytes), build_sha256: digest(buildBytes), script_sha256: scriptHash,
  noDefaults: true, background_validation: values.background ? 'IN_PROGRESS' : 'NOT_RUN',
  new_model_calls: 0, new_clinical_writes: 0, visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const owned = await openRealBrowser({ port: 9460, headless: true });
const { browser, context } = owned;
const main = await context.newPage(); main.setDefaultTimeout(30000);
main.on('pageerror', error => report.page_errors.push(error.message));
main.on('request', request => {
  const path = new URL(request.url()).pathname;
  if (path === '/api/session' || path === '/api/relations' || path.endsWith('/content'))
    report.requests.push({ path, method: request.method(), at: performance.now() });
});
main.on('requestfailed', request => {
  if (new URL(request.url()).pathname.endsWith('/content')) report.failed_requests.push({ path: new URL(request.url()).pathname, failure: request.failure() });
});
const browserSession = await browser.newBrowserCDPSession();
let csrf, stage = 'INITIALIZE';
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function checked(id, name, details = {}) {
  const available = spaceCheck(); report.checks.push({ id, name, result: 'PASS', available_bytes: available, ...details }); await save();
}
async function native(page) {
  const client = await context.newCDPSession(page);
  const info = (await client.send('Target.getTargetInfo')).targetInfo;
  const window = await browserSession.send('Browser.getWindowForTarget', { targetId: info.targetId });
  return { client, info, window };
}
const mainNative = await native(main);
async function api(method, path, status, data) {
  const response = await context.request.fetch(origin + path, { method, data,
    headers: { Origin: origin, ...(csrf ? { 'X-CSRFToken': csrf } : {}) }, timeout: 60000 });
  assert.equal(response.status(), status, `${method} ${path}`);
  return response.json();
}
async function activate(targetId) { await browserSession.send('Target.activateTarget', { targetId }); }
async function login() {
  await activate(mainNative.info.targetId); await main.goto(origin + '/login');
  await main.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await main.locator('input[type="password"]').fill(credentials.password);
  await main.getByRole('button', { name: '登录', exact: true }).click();
  await main.waitForURL('**/home');
  const session = await api('GET', '/api/session', 200);
  assert.equal(session.principal_id, source.actor_id); assert.equal(session.audience, 'family'); csrf = session.csrf_token;
}
async function enter(target) {
  await activate(mainNative.info.targetId);
  const current = await api('GET', '/api/session', 200); csrf = current.csrf_token;
  await api('POST', '/api/patient-selection', 200, { patient_id: target.patient });
  await main.goto(origin + target.route);
  await main.getByRole('heading', { name: '已保存原始文书', exact: true }).waitFor();
  await main.getByRole('button', { name: '查看原件', exact: true }).waitFor();
  assert.equal(await main.getByRole('button', { name: '查看原件', exact: true }).isEnabled(), true);
  await main.evaluate(() => {
    window.__qa60Visibility = [];
    document.addEventListener('visibilitychange', () => window.__qa60Visibility.push({ state: document.visibilityState, at: performance.now() }));
  });
}
async function blobUrls() {
  return main.evaluate(() => [...new Set([document, ...[...document.querySelectorAll('[data-screen-id]')].map(host => host.shadowRoot)]
    .flatMap(root => [...root.querySelectorAll('img[src^="blob:"],iframe[src^="blob:"]')]).map(node => node.src))].sort());
}
async function waitNoBlobs() {
  await main.waitForFunction(() => [document, ...[...document.querySelectorAll('[data-screen-id]')].map(host => host.shadowRoot)]
    .every(root => !root.querySelector('img[src^="blob:"],iframe[src^="blob:"]')), null, { polling: 100, timeout: 15000 });
}
async function revoked(urls) {
  assert.ok(urls.length);
  const results = await main.evaluate(urls => Promise.all(urls.map(url => fetch(url).then(
    response => ({ resolved: true, status: response.status }), error => ({ resolved: false, name: error.name })))), urls);
  assert.ok(results.every(item => !item.resolved && item.name === 'TypeError'));
  return results.length;
}
async function image(target) {
  const file = target.files[0];
  const response = main.waitForResponse(item => new URL(item.url()).pathname === `/api/files/${file.file_id}/content` && item.request().method() === 'GET');
  await main.getByRole('button', { name: '查看原件', exact: true }).click();
  const returned = await response;
  assert.equal(returned.status(), 200);
  const headers = await returned.allHeaders();
  assert.equal(headers['content-type'], file.media_type);
  assert.ok(headers['cache-control'].includes('no-store') && headers['cache-control'].includes('private'));
  assert.equal(await returned.finished(), null);
  const downloaded = await context.request.get(returned.url());
  assert.equal(downloaded.status(), 200);
  assert.equal(downloaded.headers()['content-type'], file.media_type);
  assert.ok(downloaded.headers()['cache-control'].includes('no-store') && downloaded.headers()['cache-control'].includes('private'));
  const bytes = await downloaded.body();
  const transfer = { kind: target.kind, status: returned.status(), content_type: headers['content-type'],
    content_length: headers['content-length'], transfer_encoding: headers['transfer-encoding'],
    authorized_http_bytes: bytes.length, authorized_http_checksum: digest(bytes) };
  report.transfers.push(transfer); await save();
  if (target.kind === 'document') await main.getByRole('dialog', { name: '原件与页序', exact: true })
    .getByRole('button', { name: /^查看原始资料/ }).click();
  const viewer = main.getByRole('dialog', { name: '原图查看器', exact: true });
  await viewer.waitFor();
  const images = viewer.locator('img[src^="blob:"]');
  await main.waitForFunction(() => !!document.querySelector('dialog[open] img[src^="blob:"]'), null, { timeout: 10000, polling: 100 });
  const decoded = await images.evaluateAll(async nodes => {
    await Promise.all(nodes.map(node => node.decode()));
    return nodes.map(node => ({ complete: node.complete, width: node.naturalWidth, height: node.naturalHeight }));
  });
  assert.ok(decoded.length && decoded.every(item => item.complete && item.width > 0 && item.height > 0));
  const urls = await blobUrls(); assert.equal(urls.length, 1);
  const actual = await main.evaluate(async urls => Promise.all(urls.map(async url => {
    const response = await fetch(url); if (!response.ok) throw new Error('当前原图Blob读取失败。');
    const data = await response.arrayBuffer(); const checksum = await crypto.subtle.digest('SHA-256', data);
    return { bytes: data.byteLength, checksum: [...new Uint8Array(checksum)].map(value => value.toString(16).padStart(2, '0')).join('') };
  })), urls);
  assert.deepEqual(actual, [{ bytes: file.size_bytes, checksum: file.checksum }]);
  transfer.adopted_blob = actual; await save();
  assert.equal(bytes.length, file.size_bytes); assert.equal(digest(bytes), file.checksum);
  await viewer.getByRole('button', { name: '旋转', exact: true }).click();
  assert.equal(await images.evaluateAll(nodes => nodes.some(node => node.style.rotate === '90deg')), true);
  return { urls, bytes: bytes.length, checksum: file.checksum, decoded };
}
async function closeImage(target) {
  await main.getByRole('dialog', { name: '原图查看器', exact: true }).getByRole('button', { name: '关闭原图', exact: true }).click();
  if (target.kind === 'document') await main.getByRole('dialog', { name: '原件与页序', exact: true })
    .getByRole('button', { name: '关闭原件与页序', exact: true }).click();
  await waitNoBlobs();
  const focused = await main.getByRole('button', { name: '查看原件', exact: true })
    .evaluate(node => node.getRootNode().activeElement === node);
  assert.equal(focused, true);
}
async function peerWindow() {
  const event = context.waitForEvent('page');
  await browserSession.send('Target.createTarget', { url: origin + '/account',
    newWindow: true });
  const peer = await event; peer.setDefaultTimeout(30000);
  const peerNative = await native(peer);
  assert.notEqual(peerNative.window.windowId, mainNative.window.windowId);
  await browserSession.send('Browser.setWindowBounds', { windowId: mainNative.window.windowId, bounds: { left: 0, top: 0, width: 430, height: 900 } });
  await browserSession.send('Browser.setWindowBounds', { windowId: peerNative.window.windowId, bounds: { left: 500, top: 0, width: 430, height: 900 } });
  await peer.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  assert.equal(await main.evaluate(() => document.visibilityState), 'visible');
  assert.equal(await peer.evaluate(() => document.visibilityState), 'visible');
  return peer;
}
async function logout(peer, file) {
  const response = peer.waitForResponse(item => new URL(item.url()).pathname === '/api/session/logout' && item.request().method() === 'POST');
  await peer.getByRole('button', { name: '退出登录', exact: true }).click(); assert.equal((await response).status(), 200);
  await peer.waitForURL('**/login'); await waitNoBlobs();
  assert.equal((await api('GET', '/api/session', 401)).code, 'AUTH_REQUIRED');
  assert.equal((await api('GET', `/api/files/${file.file_id}/content`, 401)).code, 'AUTH_REQUIRED');
  await main.waitForURL('**/login');
  await main.getByRole('form', { name: '登录', exact: true }).waitFor();
}
try {
  const index = await context.request.get(origin + '/home'); assert.equal(index.status(), 200);
  const indexBytes = await index.body(); assert.deepEqual(indexBytes, await readFile(resolve(values.dist, 'index.html')));
  assert.equal(digest(indexBytes), build.index_sha256); report.index_sha256 = build.index_sha256;
  for (const target of targets) {
    const id = offset => 'QA60-' + String((target.kind === 'document' ? 1 : 6) + offset).padStart(2, '0');
    stage = target.kind + ':login'; await login(); await enter(target);
    stage = target.kind + ':bytes'; const first = await image(target);
    await checked(id(0), 'EXACT_ORIGINAL_BYTES_AND_IMAGE_OPERATIONS', { kind: target.kind, bytes: first.bytes, checksum: first.checksum, decoded: first.decoded });
    stage = target.kind + ':close'; await closeImage(target);
    await checked(id(1), 'CLOSE_RELEASES_BLOB_AND_RESTORES_FOCUS', { revoked: await revoked(first.urls) });

    if (values.background) {
    stage = target.kind + ':background'; await enter(target); const second = await image(target);
    const created = context.waitForEvent('page');
    const newTarget = await browserSession.send('Target.createTarget', { url: 'about:blank',
      newWindow: false });
    const background = await created; const backgroundNative = await native(background);
    assert.equal(backgroundNative.window.windowId, mainNative.window.windowId);
    await activate(newTarget.targetId);
    await main.waitForFunction(() => document.visibilityState === 'hidden', null, { polling: 100, timeout: 10000 });
    await waitNoBlobs(); const released = await revoked(second.urls);
    const sessionResponse = main.waitForResponse(item => new URL(item.url()).pathname === '/api/session' && item.request().method() === 'GET');
    await activate(mainNative.info.targetId);
    await main.waitForFunction(() => document.visibilityState === 'visible', null, { polling: 100, timeout: 10000 });
    assert.equal((await sessionResponse).status(), 200); await background.close();
    await checked(id(2), 'ACTUAL_BACKGROUND_RELEASE_AND_FOREGROUND_SESSION_CHECK', { revoked: released, hidden: true });
    }

    stage = target.kind + ':peer_logout'; await enter(target); const third = await image(target);
    const peer = await peerWindow(); await logout(peer, target.files[0]);
    await checked(id(3), 'VISIBLE_PEER_LOGOUT_CLEARS_ORIGINAL_AND_SERVER_ACCESS', { revoked: await revoked(third.urls) });
    await peer.close();

    stage = target.kind + ':inflight_logout'; await login(); await enter(target);
    const pendingPeer = await peerWindow();
    await mainNative.client.send('Network.enable');
    await mainNative.client.send('Network.emulateNetworkConditions', { offline: false, latency: 3000, downloadThroughput: -1, uploadThroughput: -1 });
    const pending = main.waitForRequest(item => new URL(item.url()).pathname === `/api/files/${target.files[0].file_id}/content` && item.method() === 'GET');
    await main.getByRole('button', { name: '查看原件', exact: true }).click();
    const request = await pending;
    const aborted = main.waitForEvent('requestfailed', { predicate: item => item === request, timeout: 15000 });
    await logout(pendingPeer, target.files[0]); await aborted;
    assert.equal(request.failure().errorText, 'net::ERR_ABORTED');
    await mainNative.client.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await checked(id(4), 'ACTUAL_INFLIGHT_READ_ABORTS_AFTER_PEER_LOGOUT', { request_failure: request.failure().errorText });
    await pendingPeer.close();
  }
  assert.equal(report.checks.length, values.background ? 10 : 8); assert.deepEqual(report.page_errors, []);
  assert.equal(digest(await readFile(import.meta.filename)), scriptHash);
  assert.deepEqual(await readFile(resolve(values.source)), sourceBytes); assert.deepEqual(await readFile(resolve(values.build)), buildBytes);
  if (values.background) report.background_validation = 'PASS';
  report.result = 'PASS'; report.browser_version = browser.version(); report.finished_at = new Date().toISOString();
  await save(); console.log(JSON.stringify({ result: report.result, checks: report.checks.length, page_errors: report.page_errors.length }));
} catch (failure) {
  report.result = 'FAIL'; report.failed_stage = stage; report.failure_name = failure.name; report.finished_at = new Date().toISOString();
  report.failure_context = await main.evaluate(() => ({ url: location.pathname, visibility: document.visibilityState, visibility_events: window.__qa60Visibility,
    screens: [...document.querySelectorAll('[data-screen-id]')].map(host => ({ id: host.getAttribute('data-screen-id'),
      alerts: [...host.shadowRoot.querySelectorAll('[role="alert"]')].map(node => node.textContent),
      dialogs: [...host.shadowRoot.querySelectorAll('[role="dialog"]')].map(node => ({ label: node.getAttribute('aria-label'), text: node.textContent })),
      statuses: [...host.shadowRoot.querySelectorAll('[role="status"]')].map(node => node.textContent),
      source_status: [...host.shadowRoot.querySelectorAll('.source-view [role="status"]')].map(node => node.textContent),
      source_actions: [...host.shadowRoot.querySelectorAll('.source-view button')].map(node => ({ text: node.textContent, disabled: node.disabled })) })),
    native_dialogs: [...document.querySelectorAll('dialog')].map(node => ({ open: node.open, label: node.getAttribute('aria-label') })) }));
  await save(); throw failure;
} finally { await save(); await owned.close(); }
