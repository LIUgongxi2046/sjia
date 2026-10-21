import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, statfsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { setTimeout } from 'node:timers/promises';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: Object.fromEntries(['report', 'source', 'credentials', 'build', 'dist', 'structure'].map(key => [key, { type: 'string' }])) });
for (const key of ['report', 'source', 'credentials', 'build', 'dist', 'structure']) assert.ok(values[key], key);
const root = resolve(import.meta.dirname, '../..'), output = resolve(values.report);
assert.equal(output.startsWith(resolve(root, '.qa-work/QA60') + '/'), true);
assert.equal(existsSync(output), false);
const digest = data => createHash('sha256').update(data).digest('hex');
const sourceBytes = await readFile(resolve(values.source)), source = JSON.parse(sourceBytes);
const buildBytes = await readFile(resolve(values.build)), build = JSON.parse(buildBytes);
const structureBytes = await readFile(resolve(values.structure)), structure = JSON.parse(structureBytes);
const scriptHash = digest(await readFile(import.meta.filename));
assert.equal(source.result, 'PASS'); assert.equal(build.result, 'PASS'); assert.equal(structure.result, 'PASS');
assert.equal(structure.source_sha256, digest(sourceBytes));
for (const [name, checksum] of Object.entries(build.source_sha256)) assert.equal(digest(await readFile(resolve(root, name))), checksum);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const targets = source.documents.filter(item => item.pages.length === 1 && item.pages[0].media_type === 'application/pdf');
assert.equal(targets.length, 1);
const target = targets[0], file = source.files[target.pages[0].file_id];
assert.equal(file.file_id, structure.file_id); assert.equal(file.size_bytes, structure.bytes); assert.equal(file.checksum, structure.checksum);
function spaceCheck(reserve = 0) {
  const space = statfsSync(root), available = space.bavail * space.bsize;
  assert.ok(available >= 2 * 1024 ** 3 + reserve, `available_bytes=${available}`);
  return available;
}
spaceCheck(16 * 1024 ** 2);
const origin = 'http://127.0.0.1:5178';
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), source_sha256: digest(sourceBytes),
  build_sha256: digest(buildBytes), structure_sha256: digest(structureBytes), script_sha256: scriptHash,
  page_errors: [], console_errors: [], network_events: [], checks: [], new_model_calls: 0, new_clinical_writes: 0,
  visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const owned = await openRealBrowser({ port: 9460, headless: true });
