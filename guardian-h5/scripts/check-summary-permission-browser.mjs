import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';

const { values } = parseArgs({ options: Object.fromEntries(
  ['origin', 'credentials', 'scope', 'index', 'report'].map(name => [name, { type: 'string' }])) });
for (const name of ['origin', 'credentials', 'scope', 'index', 'report']) assert.ok(values[name], `缺少参数：${name}`);
const origin = new URL(values.origin).origin;
assert.equal(new URL(origin).hostname, '127.0.0.1');
const credentials = JSON.parse(await readFile(values.credentials, 'utf8'));
const scope = JSON.parse(await readFile(values.scope, 'utf8'));
assert.equal(scope.result, 'PASS');
assert.equal(scope.source_kind, 'USER_APPROVED_ORIGINAL');
const report = { status: 'IN_PROGRESS', started_at: new Date().toISOString(), origin,
  checks: [], responses: [], page_errors: [], visual_validation: 'NOT_RUN',
  device_validation: 'NOT_RUN', new_model_calls: 0, clinical_writes: 0 };
await writeFile(values.report, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(30000);
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('response', response => {
  const url = new URL(response.url());
  if (url.pathname.startsWith('/api/')) report.responses.push({ path: url.pathname,
    method: response.request().method(), status: response.status() });
});
async function save() { await writeFile(values.report, JSON.stringify(report, null, 2)); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + path, { method, data,
    headers: { Origin: origin, ...(csrf ? { 'X-CSRFToken': csrf } : {}) }, timeout: 60000 });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()}`);
  return response.json();
}
try {
  const index = await context.request.get(origin + '/summaries');
  assert.equal(index.status(), 200);
  const bytes = await index.body();
  assert.deepEqual(bytes, await readFile(values.index));
  report.index_sha256 = createHash('sha256').update(bytes).digest('hex');
  await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/home');
  assert.equal((await api('GET', '/api/session')).principal_id, scope.actor_id);
  const bootstrap = await api('GET', '/api/auth/bootstrap');
  await api('POST', '/api/patient-selection', { patient_id: scope.patient_id }, bootstrap.csrf_token);
  const relations = await api('GET', '/api/relations');
  const relation = relations.relations.find(item => item.patient_id === scope.patient_id);
  assert.ok(relation && relation.actions.includes('read_raw'));
  assert.ok(!relation.actions.includes('create_summary') && !relation.actions.includes('read_summary'));
  const summariesPath = `/api/patients/${scope.patient_id}/summaries`;
  const requestsPath = `/api/patients/${scope.patient_id}/requests`;
  await page.goto(origin + '/summaries', { waitUntil: 'networkidle', timeout: 60000 });
  const denied = page.getByRole('heading', { name: '当前账号没有访问权限', exact: true });
  await denied.waitFor({ state: 'visible' });
  report.permission_panels = await denied.count();
  assert.equal(report.permission_panels, 1);
  assert.equal(await page.getByRole('button', { name: '生成就医摘要', exact: true }).count(), 0);
  assert.equal(await page.getByRole('link', { name: '查看原始记录', exact: true }).count(), 1);
  assert.equal(report.responses.filter(item => item.path === summariesPath && item.status === 403).length, 1);
  assert.equal(report.responses.filter(item => item.path === requestsPath && item.status === 403).length, 1);
  report.checks.push('当前真实账号的两项摘要权限拒绝显示一个状态，生成表单保持关闭');
  const readAgain = page.waitForResponse(response => new URL(response.url()).pathname === summariesPath && response.status() === 403);
  const requestsAgain = page.waitForResponse(response => new URL(response.url()).pathname === requestsPath && response.status() === 403);
  await page.getByRole('button', { name: '重新检查访问权限', exact: true }).click();
  await Promise.all([readAgain, requestsAgain]);
  await page.locator('[aria-label="就医摘要权限说明"]').waitFor();
  assert.equal(await denied.count(), 1);
  report.checks.push('重新核验真实会话和关联后重读两项摘要接口，权限边界保持');
  const recordsRead = page.waitForResponse(response => new URL(response.url()).pathname === `/api/patients/${scope.patient_id}/records/search`
    && response.status() === 200);
  await page.getByRole('link', { name: '查看原始记录', exact: true }).click();
  await recordsRead;
  await page.getByRole('heading', { name: '记录', exact: true }).waitFor();
  report.checks.push('通过页面入口返回同一真实资料分组的获准记录');
  await page.goto(origin + '/summaries/structure', { waitUntil: 'networkidle', timeout: 60000 });
  assert.equal(await page.locator('[data-screen-id="UI2-14"]').count(), 1);
  assert.equal(await page.getByRole('button', { name: '重新选择范围', exact: true }).count(), 1);
  report.checks.push('摘要结构预览保留原有独立入口');
  const anonymous = await browser.newContext();
  const anonymousPage = await anonymous.newPage();
  await anonymousPage.goto(origin + '/summaries', { waitUntil: 'networkidle', timeout: 60000 });
  assert.equal(await anonymousPage.getByRole('heading', { name: '请先登录账号', exact: true }).count(), 1);
  assert.equal(await anonymousPage.locator('[aria-label="就医摘要权限说明"]').count(), 0);
  assert.equal(await anonymousPage.getByRole('button', { name: '生成就医摘要', exact: true }).isDisabled(), true);
  await anonymous.close();
  report.checks.push('匿名访问继续显示登录入口，生成操作保持禁用');
  assert.equal(report.page_errors.length, 0);
  assert.ok(report.responses.every(item => item.method === 'GET'
    || item.path === '/api/auth/login' || item.path.endsWith('/records/search')));
  report.status = 'PASS';
  report.browser_version = browser.version();
  process.stdout.write(JSON.stringify({ status: report.status, checks: report.checks.length,
    permission_panels: report.permission_panels, new_model_calls: 0, clinical_writes: 0 }) + '\n');
} finally {
  if (report.status !== 'PASS') report.status = 'FAIL';
  report.finished_at = new Date().toISOString();
  await save();
  await browser.close();
}
