import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t44-fixture.json', 'utf8'));
const evidence = JSON.parse(await readFile('../.planning-work/t44-simulation.json', 'utf8'));
assert.equal(evidence.status, 'PASS');
assert.equal(evidence.mode, 'SIMULATION');
const proofId = evidence.proof_ids[fixture.facts[1]];
assert.ok(proofId);
const origin = 'http://127.0.0.1:5183';
const checks = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(60000);
page.on('pageerror', error => { throw error; });

try {
function record(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + '/api' + path, { method, ...(data ? { data } : {}),
    ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}
const bootstrap = await api('GET', '/auth/bootstrap');
const session = await api('POST', '/v2/support/session/login', { username: 'simulation-support',
  password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
assert.equal(session.audience, 'support');
const proofResponse = page.waitForResponse(value => new URL(value.url()).pathname.endsWith(`/support/verifications/${proofId}`));
await page.goto(origin + `/support/verification?verification_id=${proofId}`);
const loaded = await proofResponse;
assert.equal(loaded.status(), 200, await loaded.text());
await page.getByRole('heading', { name: '核对结果已保存', exact: true }).waitFor();
await page.getByText('同次采样、独立检查与更正的专业核对', { exact: true }).click();
await page.getByRole('button', { name: '读取当前获准采样关系', exact: true }).click();
const select = page.getByLabel('指定采样关系', { exact: true });
await select.locator('option[value]:not([value=""])').first().waitFor({ state: 'attached' });
const relationId = await select.locator('option[value]:not([value=""])').first().getAttribute('value');
assert.ok(relationId);
await select.selectOption(relationId);
record('实际专业核对页面读取当前病例和准确采样关系');
await page.getByLabel('采样关系核对原因', { exact: true }).fill(`SIMULATION专业页面采样依据 ${crypto.randomUUID()}`);
const response = page.waitForResponse(value => value.request().method() === 'POST'
  && new URL(value.url()).pathname.endsWith(`/support/archive-relations/${relationId}/verification`));
await page.getByRole('button', { name: '保存指定采样关系核对', exact: true }).click();
const savedResponse = await response;
assert.equal(savedResponse.status(), 200, await savedResponse.text());
const receipt = await savedResponse.json();
await page.getByText('专业采样关系已经保存', { exact: false }).waitFor();
const current = await api('GET', `/v2/archive-relations/${relationId}`);
assert.equal(current.verification_id, proofId);
assert.equal(current.version, receipt.version);
record('专业页面保存真实关系版本与指定模拟核对证明');
await page.reload();
await page.getByRole('heading', { name: '核对结果已保存', exact: true }).waitFor();
await page.getByText('同次采样、独立检查与更正的专业核对', { exact: true }).click();
await page.getByText('专业采样关系已经保存', { exact: false }).waitFor();
record('专业页面刷新后通过原请求恢复准确核对结果');
await writeFile('../.planning-work/t44-simulation-sampling-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS',
  captured_at: new Date().toISOString(), count: checks.length, checks, relation_id: relationId, proof_id: proofId,
  visual_comparison: 'NOT_RUN', real_device: 'NOT_RUN', clinical_qualification_acceptance: 'NOT_RUN' }, null, 2));
process.stdout.write(JSON.stringify({ mode: 'SIMULATION', status: 'PASS', count: checks.length }) + '\n');
} finally { await context.close(); await browser.close(); }
