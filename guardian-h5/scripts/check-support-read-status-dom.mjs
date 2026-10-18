import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';

const { values } = parseArgs({ options: {
  'base-url': { type: 'string' }, 'family-credentials': { type: 'string' },
  'operator-credentials': { type: 'string' }, report: { type: 'string' },
}, strict: true });
for (const name of ['base-url', 'family-credentials', 'operator-credentials', 'report']) {
  if (!values[name]) throw new Error(`需要提供 --${name}`);
}
const origin = new URL(values['base-url']);
assert.equal(origin.hostname, '127.0.0.1');
assert.equal(origin.protocol, 'http:');
assert.equal(origin.pathname, '/');
assert.equal(origin.username + origin.password + origin.search + origin.hash, '');
const base = origin.origin;
const credentials = JSON.parse(await readFile(values['family-credentials'], 'utf8'));
const operator = JSON.parse(await readFile(values['operator-credentials'], 'utf8'));
for (const account of [credentials, operator]) {
  assert.equal(typeof account.username, 'string');
  assert.equal(typeof account.password, 'string');
  assert(account.username.length > 0 && account.password.length > 0);
}
const sourcePaths = ['../src/features/support/SupportApp.tsx', '../src/features/support/PurposeCopyPage.tsx'];
async function sourceChecksums() {
  return Object.fromEntries(await Promise.all(sourcePaths.map(async path => [path,
    createHash('sha256').update(await readFile(new URL(path, import.meta.url))).digest('hex')])));
}
const originalChecksums = await sourceChecksums();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const report = { checks: [], page_errors: [], requests: [], responses: [], new_model_calls: 0,
  clinical_approval: false, visual_check: 'NOT_RUN', source_files: originalChecksums };
