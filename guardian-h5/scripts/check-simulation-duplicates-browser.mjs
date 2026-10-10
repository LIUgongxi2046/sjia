import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect } from 'playwright/test';

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t44-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const origin = 'http://127.0.0.1:5183';
const checks = [];
const run = crypto.randomUUID();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(60000);
const requests = [];
page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(request.url()); });
page.on('pageerror', error => { throw error; });
page.on('response', async response => {
  const path = new URL(response.url()).pathname;
  if (response.request().method() === 'POST' && path.startsWith('/api/v2/')) {
    process.stdout.write(`实际提交响应：${path} ${response.status()}${response.status() >= 400 ? ` ${await response.text()}` : ''}\n`);
  }
});

try {
function record(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + '/api' + path, { method,
    ...(data ? { data } : {}), ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}
const bootstrap = await api('GET', '/auth/bootstrap');
await api('POST', '/auth/login', { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
const directory = await api('GET', `/v2/patients/${fixture.patient_id}/archive-relations/search`);
const previous = directory.relations.find(value => value.status !== 'released' && [value.left_file_id, value.right_file_id].includes(fixture.files[0])
  && [value.left_file_id, value.right_file_id].includes(fixture.files[2]));
await page.goto(origin + '/archives/duplicates' + (previous ? `?relation=${previous.relation_id}` : ''));
await page.getByRole('heading', { name: '重复资料核对', exact: true }).waitFor();
await page.getByText('选择当前获准来源与关系候选', { exact: true }).click();
await page.getByLabel('原始资料', { exact: true }).locator(`option[value="${fixture.files[0]}"]`).waitFor({ state: 'attached' });
if (!previous) {
  await page.getByLabel('原始资料', { exact: true }).selectOption(fixture.files[0]);
  await page.getByLabel('对应资料', { exact: true }).selectOption(fixture.files[2]);
} else await page.getByLabel('原始资料', { exact: true }).waitFor({ state: 'visible' });
record('重复核对页面读取实际患儿原件和来源选择');
await page.getByLabel('与已有资料的关系', { exact: true }).selectOption('document_copy');
await page.getByLabel('关系依据', { exact: true }).fill(`SIMULATION浏览器依据 ${run}`);
await page.getByText('关系状态、原因与专业核对', { exact: true }).click();
await page.getByLabel('确认或解除原因', { exact: true }).fill(`SIMULATION本人核对原件 ${run}`);
const savedResponse = page.waitForResponse(response => response.request().method() === 'POST'
  && /\/archive-relations(?:\/[^/]+\/relationships)?$/.test(new URL(response.url()).pathname) && [200, 201].includes(response.status()));
await page.getByRole('button', { name: '确认关系', exact: true }).click();
const receipt = await (await savedResponse).json();
await page.getByRole('heading', { name: '资料关系已经保存', exact: true }).waitFor();
assert.equal(receipt.resource_type, 'archive_relation');
record('实际页面输入保存并重新读取准确关系版本');
await page.reload();
await page.getByRole('heading', { name: '资料关系已经保存', exact: true }).waitFor();
assert.ok(page.url().includes('request='));
record('刷新通过原请求恢复保存回执与正文');
await page.getByRole('button', { name: '继续整理当前关系', exact: true }).click();
await page.getByText('关系状态、原因与专业核对', { exact: true }).click();
await page.getByLabel('确认或解除原因', { exact: true }).fill(`SIMULATION解除资料关联 ${run}`);
await expect(page.getByRole('button', { name: '解除当前关联', exact: true })).toBeEnabled({ timeout: 15000 });
const releasedResponse = page.waitForResponse(response => response.request().method() === 'POST'
  && new URL(response.url()).pathname.endsWith(`/archive-relations/${receipt.resource_id}/relationships`) && response.status() === 200);
await page.getByRole('button', { name: '解除当前关联', exact: true }).click();
const released = await (await releasedResponse).json();
await page.getByRole('heading', { name: '资料关系已经保存', exact: true }).waitFor();
assert.equal(BigInt(released.version), BigInt(receipt.version) + 1n);
const current = await api('GET', `/v2/archive-relations/${receipt.resource_id}`);
assert.equal(current.status, 'released');
record('解除操作保存实际新版本并能够重新读取');
const historical = await api('GET', `/v2/archive-relations/${receipt.resource_id}?version=${receipt.version}`);
assert.equal(historical.status, 'confirmed');
assert.equal(historical.evidence_ref, `SIMULATION浏览器依据 ${run}`);
record('解除后原依据和准确历史版本仍可查询');
for (const width of [320, 360, 390, 430, 580]) {
  await page.setViewportSize({ width, height: 844 });
  const geometry = await page.evaluate(() => {
    const root = document.querySelector('sjia-ui2')?.shadowRoot ?? document.querySelector('[data-ui2-host]')?.shadowRoot;
    const phone = root?.querySelector('.phone-scroll') ?? [...document.querySelectorAll('*')].flatMap(value => value.shadowRoot ? [...value.shadowRoot.querySelectorAll('.phone-scroll')] : [])[0];
    if (!phone) throw new Error('缺少 UI 2.0 页面容器');
    return { client: phone.clientWidth, scroll: phone.scrollWidth, storage: localStorage.length + sessionStorage.length };
  });
  assert.ok(geometry.scroll <= geometry.client + 1, `${width}: ${JSON.stringify(geometry)}`);
  assert.equal(geometry.storage, 0);
}
record('五种手机宽度无横向溢出并保持资料不进入浏览器持久存储');
const count = requests.length;
await page.waitForTimeout(3000);
assert.equal(requests.length, count);
record('稳定页面没有重复查询或恢复循环');
await page.getByRole('button', { name: '继续整理当前关系', exact: true }).focus();
await page.keyboard.press('Enter');
await page.getByRole('button', { name: '确认关系', exact: true }).waitFor();
record('键盘操作能够继续整理当前已保存关系');
await writeFile('../.planning-work/t44-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS',
  captured_at: new Date().toISOString(), count: checks.length, checks, relation_id: receipt.resource_id,
  version: released.version, visual_comparison: 'NOT_RUN', real_device: 'NOT_RUN', wechat: 'NOT_RUN' }, null, 2));
process.stdout.write(JSON.stringify({ mode: 'SIMULATION', status: 'PASS', count: checks.length }) + '\n');
} finally { await context.close(); await browser.close(); }
