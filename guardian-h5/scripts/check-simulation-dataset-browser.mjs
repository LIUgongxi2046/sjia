import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.planning-work/t48-simulation.json', 'utf8'));
const input = fixture.inputs.format.payload;
const origin = 'http://127.0.0.1:5183', checks = [], responses = [], requests = [], measurements = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.on('pageerror', error => { throw error; });
page.on('request', request => { const path = new URL(request.url()).pathname; if (path.startsWith('/api/')) requests.push({ path, method: request.method() }); });
function check(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(path, schema) {
  const response = await context.request.get(origin + path);
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  const value = await response.json(); if (schema) responses.push({ schema, value }); return value;
}
function prepare(operation, dataset) {
  return JSON.parse(execFileSync('../backend/.venv/bin/python', ['../.planning-work/prepare_t48_browser.py', operation,
    dataset.dataset_id, dataset.version], { encoding: 'utf8', env: { ...process.env, TMPDIR: process.cwd() + '/../.planning-work/tmp' } }));
}
async function chooseOperation(name, dataset) {
  await page.getByRole('button', { name: `${dataset.protocol_ref} · 版本 ${dataset.version}`, exact: true }).click();
  await page.getByRole('button', { name, exact: true }).click();
}
try {
  const bootstrap = await api('/api/auth/bootstrap');
  const login = await context.request.post(origin + '/api/v2/support/session/login', {
    data: { username: 'simulation-support', password: process.env.SJIA_SIMULATION_PASSWORD }, headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(login.ok());
  await page.goto(origin + '/support/datasets');
  await page.getByRole('button', { name: '选择获准资料', exact: true }).click();
  await page.getByRole('button', { name: '登记新的用途清单', exact: true }).click();
  await page.getByLabel('固定评价方案引用', { exact: true }).fill(input.protocol_ref);
  await page.getByLabel('真实来源清单引用', { exact: true }).fill(input.source_manifest_ref);
  await page.getByLabel('获准用途副本标识，每行一份', { exact: true }).fill(input.purpose_copy_ids.join('\n'));
  await page.getByLabel('独立答案版本引用', { exact: true }).fill(input.label_version_ref);
  await page.getByRole('button', { name: '保存用途清单', exact: true }).click();
  await expect(page.getByRole('heading', { name: '资料已保存并重新读取', exact: true })).toBeVisible();
  let dataset = await api('/api/v2/support/datasets/' + new URL(page.url()).searchParams.get('dataset_id'), 'DatasetView');
  assert.equal(dataset.version, '1'); assert.equal(dataset.counts.files, 2);
  check('受控页面通过实际表单创建非空评价集合并读取准确版本');
  await page.reload(); await expect(page.getByRole('heading', { name: '正在查看准确保存版本', exact: true })).toBeVisible();
  check('刷新按保存链接恢复用途及完整统计范围');
  const partition = prepare('partition', dataset);
  await chooseOperation('登记患儿集合划分', dataset);
  await page.getByLabel('患儿标识', { exact: true }).fill(partition.patient_id);
  await page.getByLabel('患儿用途授权标识', { exact: true }).fill(partition.authorization_id);
  await page.getByLabel('集合划分依据引用', { exact: true }).fill(partition.evidence_ref);
  const lock = spawn('../backend/.venv/bin/python', ['../.planning-work/lock_t48_simulation.py'], {
    env: { ...process.env, TMPDIR: process.cwd() + '/../.planning-work/tmp' } });
  const lockExited = new Promise((resolve, reject) => { lock.on('error', reject); lock.on('exit', code => code === 0 ? resolve() : reject(new Error('模拟读取锁检查失败'))); });
  await new Promise((resolve, reject) => { lock.stdout.once('data', data => {
    if (data.toString().includes('SIMULATION_LOCK_ACQUIRED')) resolve(); else reject(new Error('模拟锁没有准确建立'));
  }); lock.once('error', reject); });
  await page.getByRole('button', { name: '登记患儿集合划分', exact: true }).click();
  await expect(page.getByRole('heading', { name: '提交准备未完成', exact: true })).toBeVisible();
  const requestId = new URL(page.url()).searchParams.get('request_id'); assert.ok(requestId);
  await lockExited;
  assert.equal((await api('/api/v2/support/datasets/' + dataset.dataset_id, 'DatasetView')).version, '1');
  check('实际数据库读取锁超时后保留原请求且没有新增业务版本');
  await page.getByRole('dialog', { name: '登记患儿集合划分', exact: true }).getByRole('button', { name: '重新准备原请求', exact: true }).click();
  await expect(page.getByRole('heading', { name: '资料已保存并重新读取', exact: true })).toBeVisible();
  dataset = await api('/api/v2/support/datasets/' + dataset.dataset_id, 'DatasetView');
  assert.equal(dataset.version, '2'); assert.equal(dataset.partitions[0].partition, 'development');
  assert.equal(new URL(page.url()).searchParams.get('request_id'), requestId);
  check('读取锁释放后使用同一原请求准确完成保存');
  check('受控页面按患儿保存准确划分及模拟批准依据');
  const freeze = prepare('freeze', dataset);
  await chooseOperation('冻结评价集合', dataset);
  await page.getByLabel('冻结确认依据引用', { exact: true }).fill(freeze.evidence_ref);
  await page.getByRole('button', { name: '冻结评价集合', exact: true }).click();
  await expect(page.getByRole('heading', { name: '资料已保存并重新读取', exact: true })).toBeVisible();
  dataset = await api('/api/v2/support/datasets/' + dataset.dataset_id, 'DatasetView');
  assert.equal(dataset.status, 'frozen'); assert.equal(dataset.version, '3');
  check('受控页面冻结准确清单并读取独立保存版本');
  const runInput = prepare('run', dataset);
  await chooseOperation('创建固定版本评价运行', dataset);
  await page.getByLabel('已批准规则版本标识，每行一项', { exact: true }).fill(runInput.config_ids.join('\n'));
  await page.getByLabel('本次采用的已验证程序摘要', { exact: true }).fill(runInput.code_version);
  await page.getByRole('button', { name: '创建固定版本评价运行', exact: true }).click();
  await expect(page.getByRole('heading', { name: '评价运行已受理并重新读取', exact: true })).toBeVisible();
  const runId = new URL(page.url()).searchParams.get('run_id'); assert.ok(runId);
  execFileSync('../backend/.venv/bin/python', ['../.planning-work/execute_t48_browser.py', runId], {
    encoding: 'utf8', env: { ...process.env, TMPDIR: process.cwd() + '/../.planning-work/tmp' } });
  await expect(page.getByRole('heading', { name: '评价结果已保存', exact: true })).toBeVisible();
  await expect(page.locator('details')).toHaveCount(9);
  const run = await api('/api/v2/support/evaluation-runs/' + runId, 'EvaluationRunView');
  const denominators = await api('/api/v2/support/evaluation-runs/' + runId + '/denominators', 'EvaluationDenominatorsView');
  const result = await api('/api/v2/support/evaluation-runs/' + runId + '/results', 'EvaluationResultsView');
  assert.equal(run.task.status, 'completed'); assert.equal(result.input_sha256, denominators.input_sha256);
  check('页面提交后实际消费指定模拟任务并显示九类保存结果');
  await page.locator('details').first().locator('summary').click();
  await expect(page.getByText(`结果引用：/support/evaluation-runs/${runId}/results#files`, { exact: true })).toBeVisible();
  check('结果内容可以追溯准确对象及保存引用');
  await page.reload(); await expect(page.getByRole('heading', { name: '评价结果已保存', exact: true })).toBeVisible();
  await page.locator('details').first().locator('summary').focus(); await page.keyboard.press('Enter');
  await expect(page.getByText(`结果引用：/support/evaluation-runs/${runId}/results#files`, { exact: true })).toBeVisible();
  check('结果刷新保持准确运行且键盘可以展开证据内容');
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await page.locator('.phone-scroll').evaluate(scroll => {
      window.datasetTextSizes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')]
        .map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      for (const item of window.datasetTextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
    });
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth }));
    assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ width, geometry })); measurements.push({ width, geometry });
    await page.evaluate(() => { for (const item of window.datasetTextSizes) item.node.style.fontSize = item.original; delete window.datasetTextSizes; });
  }
  check('五种宽度与200%文字保留完整统计和追溯内容');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  await page.waitForLoadState('networkidle'); const count = requests.length; await page.waitForTimeout(1200); assert.equal(requests.length, count);
  check('完成任务停止自动查询且评价资料没有浏览器存储');
  await writeFile('../.planning-work/t48-dataset-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T48',
    checked_at: new Date().toISOString(), dataset_id: dataset.dataset_id, run_id: runId, checks, responses, requests, measurements,
    visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat_browser: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
