import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

process.loadEnvFile('../.simulation-work/environment.env');
const expect = baseExpect.configure({ timeout: 20000 });
const fixture = JSON.parse(await readFile('../.planning-work/t04-field-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const origin = 'http://127.0.0.1:5183', checks = [], responses = [], errors = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.on('dialog', dialog => dialog.accept());
page.on('pageerror', error => errors.push(error.message));
page.on('response', response => { const path = new URL(response.url()).pathname;
  if (path.startsWith('/api/')) responses.push({ path, method: response.request().method(), status: response.status() }); });
function check(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function readDraft(id) {
  const response = await context.request.get(origin + '/api/drafts/' + id);
  assert.equal(response.status(), 200); assert.ok(response.headers()['cache-control'].includes('no-store'));
  return response.json();
}
try {
  await page.goto(origin + '/login');
  await expect(page.getByLabel('账号', { exact: true })).toBeEnabled();
  await page.getByLabel('账号', { exact: true }).fill('simulation-family');
  await page.getByLabel('密码', { exact: true }).fill(process.env.SJIA_SIMULATION_PASSWORD);
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await expect(page.getByRole('heading', { name: '陪伴孩子，健康成长', exact: true })).toBeVisible();
  await page.goto(origin + '/register/observations');
  const configurationResponse = await context.request.get(origin + `/api/patients/${fixture.patient_id}/field-config?kind=observation`);
  const configuration = await configurationResponse.json();
  assert.equal(configuration.state, 'available');
  await expect(page.getByLabel('体温原值', { exact: true })).toBeEnabled();
  await page.getByLabel('体温原值', { exact: true }).fill('38.0');
  await page.getByText('测量方式与补充信息', { exact: true }).click();
  await page.getByLabel('体温单位', { exact: true }).selectOption('摄氏度（°C）');
  const note = 'SIMULATION草稿保存与恢复 ' + randomUUID();
  await page.getByRole('textbox', { name: '补充说明', exact: true }).fill(note);
  const created = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === `/api/patients/${fixture.patient_id}/drafts`);
  await page.getByRole('button', { name: '保存为草稿', exact: true }).click();
  const creation = await created; assert.equal(creation.status(), 201);
  const first = await creation.json();
  await page.waitForURL(url => url.searchParams.get('draft') === first.draft_id);
  const saved = await readDraft(first.draft_id);
  assert.equal(saved.patient_id, fixture.patient_id); assert.equal(saved.form_type, 'observation');
  assert.equal(saved.payload.temp, '38.0'); assert.equal(saved.payload.description, note);
  assert.equal(saved.payload.measuredTime.precision, 'unknown');
  assert.equal(saved.payload.observedTime.precision, 'unknown');
  await page.reload();
  await expect(page.getByLabel('体温原值', { exact: true })).toHaveValue('38.0');
  await expect(page.getByRole('textbox', { name: '补充说明', exact: true })).toHaveValue(note);
  check('草稿通过实际页面保存到数据库，刷新恢复原值和独立未知医学时间');
  await context.setOffline(true);
  const updatedNote = note + '，继续补充观察说明';
  await page.getByRole('textbox', { name: '补充说明', exact: true }).fill(updatedNote);
  await page.getByRole('button', { name: '保存为草稿', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  await expect(page.getByRole('textbox', { name: '补充说明', exact: true })).toHaveValue(updatedNote);
  await expect(page.getByRole('button', { name: '保存为草稿', exact: true })).toBeEnabled();
  await context.setOffline(false);
  const unchanged = await readDraft(first.draft_id);
  assert.equal(unchanged.version, saved.version); assert.equal(unchanged.payload.description, note);
  check('真实浏览器断网时保存失败，当前输入保留，数据库正文与版本保持准确');
  const updated = page.waitForResponse(response => response.request().method() === 'PUT'
    && new URL(response.url()).pathname === '/api/drafts/' + first.draft_id);
  await page.getByRole('button', { name: '保存为草稿', exact: true }).click();
  const update = await updated; assert.equal(update.status(), 200);
  const current = await update.json();
  assert.equal(current.draft_id, first.draft_id); assert.equal(BigInt(current.version), BigInt(saved.version) + 1n);
  const reread = await readDraft(first.draft_id);
  assert.equal(reread.payload.description, updatedNote); assert.equal(reread.version, current.version);
  await page.reload(); await expect(page.getByRole('textbox', { name: '补充说明', exact: true })).toHaveValue(updatedNote);
  check('网络恢复后本人重试同一草稿，版本递增一次并重新读取准确正文');
  await page.goto(origin + '/drafts?draft=' + first.draft_id);
  await page.getByRole('link', { name: '继续编辑', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/register/observations' && url.searchParams.get('draft') === first.draft_id);
  await expect(page.getByLabel('体温原值', { exact: true })).toHaveValue('38.0');
  await expect(page.getByRole('textbox', { name: '补充说明', exact: true })).toHaveValue(updatedNote);
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  assert.deepEqual(errors, []);
  check('草稿目录按本人准确身份继续编辑，内容通过服务重新取得');
  await writeFile('../.planning-work/t54-draft-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS',
    task_id: 'T54', checked_at: new Date().toISOString(), checks, responses, errors,
    draft_id: first.draft_id, first_version: first.version, current_version: current.version,
    script_sha256: createHash('sha256').update(await readFile('scripts/check-simulation-ui2-draft.mjs')).digest('hex'),
    visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
