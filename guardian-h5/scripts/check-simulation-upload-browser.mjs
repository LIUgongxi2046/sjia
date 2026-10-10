import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
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
  assert.ok(response.headers()['cache-control'].includes('no-store'));
  const value = await response.json(); if (schema) responses.push({ schema, value }); return value;
}
try {
  const bootstrap = await api('/api/auth/bootstrap');
  const login = await context.request.post(origin + '/api/auth/login', { data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(login.ok());
  await page.goto(origin + '/archives/batches');
  await page.getByRole('button', { name: '选择已有资料', exact: true }).click();
  const source = page.getByRole('combobox', { name: '资料用途授权', exact: true });
  await expect(source).toBeEnabled();
  const value = await source.locator('option').filter({ hasText: 't40-authorized-input' }).getAttribute('value');
  assert.ok(value); await source.selectOption(value);
  const pdf = await readFile('../.planning-work/t40-upload-input.pdf');
  await page.getByLabel('选择原始资料文件', { exact: true }).setInputFiles([
    { name: 'SIMULATION report.pdf', mimeType: 'application/pdf', buffer: pdf },
    { name: 'SIMULATION notes.txt', mimeType: 'text/plain', buffer: Buffer.from('SIMULATION\n铁蛋白 1810.00 ng/mL\n采样时间 2026-09-01 08:30\n') },
  ]);
  await page.getByRole('button', { name: '保存所选完整原件', exact: true }).click();
  await expect(page.getByRole('button', { name: '采用已保存原件', exact: true })).toBeVisible();
  const groupId = new URL(page.url()).searchParams.get('upload_group'); assert.ok(groupId);
  const group = await api('/api/uploads/' + groupId, 'UploadGroupView');
  assert.equal(group.status, 'available'); assert.equal(group.files.length, 2);
  assert.deepEqual(group.files.map(file => [file.media_type, file.page_count]), [['application/pdf', 2], ['text/plain', 1]]);
  check('浏览器实际保存 PDF 与文本完整原件并读取准确页数');
  await page.reload();
  await expect(page.getByRole('button', { name: '采用已保存原件', exact: true })).toBeVisible();
  assert.equal(new URL(page.url()).searchParams.get('upload_group'), groupId);
  assert.equal(await source.inputValue(), value);
  check('刷新恢复准确上传组及原资料用途授权');
  await page.getByRole('button', { name: '重新读取上传结果', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: '采用已保存原件', exact: true })).toBeEnabled();
  check('键盘操作重新读取实际保存结果');
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await page.locator('.phone-scroll').evaluate(scroll => {
      window.uploadTextSizes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')]
        .map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      for (const item of window.uploadTextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
    });
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth }));
    assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ width, geometry })); measurements.push({ width, geometry });
    await page.evaluate(() => { for (const item of window.uploadTextSizes) item.node.style.fontSize = item.original; delete window.uploadTextSizes; });
  }
  check('五种宽度及200%文字保存完整内容且没有横向溢出');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  await page.waitForLoadState('networkidle'); const count = requests.length;
  await page.waitForTimeout(1200); assert.equal(requests.length, count);
  check('私人文件不进入浏览器存储且页面没有循环读取');
  await writeFile('../.planning-work/t40-upload-browser.json', JSON.stringify({ mode: 'SIMULATION', task_id: 'T40', status: 'PASS', checked_at: new Date().toISOString(), group_id: groupId, checks, responses, requests, measurements, visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat_browser: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
