import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, credentials: { type: 'string' },
  readiness: { type: 'string' }, source: { type: 'string' } } });
for (const key of ['report', 'credentials', 'readiness', 'source']) assert.ok(values[key], key);
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const readiness = JSON.parse(await readFile(resolve(values.readiness), 'utf8'));
const source = JSON.parse(await readFile(resolve(values.source), 'utf8'));
assert.equal(readiness.enabled_releases, 0);
assert.equal(readiness.counts.Verification, 0);
assert.equal(readiness.counts.AlertEvent, 0);
const homes = readiness.responses.filter(item => item.path.endsWith('/home'));
assert.equal(homes.length, 5);
assert.ok(homes.every(item => item.status === 200 && item.body.assessment_state === 'configuration_pending'));
const origin = 'http://127.0.0.1:5178';
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), checks: [], page_errors: [],
  positive_calculation: 'NOT_RUN', positive_family_event: 'NOT_RUN', new_model_calls: 0,
  new_clinical_writes: 0, visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: 9233, headless: true });
const { context } = native;
const page = await context.newPage();
page.setDefaultTimeout(20000);
page.on('pageerror', failure => report.page_errors.push(failure.message));
async function record(name, details = {}) {
  report.checks.push({ name, result: 'PASS', ...details });
  await writeFile(output, JSON.stringify(report, null, 2));
}
try {
  await page.goto(origin + '/login');
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL('**/home');
  const session = await context.request.get(origin + '/api/session');
  assert.equal(session.status(), 200);
  assert.equal((await session.json()).principal_id, source.actor_id);
  for (const home of homes) {
    const patient = home.body.patient_id;
    await page.goto(origin + '/associations');
    const card = page.locator('.rich-child-identity').filter({ has: page.getByRole('heading', { name: home.body.display_id, exact: true }) });
    await card.waitFor();
    const select = card.getByRole('button', { name: '选择此患儿' });
    if (await select.count()) { await select.click(); await page.waitForURL('**/home'); }
    else await page.goto(origin + '/home');
    await page.getByRole('heading', { name: '临床提示尚未启用', exact: true }).waitFor();
    const response = await context.request.get(origin + `/api/patients/${patient}/home`);
    assert.equal(response.status(), 200);
    const current = await response.json();
    assert.equal(current.assessment_state, 'configuration_pending');
    assert.equal(current.current_alert_id, null);
    await page.getByRole('button', { name: '查看服务状态与资料依据', exact: true }).click();
    await page.waitForURL('**/alerts/current');
    await page.getByRole('heading', { name: '临床提示尚未启用', exact: true }).waitFor();
    await record('ACTUAL_HOME_AND_DETAIL_MATCH_UNPUBLISHED_STATE', { patient_id: patient });
  }
  await page.goto(origin + '/alerts');
  await page.getByRole('heading', { name: '当前账号没有访问权限', exact: true }).waitFor();
  assert.equal(await page.getByRole('heading', { name: '尚无获准提示', exact: true }).count(), 0);
  await record('ALERT_LIST_DENIAL_REMAINS_EXPLICIT');
  await page.goto(origin + '/support/login');
  await page.locator('input[autocomplete="username"]').fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await page.waitForURL('**/support/data-use');
  await page.goto(origin + '/support/assessment?evaluation_kind=dynamic');
  await page.getByRole('heading', { name: '当前账号没有所需访问权限', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '创建评估任务', exact: true }).count(), 0);
  await record('CALCULATION_REQUIRES_ACTUAL_AUTHORIZED_ROLE');
  assert.deepEqual(report.page_errors, []);
  report.result = 'PASS';
} catch (failure) {
  report.result = 'FAIL'; report.failure = { name: failure.name, message: failure.message, pathname: new URL(page.url()).pathname };
  throw failure;
} finally {
  report.finished_at = new Date().toISOString();
  await writeFile(output, JSON.stringify(report, null, 2)); await native.close();
  console.log(JSON.stringify({ result: report.result, display_checks: report.checks.length,
    positive_calculation: report.positive_calculation, positive_family_event: report.positive_family_event }));
}
