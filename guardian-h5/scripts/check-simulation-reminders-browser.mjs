import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { chromium } from 'playwright';

process.loadEnvFile('../.simulation-work/environment.env');
const execute = promisify(execFile);
const origin = 'http://127.0.0.1:5183';
const checks = [];
const run = crypto.randomUUID();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(60000);
page.setDefaultNavigationTimeout(30000);
const requests = [];
page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push(request.url()); });
page.on('pageerror', error => { throw error; });

try {

async function api(method, path, data, csrf) {
  const result = await context.request.fetch(origin + '/api' + path, {
    method, ...(data ? { data } : {}), ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(result.ok(), `${method} ${path}: ${result.status()} ${await result.text()}`);
  return result.json();
}
function record(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }

const bootstrap = await api('GET', '/auth/bootstrap');
const session = await api('POST', '/auth/login', { username: 'simulation-family',
  password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
assert.equal(session.audience, 'family');
await page.goto(origin + '/account/reminders');
await page.getByLabel('提醒对象', { exact: true }).selectOption('record_task');
await page.getByText('创建本人明确设置的记录任务', { exact: true }).click();
await page.getByLabel('本人记录任务名称', { exact: true }).fill(`SIMULATION浏览器任务 ${run}`);
await page.getByLabel('本人记录任务说明', { exact: true }).fill('浏览器实际输入、保存和刷新验证');
const policies = await page.getByLabel('本人记录任务保留政策').locator('option').evaluateAll(nodes =>
  nodes.map(node => node.value).filter(Boolean));
assert.equal(policies.length, 1);
await page.getByLabel('本人记录任务保留政策').selectOption(policies[0]);
const taskSaved = page.waitForResponse(response => response.request().method() === 'POST'
  && /\/personal-record-tasks$/.test(new URL(response.url()).pathname));
await page.getByRole('button', { name: '保存本人记录任务', exact: true }).click();
const taskResponse = await taskSaved;
assert.equal(taskResponse.status(), 201);
const task = await taskResponse.json();
await page.getByRole('heading', { name: '本人记录任务已经保存', exact: true }).waitFor();
record('浏览器实际创建本人任务并核验保存回读');
await page.getByRole('button', { name: '继续查看本人记录任务', exact: true }).click();
await page.getByLabel('准确目标与版本', { exact: true }).selectOption(task.resource_id);
await page.getByLabel('时区', { exact: true }).fill('Asia/Shanghai');
const due = new Date(Date.now() + 45000);
const local = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
  day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(due).replace(' ', 'T');
await page.getByLabel('提醒时间', { exact: true }).fill(local);
await page.getByRole('switch', { name: '启用资料补充提醒', exact: true }).check();
const reminderSaved = page.waitForResponse(response => response.request().method() === 'POST'
  && /\/material-reminders$/.test(new URL(response.url()).pathname));
await page.getByRole('button', { name: '保存提醒设置', exact: true }).click();
const reminderResponse = await reminderSaved;
assert.equal(reminderResponse.status(), 201);
const reminder = await reminderResponse.json();
await page.getByRole('heading', { name: '提醒设置已经保存', exact: true }).waitFor();
record('浏览器明确启用提醒并保存准确时间');
await page.getByRole('button', { name: '继续查看当前提醒', exact: true }).click();
await page.reload();
await page.getByRole('heading', { name: '当前保存结果', exact: true }).waitFor();
assert.equal(await page.getByRole('switch', { name: '启用资料补充提醒', exact: true }).isChecked(), true);
record('刷新后重新读取当前设置和本人任务');
const delay = due.getTime() - Date.now();
assert.ok(delay < 60000);
if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay + 100));
const scheduled = await execute('../backend/.venv/bin/python', ['../.planning-work/schedule_simulation.py'], {
  env: { ...process.env, TMPDIR: process.cwd() + '/../.planning-work/tmp' }, timeout: 60000 });
const schedule = JSON.parse(scheduled.stdout);
const notice = schedule.items.find(item => item.reminder_id === reminder.resource_id);
assert.ok(notice?.notice_id);
await page.reload();
await page.getByRole('button', { name: '查看提醒内容', exact: true }).waitFor();
const delivered = page.waitForResponse(response => response.request().method() === 'POST'
  && new URL(response.url()).pathname === `/api/v2/material-reminder-notices/${notice.notice_id}/delivered`);
const read = page.waitForResponse(response => response.request().method() === 'POST'
  && new URL(response.url()).pathname === `/api/v2/material-reminder-notices/${notice.notice_id}/read`);
await page.getByRole('button', { name: '查看提醒内容', exact: true }).click();
await page.getByText('【模拟开发】您保存的资料任务已经到达提醒时间，请打开任务核对处理情况。', { exact: true }).scrollIntoViewIfNeeded();
const deliveredResponse = await delivered;
assert.equal(deliveredResponse.status(), 200);
assert.equal((await deliveredResponse.json()).status, 'delivered');
const readResponse = await read;
assert.equal(readResponse.status(), 200);
assert.ok((await readResponse.json()).read_at);
await page.getByText('【模拟开发】您保存的资料任务已经到达提醒时间，请打开任务核对处理情况。', { exact: true }).waitFor();
record('实际 DOM 正文显示后保存送达与已读回执');
await page.getByRole('button', { name: '关闭提醒内容', exact: true }).click();
for (const width of [320, 360, 390, 430, 580]) {
  await page.setViewportSize({ width, height: 844 });
  const geometry = await page.locator('[data-screen-id="UI2-32"]').evaluate(node => {
    const content = node.shadowRoot.querySelector('.phone-scroll');
    return { width: content.clientWidth, scroll: content.scrollWidth }; });
  assert.ok(geometry.scroll <= geometry.width, JSON.stringify({ width, ...geometry }));
  record(`模拟业务内容宽度 ${width} 没有水平溢出`);
}
assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
record('浏览器持久存储没有保存私人资料');
const before = requests.length;
await new Promise(resolve => setTimeout(resolve, 3000));
assert.equal(requests.length, before);
record('完成后保持稳定，没有持续恢复请求');
await page.getByRole('button', { name: '关闭提醒', exact: true }).focus();
const stopped = page.waitForResponse(response => response.request().method() === 'POST'
  && new URL(response.url()).pathname === `/api/v2/material-reminders/${reminder.resource_id}/settings`);
await page.keyboard.press('Enter');
assert.equal((await stopped).status(), 200);
await page.getByRole('heading', { name: '提醒设置已经保存', exact: true }).waitFor();
record('键盘操作能够关闭已保存的提醒');
await writeFile('../.planning-work/t53-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS',
  captured_at: new Date().toISOString(), checks, count: checks.length, task_id: task.resource_id,
  reminder_id: reminder.resource_id, notice_id: notice.notice_id, visual_comparison: 'NOT_RUN',
  real_device: 'NOT_RUN', wechat: 'NOT_RUN' }, null, 2));
process.stdout.write(JSON.stringify({ mode: 'SIMULATION', status: 'PASS', count: checks.length }) + '\n');
} finally {
  await context.close();
  await browser.close();
}
