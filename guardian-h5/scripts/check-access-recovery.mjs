import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

for (const name of ['SJIA_BASE_URL', 'SJIA_TEST_CREDENTIALS', 'SJIA_TEST_UNASSOCIATED_CREDENTIALS', 'SJIA_TEST_OUTPUT']) {
  if (!process.env[name]) throw new Error(`缺少测试配置 ${name}`);
}
const base = new URL(process.env.SJIA_BASE_URL).origin;
const credentials = JSON.parse(await readFile(process.env.SJIA_TEST_CREDENTIALS, 'utf8'));
const unassociated = JSON.parse(await readFile(process.env.SJIA_TEST_UNASSOCIATED_CREDENTIALS, 'utf8'));
const report = { started_at: new Date().toISOString(), checks: [], page_errors: [] };
const anonymousPaths = ['/home', '/register/observations', '/register/reports', '/register/reports/confirm',
  '/register/therapies', '/records', '/records/period', '/archives', '/archives/batches', '/archives/changes',
  '/archives/confirm', '/archives/source', '/archives/regroup', '/archives/grouping', '/archives/duplicates',
  '/summaries', '/drafts', '/alerts', '/alert-presets', '/account/reminders', '/associations', '/associations/new'];

async function login(page, account) {
  await page.getByPlaceholder('账号', { exact: true }).fill(account.username);
  await page.locator('input[type="password"]').fill(account.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL('**/home');
}

test('访问状态与实际会话恢复', { timeout: 240000 }, async t => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  t.after(async () => {
    await writeFile(process.env.SJIA_TEST_OUTPUT, JSON.stringify(report, null, 2));
    await browser.close();
  });
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('pageerror', error => report.page_errors.push(error.message));
  await page.goto(base + '/home');
  await page.getByRole('heading', { name: '请先登录账号', exact: true }).waitFor();
  const session = page.waitForResponse(response => response.url() === base + '/api/session');
  await page.getByRole('button', { name: '检查登录状态', exact: true }).click();
  assert.equal((await session).status(), 401);
  await page.getByRole('heading', { name: '请先登录账号', exact: true }).waitFor();
  report.checks.push({ id: 'QA09-01', result: 'PASS', session_status: 401 });
  for (const path of anonymousPaths) {
    await page.goto(base + path);
    await page.getByRole('heading', { name: '请先登录账号', exact: true }).first().waitFor();
    await page.waitForLoadState('networkidle');
    assert.equal(await page.getByRole('heading', { name: '请先登录账号', exact: true }).count(), 1, path);
    assert.equal(await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).count(), 0, path);
    report.checks.push({ id: 'QA09-05', path, state: 'anonymous', result: 'PASS' });
  }
  await page.goto(base + '/home');
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await page.waitForURL('**/login');
  await login(page, credentials);
  await page.locator('.home-patient strong').waitFor();
  assert.equal((await context.request.get(base + '/api/session')).status(), 200);
  report.checks.push({ id: 'QA09-04', result: 'PASS' });
  for (const path of ['/home', '/records', '/archives', '/archives/changes', '/summaries', '/drafts', '/account/reminders', '/alerts']) {
    await page.goto(base + path);
    await page.waitForLoadState('networkidle');
    assert.equal(await page.getByRole('heading', { name: '请先登录账号', exact: true }).count(), 0);
    assert.equal(await page.locator('.rich-read-state[role="alert"]').count(), 0);
    report.checks.push({ id: 'QA09-05', path, result: 'PASS' });
  }
  await context.clearCookies();
  await page.goto(base + '/records');
  await page.getByRole('heading', { name: '请先登录账号', exact: true }).waitFor();
  report.checks.push({ id: 'QA09-02', result: 'PASS', expired_session_hides_records: true });
  await context.close();

  const emptyContext = await browser.newContext();
  const emptyPage = await emptyContext.newPage();
  emptyPage.on('pageerror', error => report.page_errors.push(error.message));
  await emptyPage.goto(base + '/login');
  await login(emptyPage, unassociated);
  await emptyPage.getByRole('heading', { name: '还没有关联的孩子', exact: true }).waitFor();
  await emptyPage.goto(base + '/records');
  await emptyPage.getByRole('heading', { name: '尚无可访问的患儿记录', exact: true }).waitFor();
  const relations = emptyPage.waitForResponse(response => response.url() === base + '/api/relations');
  await emptyPage.getByRole('button', { name: '重新读取患儿关联', exact: true }).click();
  assert.equal((await relations).status(), 200);
  await emptyPage.getByRole('heading', { name: '尚无可访问的患儿记录', exact: true }).waitFor();
  await emptyPage.getByRole('button', { name: '管理患儿关联', exact: true }).click();
  await emptyPage.waitForURL('**/associations');
  report.checks.push({ id: 'QA09-03', result: 'PASS', relation_count: 0 });
  assert.equal(report.page_errors.length, 0);
});
