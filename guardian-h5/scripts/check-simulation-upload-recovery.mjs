import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const inputs = JSON.parse(await readFile('../.planning-work/t40-upload-browser.json', 'utf8'));
const origin = 'http://127.0.0.1:5183', checks = [], responses = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage(); page.on('pageerror', error => { throw error; });
function check(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function get(path, schema) {
  const response = await context.request.get(origin + path); assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  const value = await response.json(); if (schema) responses.push({ schema, value }); return value;
}
try {
  const bootstrap = await get('/api/auth/bootstrap');
  const login = await context.request.post(origin + '/api/auth/login', { data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(login.ok()); const session = await login.json();
  const source = await get('/api/uploads/' + inputs.group_id, 'UploadGroupView');
  const groupId = randomUUID();
  const created = await context.request.post(`${origin}/api/patients/${source.patient_id}/uploads`, {
    data: { authorization_id: source.authorization_id, client_group_id: groupId }, headers: { 'X-CSRFToken': session.csrf_token } });
  assert.ok(created.ok());
  const pdf = await readFile('../.planning-work/t40-upload-input.pdf');
  const query = new URLSearchParams({ checksum: createHash('sha256').update(pdf).digest('hex'), size: String(pdf.length), ordinal: '1', media_type: 'application/pdf' });
  const uploaded = await context.request.put(`${origin}/api/uploads/${groupId}/files/${randomUUID()}?${query}`, {
    data: pdf, headers: { 'Content-Type': 'application/octet-stream', 'X-CSRFToken': session.csrf_token } });
  assert.equal(uploaded.status(), 201);
  await page.goto(`${origin}/archives/batches?upload_group=${groupId}`);
  await expect(page.getByRole('button', { name: '完成已经保存的原件组', exact: true })).toBeEnabled();
  check('刷新后读取已经保存而尚未完成的准确原件组');
  await page.getByRole('button', { name: '完成已经保存的原件组', exact: true }).click();
  await expect(page.getByRole('button', { name: '采用已保存原件', exact: true })).toBeVisible();
  const saved = await get('/api/uploads/' + groupId, 'UploadGroupView');
  assert.equal(saved.status, 'available'); assert.equal(saved.files.length, 1); assert.equal(saved.files[0].page_count, 2);
  check('原件组恢复后完成接收且没有重复上传文件');
  await page.goto(origin + '/archives/batches');
  await page.getByRole('button', { name: '选择已有资料', exact: true }).click();
  const sourceSelect = page.getByRole('combobox', { name: '资料用途授权', exact: true });
  await expect(sourceSelect).toBeEnabled(); await sourceSelect.selectOption(source.authorization_id);
  await page.getByLabel('选择原始资料文件', { exact: true }).setInputFiles({ name: 'SIMULATION invalid.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.7\ninvalid') });
  await page.getByRole('button', { name: '保存所选完整原件', exact: true }).click();
  await expect(page.getByRole('alert')).toBeVisible();
  const failedId = new URL(page.url()).searchParams.get('upload_group'); assert.ok(failedId);
  const failed = await get('/api/uploads/' + failedId, 'UploadGroupView'); assert.equal(failed.files.length, 0);
  check('浏览器实际提交无效 PDF 显示服务拒绝且没有保存原件');
  await page.getByLabel('选择原始资料文件', { exact: true }).setInputFiles({ name: 'SIMULATION corrected.txt', mimeType: 'text/plain', buffer: Buffer.from('SIMULATION更正后的完整资料\n铁蛋白 1810.00 ng/mL\n采样 2026-09-01 08:30') });
  await page.getByRole('button', { name: '保存所选完整原件', exact: true }).click();
  await expect(page.getByRole('button', { name: '采用已保存原件', exact: true })).toBeVisible();
  const corrected = await get('/api/uploads/' + failedId, 'UploadGroupView'); assert.equal(corrected.files.length, 1); assert.equal(corrected.files[0].media_type, 'text/plain');
  check('实际更正文件后使用原上传组保存且只有准确完整原件');
  await page.reload(); await expect(page.getByRole('button', { name: '采用已保存原件', exact: true })).toBeVisible();
  assert.equal(new URL(page.url()).searchParams.get('upload_group'), failedId);
  check('更正后的保存结果可以刷新恢复');
  await writeFile('../.planning-work/t40-upload-recovery.json', JSON.stringify({ mode: 'SIMULATION', task_id: 'T40', status: 'PASS',
    checked_at: new Date().toISOString(), checks, responses, restored_group_id: groupId, corrected_group_id: failedId,
    visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat_browser: 'NOT_RUN' }, null, 2));
} finally { await context.close(); await browser.close(); }
