import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const origin = process.env.SJIA_H5_BASE_URL ?? 'http://127.0.0.1:15173';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const browserVersion = browser.version();
const runId = randomUUID();
const results = [];
const pageErrors = [];
const apiResponses = [];
let runtime = null;

function record(id, status, evidence, limitation = null) {
  results.push({ id, status, evidence, limitation });
}

try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN' });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('response', response => {
    if (response.url().startsWith(`${origin}/api/`)) apiResponses.push({ path: new URL(response.url()).pathname, status: response.status() });
  });
  await page.goto(`${origin}/login`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('form', { name: '登录账号' }).waitFor({ state: 'visible' });
  assert.equal(await page.locator('form input[name="username"]').count(), 1);
  assert.equal(await page.locator('form input[name="password"]').count(), 1);
  const bootstrapResponse = apiResponses.find(value => value.path === '/api/auth/bootstrap');
  assert.equal(bootstrapResponse?.status, 200);
  record('FORM_AND_LABELS', 'PASS', `实际登录页表单；同源 bootstrap HTTP ${bootstrapResponse.status}`);
  runtime = await page.evaluate(() => ({
    user_agent: navigator.userAgent,
    language: navigator.language,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    device_pixel_ratio: window.devicePixelRatio,
    screen: { width: window.screen.width, height: window.screen.height, orientation: window.screen.orientation?.type ?? null },
  }));

  const widths = [320, 360, 390, 430, 580];
  for (const width of widths) {
    await page.setViewportSize({ width, height: 844 });
    const metrics = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      document: document.documentElement.scrollWidth,
      form: Boolean(document.querySelector('[data-screen-id]')?.shadowRoot?.querySelector('form[aria-label="登录账号"]')),
      inputs: [...(document.querySelector('[data-screen-id]')?.shadowRoot?.querySelectorAll('form input') ?? [])].filter(input => input.getClientRects().length > 0).length,
      submitHeight: document.querySelector('[data-screen-id]')?.shadowRoot?.querySelector('form button[type="submit"]')?.getBoundingClientRect().height ?? 0,
    }));
    assert.ok(metrics.form && metrics.inputs === 2, `登录控件未完整显示：${width}px`);
    assert.ok(metrics.submitHeight >= 44, `主要触控目标小于 44px：${width}px ${JSON.stringify(metrics)}`);
    assert.ok(metrics.document <= metrics.viewport, `存在横向溢出：${width}px，${JSON.stringify(metrics)}`);
    record(`WIDTH_${width}`, 'PASS', JSON.stringify(metrics));
  }

  await page.setViewportSize({ width: 844, height: 390 });
  const landscape = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    form: Boolean(document.querySelector('[data-screen-id]')?.shadowRoot?.querySelector('form[aria-label="登录账号"]')),
  }));
  assert.ok(landscape.form && landscape.document <= landscape.viewport, JSON.stringify(landscape));
  record('LANDSCAPE', 'PASS', JSON.stringify(landscape), '桌面 Chromium 视口模拟；设备旋转未运行');

  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  const zoom = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    labels: [...(document.querySelector('[data-screen-id]')?.shadowRoot?.querySelectorAll('form label') ?? [])].every(label => label.getClientRects().length > 0),
    submit: document.querySelector('[data-screen-id]')?.shadowRoot?.querySelector('form button[type="submit"]')?.getClientRects().length === 1,
  }));
  assert.ok(zoom.labels && zoom.submit, JSON.stringify(zoom));
  record('TEXT_200_PERCENT', 'PASS', JSON.stringify(zoom), '通过根字号样式模拟放大；系统文字放大设置未运行');

  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
  await page.locator('form input[name="username"]').focus();
  const firstFocus = await page.evaluate(() => (document.querySelector('[data-screen-id]')?.shadowRoot?.activeElement instanceof HTMLElement
    ? document.querySelector('[data-screen-id]')?.shadowRoot?.activeElement?.getAttribute('name') : null));
  assert.equal(firstFocus, 'username');
  await page.keyboard.press('Tab');
  const secondFocus = await page.evaluate(() => (document.querySelector('[data-screen-id]')?.shadowRoot?.activeElement instanceof HTMLElement
    ? document.querySelector('[data-screen-id]')?.shadowRoot?.activeElement?.getAttribute('name') : null));
  assert.equal(secondFocus, 'password');
  record('KEYBOARD_ORDER', 'PASS', `${firstFocus} → ${secondFocus}`);

  await page.locator('form input[name="username"]').fill('模拟输入');
  await page.evaluate(() => {
    const input = document.querySelector('[data-screen-id]')?.shadowRoot?.querySelector('input[name="username"]');
    input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '' }));
    input.dispatchEvent(new CompositionEvent('compositionupdate', { bubbles: true, data: '模拟输入' }));
    input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '模拟输入' }));
  });
  assert.equal(await page.locator('form input[name="username"]').inputValue(), '模拟输入');
  record('IME_EVENT', 'PASS', '输入控件保留中文文本，composition 事件可触发', '系统中文输入法组合提交未运行');

  await page.getByRole('button', { name: '了解资料如何使用' }).click();
  assert.match(page.url(), /account\/data-use$/);
  await page.goBack();
  await page.getByRole('form', { name: '登录账号' }).waitFor({ state: 'visible' });
  record('BACK_NAVIGATION', 'PASS', '资料使用页内跳转后浏览器返回恢复登录表单');

  const target = `${origin}/summaries/00000000-0000-4000-8000-000000000058`;
  await page.goto(target, { waitUntil: 'domcontentloaded' });
  assert.match(page.url(), /summaries\/00000000-0000-4000-8000-000000000058$/);
  record('DEEP_LINK', 'PASS', page.url(), '模拟匿名浏览器保留目标地址；已登录权限链路未运行');

  await page.goto(`${origin}/login`, { waitUntil: 'domcontentloaded' });
  await page.context().setOffline(true);
  let offlineFailure = null;
  try {
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 5000 });
  } catch (error) {
    offlineFailure = error.message;
  }
  assert.ok(offlineFailure, '离线刷新应报告浏览器网络错误');
  record('OFFLINE_REFRESH', 'PASS', offlineFailure, '未触发正式提交；弱网服务端处理未运行');
  await page.context().setOffline(false);
  await page.goto(`${origin}/login`, { waitUntil: 'domcontentloaded' });

  const historyBefore = await page.evaluate(() => history.length);
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  assert.equal(await page.getByRole('alert').count(), 0);
  assert.ok((await page.evaluate(() => history.length)) >= historyBefore);
  record('REPEATED_TOUCH', 'PASS', '空表单的两次点击受浏览器必填校验拦截', '认证请求、重复登记和请求幂等未运行');

  const priorBootstrapCount = apiResponses.filter(value => value.path === '/api/auth/bootstrap').length;
  const resumeBootstrap = page.waitForResponse(response => response.url().startsWith(`${origin}/api/auth/bootstrap`), { timeout: 5000 });
  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
  });
  assert.equal((await resumeBootstrap).status(), 200);
  assert.deepEqual(pageErrors, []);
  const refreshCount = apiResponses.filter(value => value.path === '/api/auth/bootstrap').length - priorBootstrapCount;
  assert.ok(refreshCount > 0, '恢复事件没有触发同源权限刷新');
  record('BACKGROUND_RESUME_EVENT', 'PASS', `恢复事件触发 ${refreshCount} 次同源 bootstrap`, '系统后台挂起和恢复未运行');

  const browserCapabilities = await page.evaluate(() => ({
    mediaDevices: Boolean(navigator.mediaDevices?.getUserMedia),
    speechRecognition: Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
    userAgent: navigator.userAgent,
  }));
  record('VOICE_CAPABILITY', 'NOT_RUN', JSON.stringify(browserCapabilities), '未申请麦克风权限，未录音或调用识别服务');
  record('IME_ENTER_DURING_COMPOSITION', 'NOT_RUN', '仅发送并检查了 compositionstart/update/end 事件', '未在系统输入法组合期间实际按 Enter');
  record('AUTHENTICATED_DEEP_LINK_AND_PERMISSION', 'NOT_RUN', '仅检查匿名地址保留', '未登录模拟家庭账号，也未读取患儿权限');
  record('FAMILY_SAVE_REFRESH_CORRECTION_REVOKE', 'NOT_RUN', '当前程序未登录家庭账号', '未执行治疗保存、刷新、更正或撤权后的读取');
  record('RESULT_UNKNOWN_AND_IDEMPOTENCY', 'NOT_RUN', '当前程序未提交正式业务请求', '未测量原请求恢复、重复提交或重复记录数量');
  record('DEVICE_KEYBOARD_AND_BACKGROUND', 'NOT_RUN', '当前程序只检查桌面浏览器事件', '真实系统键盘、应用后台挂起与恢复未运行');
  record('IOS_ANDROID_BROWSER_MATRIX', 'NOT_RUN', '当前程序使用桌面 Chrome', 'iOS Safari 与 Android Chrome 未运行');
  record('WECHAT_BROWSER', 'NOT_RUN', '当前浏览器为桌面 Chrome', 'iOS/Android 微信内置浏览器未运行');
  record('VISUAL', 'NOT_RUN', '没有执行图像或视觉检查');

  assert.deepEqual(pageErrors, []);
} finally {
  await browser.close();
}

const report = {
  run_id: runId,
  mode: 'SIMULATION_ONLY',
  task_ids: ['T30', 'T58'],
  acceptance_ids: ['V30', 'V58', 'AC-UX-01', 'AC-H5-01', 'AC-H5-02', 'AC-H5-03', 'AC-H5-04', 'AC-H5-05', 'AC-H5-06'],
  run_at: new Date().toISOString(),
  origin,
  browser: browserVersion,
  environment: {
    execution: 'desktop-browser-simulation',
    system: process.platform,
    architecture: process.arch,
    device_model: null,
    operating_system_version: null,
    browser_version: browserVersion,
    wechat_version: null,
    network: 'local-simulation',
    simulated_account_id: null,
    simulated_patient_id: null,
    authorization_ref: null,
    runtime,
  },
  results,
  pageErrors,
};
const outputPath = resolve(process.env.SJIA_DEVICE_RESULT_PATH
  ?? '.planning-work/parallel/window2/evidence/device-check-' + report.run_at.replaceAll(':', '-') + '.json');
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' });
console.log(JSON.stringify({ ...report, result_path: outputPath }, null, 2));