const { browser, context } = owned;
const page = await context.newPage(); page.setDefaultTimeout(30000);
const network = await context.newCDPSession(page), fileRequests = new Set();
network.on('Network.requestWillBeSent', event => {
  if (new URL(event.request.url).pathname.endsWith('/content')) fileRequests.add(event.requestId);
});
for (const name of ['Network.loadingFailed', 'Network.loadingFinished']) network.on(name, event => {
  if (fileRequests.has(event.requestId)) report.network_events.push({ name, ...event });
});
await network.send('Network.enable');
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') report.console_errors.push(message.text()); });
let stage = 'LOGIN', csrf;
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function api(method, path, data) {
  const response = await context.request.fetch(origin + path, { method, data,
    headers: { Origin: origin, ...(csrf ? { 'X-CSRFToken': csrf } : {}) }, timeout: 60000 });
  assert.equal(response.status(), 200, `${method} ${path}`); return response.json();
}
async function frames() {
  return Promise.all(page.frames().map(async frame => ({ scheme: frame.url() ? new URL(frame.url()).protocol : 'empty',
    elements: await frame.locator('embed,pdf-viewer,viewer-toolbar,viewer-page-selector').evaluateAll(nodes => nodes.map(node => ({
      tag: node.tagName, type: node.getAttribute('type'), children: node.childElementCount, shadow: !!node.shadowRoot }))) })));
}
try {
  const index = await context.request.get(origin + '/home'); assert.equal(index.status(), 200);
  assert.deepEqual(await index.body(), await readFile(resolve(values.dist, 'index.html')));
  await page.goto(origin + '/login');
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录', exact: true }).click(); await page.waitForURL('**/home');
  const session = await api('GET', '/api/session');
  assert.equal(session.principal_id, source.actor_id); assert.equal(session.audience, 'family'); csrf = session.csrf_token;
  await api('POST', '/api/patient-selection', { patient_id: target.patient_id });
  await page.goto(origin + `/archives/source?document=${target.document_id}`);
  await page.getByRole('heading', { name: '已保存原始文书', exact: true }).waitFor();
  stage = 'AUTHORIZED_HTTP'; report.stage = stage; await save();
  const downloaded = await context.request.get(origin + `/api/files/${file.file_id}/content`, { timeout: 60000 });
  assert.equal(downloaded.status(), 200);
  const bytes = await downloaded.body(); assert.equal(bytes.length, file.size_bytes); assert.equal(digest(bytes), file.checksum);
  report.checks.push({ name: 'EXACT_AUTHORIZED_HTTP', result: 'PASS', bytes: bytes.length, checksum: digest(bytes), available_bytes: spaceCheck() });
  await downloaded.dispose();
  stage = 'BROWSER_READ'; report.stage = stage; await save();
  const returned = page.waitForResponse(item => new URL(item.url()).pathname === `/api/files/${file.file_id}/content` && item.request().method() === 'GET');
  await page.getByRole('button', { name: '查看原件', exact: true }).click();
  const response = await returned; assert.equal(response.status(), 200);
  const headers = await response.allHeaders();
  assert.equal(headers['content-type'], 'application/pdf'); assert.equal(Number(headers['content-length']), file.size_bytes);
  assert.ok(headers['cache-control'].includes('no-store') && headers['cache-control'].includes('private'));
  const dialog = page.getByRole('dialog', { name: '原件与页序', exact: true });
  const iframe = dialog.locator('iframe'); await iframe.waitFor();
  const actual = await iframe.evaluate(async node => {
    const response = await fetch(node.src, { signal: AbortSignal.timeout(30000) }); if (!response.ok) throw new Error('当前PDF Blob读取失败。');
    const bytes = await response.arrayBuffer(), checksum = await crypto.subtle.digest('SHA-256', bytes);
    return { bytes: bytes.byteLength, checksum: [...new Uint8Array(checksum)].map(value => value.toString(16).padStart(2, '0')).join('') };
  });
  assert.deepEqual(actual, { bytes: file.size_bytes, checksum: file.checksum });
  report.checks.push({ name: 'EXACT_PDF_HTTP_AND_BLOB', result: 'PASS', ...actual, pages: structure.pages, available_bytes: spaceCheck() }); await save();
  stage = 'PDF_VIEWER_READY';
  const deadline = performance.now() + 15000;
  let viewers = [];
  while (performance.now() < deadline) {
    viewers = [];
    for (const frame of page.frames()) if (await frame.locator('pdf-viewer').count()) viewers.push(frame);
    if (viewers.length) break;
    await setTimeout(200);
  }
  report.frames = await frames(); await save();
  assert.equal(viewers.length, 1, 'PDF viewer');
  const pageCount = viewers[0].locator('#pageselector #pagelength');
  await pageCount.waitFor();
  assert.equal(Number((await pageCount.textContent()).trim().replace('/', '')), structure.pages);
  report.checks.push({ id: 'QA60-11', name: 'PDF_VIEWER_PAGE_COUNT', result: 'PASS', pages: structure.pages, available_bytes: spaceCheck() });
  const url = await iframe.getAttribute('src');
  await dialog.getByRole('button', { name: '关闭原件与页序', exact: true }).click();
  assert.equal(await page.locator('iframe[src^="blob:"]').count(), 0);
  assert.equal(await page.evaluate(url => fetch(url).then(() => false, error => error.name === 'TypeError'), url), true);
  await api('POST', '/api/session/logout', {});
  assert.deepEqual(report.page_errors, []);
  assert.equal(digest(await readFile(import.meta.filename)), scriptHash);
  assert.deepEqual(await readFile(resolve(values.source)), sourceBytes); assert.deepEqual(await readFile(resolve(values.build)), buildBytes);
  report.result = 'PASS'; report.browser_version = browser.version(); report.finished_at = new Date().toISOString();
  await save(); console.log(JSON.stringify({ result: report.result, pages: structure.pages, checks: report.checks.length }));
} catch (failure) {
  report.result = 'FAIL'; report.failed_stage = stage; report.failure_name = failure.name; report.failure_message = failure.message;
  if (!page.isClosed()) {
    report.frames = await frames();
    report.alerts = await page.locator('[role="alert"]').allTextContents();
  }
  report.finished_at = new Date().toISOString(); await save(); throw failure;
} finally { await save(); await owned.close(); }