function watch(page) {
  page.setDefaultTimeout(90000);
  page.on('pageerror', error => report.page_errors.push(error.message));
  page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith('/api/') && !['GET', 'HEAD'].includes(request.method())) report.requests.push({ path, method: request.method() });
  });
}
async function supportResponse(page, status, code) {
  const response = await page.waitForResponse(item => new URL(item.url()).pathname === '/api/v2/support/session');
  assert.equal(response.status(), status);
  const body = await response.json();
  if (code) assert.equal(body.code, code);
  report.responses.push({ path: '/api/v2/support/session', status, code: body.code ?? null });
  return body;
}
try {
  const family = await browser.newContext(), page = await family.newPage();
  watch(page);
  await page.goto(base + '/login');
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL('**/home');
  for (const route of ['verification', 'assessment', 'publication', 'datasets', 'data-use', 'deliveries']) {
    const pending = supportResponse(page, 403, 'SUPPORT_AUDIENCE_REQUIRED');
    await page.goto(base + '/support/' + route);
    await pending;
    await page.getByRole('heading', { name: '请使用支持端登录入口', exact: true }).waitFor();
    const status = page.locator('.status-panel.permission').first();
    assert((await status.textContent()).includes('当前会话使用家庭端身份'));
    assert.equal(await status.getByRole('link', { name: '登录支持端', exact: true }).getAttribute('href'), '/support/login');
    assert.equal(await status.getByRole('link', { name: '返回家庭端', exact: true }).getAttribute('href'), '/account');
    assert.equal(await status.locator('details').getAttribute('open'), null);
    report.checks.push(`实际家庭会话访问${route}收到准确403，中文说明、登录和返回入口一致`);
  }
  const recheck = supportResponse(page, 403, 'SUPPORT_AUDIENCE_REQUIRED');
  await page.getByRole('button', { name: '重新核验访问权限', exact: true }).click();
  await recheck;
  assert.equal(new URL(page.url()).pathname, '/support/deliveries');
  report.checks.push('实际手动核验重新读取当前权限并保持当前页面');
  await page.getByRole('link', { name: '返回家庭端', exact: true }).click();
  await page.waitForURL('**/account');
  const familySession = await family.request.get(base + '/api/session');
  assert.equal(familySession.status(), 200);
  assert.equal((await familySession.json()).audience, 'family');
  report.checks.push('返回家庭入口实际可访问，原家庭会话保持');
  const denied = supportResponse(page, 403, 'SUPPORT_AUDIENCE_REQUIRED');
  await page.goto(base + '/support/data-use');
  await denied;
  const purposeRecheck = supportResponse(page, 403, 'SUPPORT_AUDIENCE_REQUIRED');
  await page.getByRole('button', { name: '重新核验访问权限', exact: true }).click();
  await purposeRecheck;
  assert.equal(new URL(page.url()).pathname, '/support/data-use');
  report.checks.push('用途页面的手动核验实际读取权限，登录入口独立可操作');
  await page.getByRole('link', { name: '登录支持端', exact: true }).click();
  await page.waitForURL('**/support/login');
  await page.getByLabel('账号', { exact: true }).fill(credentials.username);
  await page.getByLabel('密码', { exact: true }).fill(credentials.password);
  const familyLoginResponse = page.waitForResponse(item => new URL(item.url()).pathname === '/api/v2/support/session/login');
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  const familyLogin = await familyLoginResponse;
  assert.equal(familyLogin.status(), 200);
  const familySupport = await familyLogin.json();
  assert.equal(familySupport.audience, 'support');
  assert(familySupport.capabilities.some(item => item.capability_id === 'manage_data_use'));
  assert(!familySupport.capabilities.some(item => item.capability_id === 'read_assessment'));
  await page.waitForURL('**/support/data-use');
  const roleRead = supportResponse(page, 200);
  await page.goto(base + '/support/assessment');
  await roleRead;
  await page.getByRole('heading', { name: '当前账号没有所需访问权限', exact: true }).waitFor();
  assert.equal(await page.getByRole('link', { name: '更换支持账号', exact: true }).getAttribute('href'), '/support/login');
  report.responses.push({ path: '/api/v2/support/session/login', status: 200, code: null });
  report.checks.push('同一账号实际切换支持受众后登录成功，缺少评估岗位时明确说明访问受限');
  await page.getByRole('link', { name: '更换支持账号', exact: true }).click();
  await page.waitForURL('**/support/login');
  await page.getByLabel('账号', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('账号', { exact: true }).inputValue(), '');
  assert.equal(await page.getByLabel('密码', { exact: true }).inputValue(), '');
  report.checks.push('更换账号入口实际可达，表单没有保留上次密码');
  const anonymous = await browser.newContext(), anonymousPage = await anonymous.newPage();
  watch(anonymousPage);
  const anonymousRead = supportResponse(anonymousPage, 401, 'AUTH_REQUIRED');
  await anonymousPage.goto(base + '/support/datasets');
  await anonymousRead;
  await anonymousPage.getByRole('heading', { name: '请登录支持端', exact: true }).waitFor();
  await anonymousPage.getByRole('link', { name: '登录支持端', exact: true }).click();
  await anonymousPage.waitForURL('**/support/login');
  await anonymousPage.getByLabel('账号', { exact: true }).waitFor();
  report.checks.push('实际匿名401明确要求登录，入口到达支持端登录表单');
  const support = await browser.newContext(), supportPage = await support.newPage();
  watch(supportPage);
  await supportPage.goto(base + '/support/login');
  await supportPage.getByLabel('账号', { exact: true }).fill(operator.username);
  await supportPage.getByLabel('密码', { exact: true }).fill(operator.password);
  const operatorLoginResponse = supportPage.waitForResponse(item => new URL(item.url()).pathname === '/api/v2/support/session/login');
  await supportPage.getByRole('button', { name: '登录账号', exact: true }).click();
  const login = await operatorLoginResponse;
  assert.equal(login.status(), 200);
  const session = await login.json();
  assert.equal(session.audience, 'support');
  assert(session.capabilities.some(item => item.capability_id === 'manage_config'));
  await supportPage.waitForURL('**/support/data-use');
  const activeSession = await support.request.get(base + '/api/v2/support/session');
  assert.equal(activeSession.status(), 200);
  assert.equal((await activeSession.json()).principal_id, session.principal_id);
  report.support_capabilities = session.capabilities.map(item => item.capability_id);
  report.responses.push({ path: '/api/v2/support/session/login', status: 200, code: null });
  report.checks.push('现有获准支持账号通过实际登录及会话重读');
  const publicationRead = supportResponse(supportPage, 200);
  await supportPage.goto(base + '/support/publication');
  await publicationRead;
  await supportPage.getByRole('heading', { name: '内容与启用', exact: true, level: 1 }).waitFor();
  assert.equal(await supportPage.getByRole('heading', { name: '请使用支持端登录入口', exact: true }).count(), 0);
  assert.equal(await supportPage.getByRole('heading', { name: '请登录支持端', exact: true }).count(), 0);
  report.checks.push('实际获准支持页面保持正常入口，没有家庭受众或匿名提示');
  assert.equal(report.page_errors.length, 0);
  assert(report.requests.every(request => request.path === '/api/auth/login'
    || request.path === '/api/v2/support/session/login' || request.path.endsWith('/search')));
  report.checks.push('没有临床写入、意图准备、模型调用或页面运行错误');
  assert.deepEqual(await sourceChecksums(), originalChecksums);
  report.checks.push('全部实际检查期间共享组件及调用方的准确源码保持');
  report.result = 'PASS';
} finally {
  report.measured_at = new Date().toISOString();
  await writeFile(values.report, JSON.stringify(report, null, 2), { mode: 0o600 });
  await browser.close();
}
console.log(JSON.stringify({ result: report.result, checks: report.checks.length,
  page_errors: report.page_errors, responses: report.responses.length }));
