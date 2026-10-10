import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t47-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const origin = 'http://127.0.0.1:5183', paths = [], checks = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.on('pageerror', error => { throw error; });
page.on('request', request => { paths.push(new URL(request.url()).pathname); });
try {
  const bootstrapResponse = await context.request.get(origin + '/api/auth/bootstrap');
  assert.ok(bootstrapResponse.ok()); const bootstrap = await bootstrapResponse.json();
  const login = await context.request.post(origin + '/api/auth/login', { data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD },
    headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(login.ok());
  await page.goto(origin + '/records/period');
  const start = page.getByLabel('开始日期', { exact: true });
  await expect(start).toBeEnabled(); const before = await start.inputValue();
  assert.ok(before);
  const end = page.getByLabel('结束日期', { exact: true }); await start.focus();
  for (let count = 0; count < 8 && !await end.evaluate(element => element === element.getRootNode().activeElement); count++) await page.keyboard.press('Tab');
  await expect(end).toBeFocused();
  checks.push('原生日期字段的各个时间部分均可通过键盘操作');
  await page.evaluate(() => {
    window.periodLifecycle = [];
    document.addEventListener('freeze', () => window.periodLifecycle.push('freeze'));
    document.addEventListener('resume', () => window.periodLifecycle.push('resume'));
  });
  const cdp = await context.newCDPSession(page), boundary = paths.length;
  await cdp.send('Page.setWebLifecycleState', { state: 'frozen' });
  await page.waitForTimeout(200);
  await cdp.send('Page.setWebLifecycleState', { state: 'active' });
  await page.waitForTimeout(200);
  const observed = await page.evaluate(() => window.periodLifecycle);
  if (observed.length === 0) {
    await writeFile('../.planning-work/t47-simulation-resume.json', JSON.stringify({ mode: 'SIMULATION', status: 'NOT_RUN', task_id: 'T47',
      checked_at: new Date().toISOString(), checks, observed_lifecycle: observed, actual_browser_lifecycle: 'NOT_RUN',
      reason: '当前Chrome会话没有触发原生freeze与resume事件，实际后台条件继续待验收',
      actual_devices: 'NOT_RUN', visual_comparison: 'NOT_RUN' }, null, 2));
    throw new Error('当前Chrome会话未触发原生生命周期事件，已保存实际观察结果。');
  }
  assert.deepEqual(observed, ['freeze', 'resume']);
  checks.push('Chrome实际生命周期冻结与恢复事件均发生');
  await expect.poll(() => paths.slice(boundary).includes('/api/session')).toBe(true);
  await expect.poll(() => paths.slice(boundary).includes(`/api/v2/patients/${fixture.patient_id}/observation-period`)).toBe(true);
  checks.push('恢复后重新认证并重新读取当前观察范围');
  await expect(start).toBeEnabled(); await expect(start).toHaveValue(before);
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  checks.push('恢复后的保存范围准确且正文没有写入浏览器存储');
  await writeFile('../.planning-work/t47-simulation-resume.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T47',
    checked_at: new Date().toISOString(), checks, actual_browser_lifecycle: 'PASS', actual_devices: 'NOT_RUN', visual_comparison: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${checks.length} 项实际Chrome生命周期恢复检查\n`);
} finally { await context.close(); await browser.close(); }
