import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, credentials: { type: 'string' },
  source: { type: 'string' }, dist: { type: 'string' }, origin: { type: 'string', default: 'http://127.0.0.1:5178' },
  production: { type: 'boolean', default: false }, port: { type: 'string', default: '9237' } } });
for (const key of ['report', 'credentials', 'source', 'dist']) assert.ok(values[key], key);
assert.equal(new URL(values.origin).hostname, '127.0.0.1');
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const source = JSON.parse(await readFile(resolve(values.source), 'utf8'));
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), checks: [], page_errors: [],
  responses: [], stage: 'START', test_sha256: createHash('sha256').update(await readFile(import.meta.filename)).digest('hex'),
  visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: Number(values.port), headless: true });
const { context, browser } = native;
const page = await context.newPage();
page.setDefaultTimeout(30000);
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('response', response => {
  const path = new URL(response.url()).pathname;
  if (path.startsWith('/api/')) report.responses.push({ path, status: response.status() });
});
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function check(name, details = {}) { report.checks.push({ name, result: 'PASS', ...details }); await save(); }
async function get(path) {
  const response = await context.request.get(values.origin + path);
  assert.equal(response.status(), 200, path);
  return response.json();
}
try {
  const index = await context.request.get(values.origin + '/login');
  assert.equal(index.status(), 200);
  assert.deepEqual(await index.body(), await readFile(resolve(values.dist, 'index.html')));
  await page.goto(values.origin + '/login');
  await page.getByRole('form', { name: '登录', exact: true }).waitFor();
  report.stage = 'CREDENTIAL_CARD'; await save();
  const card = page.getByRole('region', { name: '本地测试账号', exact: true });
  if (values.production) {
    assert.equal(await card.count(), 0);
    await check('PRODUCTION_HAS_NO_LOCAL_CREDENTIAL_CARD');
  } else {
    await card.waitFor();
    assert.equal(await card.getByTestId('test-username').textContent(), credentials.username);
    assert.equal(await card.getByTestId('test-password').textContent(), credentials.password);
    await card.getByRole('button', { name: '填入测试账号', exact: true }).click();
    assert.equal(await page.getByPlaceholder('账号', { exact: true }).inputValue(), credentials.username);
    assert.equal(await page.locator('input[type="password"]').inputValue(), credentials.password);
    await check('LOCAL_CREDENTIALS_DISPLAY_AND_FILL');
    await page.getByRole('button', { name: '没有账号，去注册', exact: true }).click();
    await page.getByRole('form', { name: '注册账号', exact: true }).waitFor();
    assert.equal(await card.count(), 0);
    assert.equal(await page.locator('input[type="password"]').inputValue(), '');
    await page.getByRole('button', { name: '已有账号，去登录', exact: true }).click();
    await card.getByRole('button', { name: '填入测试账号', exact: true }).click();
    await check('REGISTRATION_HIDES_TEST_CREDENTIALS');
    report.stage = 'LOGIN'; await save();
    const login = page.waitForResponse(item => new URL(item.url()).pathname === '/api/auth/login');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    assert.equal((await login).status(), 200);
    await page.waitForURL('**/home');
    const session = await get('/api/session');
    assert.equal(session.principal_id, source.actor_id);
    assert.equal(session.selected_patient_id, source.patient_id);
    const relations = await get('/api/relations');
    assert.equal(relations.relations.length, 5);
    const target = relations.relations.find(item => item.patient_id === source.patient_id);
    assert.ok(target?.actions.includes('read_raw'));
    await check('REAL_TEST_ACCOUNT_HAS_AUTHORIZED_CHILDREN', { relation_count: relations.relations.length });
    report.stage = 'RECORDS'; await save();
    await page.goto(values.origin + '/associations');
    const child = page.locator('.rich-child-identity').filter({ has: page.getByRole('heading', { name: target.display_id, exact: true }) });
    await child.waitFor();
    const select = child.getByRole('button', { name: '选择此患儿', exact: true });
    if (await select.count()) { await select.click(); await page.waitForURL('**/home'); }
    await page.goto(values.origin + '/records');
    const record = page.locator(`a[href="/records/${source.versions[0].record_id}"]`);
    await record.waitFor();
    await record.click();
    await page.waitForURL(`**/records/${source.versions[0].record_id}`);
    const saved = await get(`/api/records/${source.versions[0].record_id}`);
    assert.equal(saved.patient_id, source.patient_id);
    assert.equal(saved.fields.length, 83);
    assert.equal(saved.record_version_id, source.versions[0].version_id);
    assert.ok(saved.fields.every(field => field.source_anchor !== null));
    await page.getByRole('region', { name: '记录原始内容', exact: true }).waitFor();
    assert.equal(await page.locator('.rich-record-field').count(), saved.fields.length);
    await page.reload();
    await page.getByRole('region', { name: '记录原始内容', exact: true }).waitFor();
    assert.equal(await page.locator('.rich-record-field').count(), saved.fields.length);
    assert.deepEqual(await get(`/api/records/${saved.record_id}`), saved);
    await check('REAL_RECORD_FIELDS_AND_SOURCE_PERSIST_AFTER_RELOAD', { field_count: saved.fields.length });
    await page.goto(values.origin + '/account');
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await page.waitForURL('**/login');
    await card.waitFor();
    await check('LOGOUT_RETURNS_TO_LOCAL_LOGIN');
  }
  assert.deepEqual(report.page_errors, []);
  report.stage = 'COMPLETE'; report.result = 'PASS';
} finally {
  if (report.result === 'IN_PROGRESS') report.result = 'FAIL';
  report.final_url = page.url();
  report.alerts = await page.getByRole('alert').allTextContents();
  report.headings = await page.getByRole('heading').allTextContents();
  report.finished_at = new Date().toISOString(); report.browser_version = browser.version();
  await save(); await native.close();
  console.log(JSON.stringify({ result: report.result, stage: report.stage, checks: report.checks.length,
    page_errors: report.page_errors.length }));
}
