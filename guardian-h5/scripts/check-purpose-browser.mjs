import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const origin = 'http://127.0.0.1:4173';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const checks = [];
const pageErrors = [];
const responses = [];
const requests = [];
const loadedAssets = [];
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => requests.push({ url: request.url(), method: request.method() }));
  page.on('response', response => {
    if (response.url().includes('/api/')) responses.push({ url: response.url(), status: response.status(),
      cache_control: response.headers()['cache-control'] });
    const url = new URL(response.url());
    if (url.origin === origin && url.pathname.startsWith('/assets/') && ['.js', '.css'].includes(path.extname(url.pathname))) {
      loadedAssets.push((async () => {
        const data = await response.body();
        const file = `dist${url.pathname}`;
        const digest = createHash('sha256').update(data).digest('hex');
        assert.equal(digest, createHash('sha256').update(await readFile(file)).digest('hex'));
        return { path: file, sha256: digest, size_bytes: data.length };
      })());
    }
  });
  for (const width of [320, 360, 390, 430, 580]) {
    await page.setViewportSize({ width, height: 844 });
    const denial = page.waitForResponse(response => response.url() === `${origin}/api/v2/support/session` && response.status() === 401);
    await page.goto(`${origin}/support/data-use`);
    await denial;
    await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
    assert.equal(await page.getByRole('heading', { name: '用途与副本', level: 1 }).count(), 1);
    assert.equal(await page.getByRole('navigation').count(), 0);
    assert.equal(await page.getByRole('button', { name: '提交复核记录', exact: true }).count(), 0);
    assert.equal(await page.locator('.pair').count(), 0);
    const css = await page.locator('[data-screen-id="UI2-38"]').evaluate(node => {
      if (!node.shadowRoot) throw new Error('当前页面未建立样式容器。');
      const content = node.shadowRoot.querySelector('.page-content');
      if (!content) throw new Error('当前页面未建立内容区域。');
      return { ink: getComputedStyle(node).getPropertyValue('--ink'), canvas: getComputedStyle(node).getPropertyValue('--canvas'),
        sheets: node.shadowRoot.querySelectorAll('style').length, content_class: content.className };
    });
    assert.equal(css.ink.toLowerCase(), '#243b55');
    assert.equal(css.canvas.toLowerCase(), '#f1f5fa');
    assert.equal(css.sheets, 1);
    checks.push({ name: '真实匿名拒绝与受限内容清除', viewport_width: width, result: 'PASS', css });
  }
  const explanation = page.getByRole('button', { name: '页面说明', exact: true });
  await explanation.click();
  const sheet = page.getByRole('dialog', { name: '页面说明', exact: true });
  await sheet.waitFor();
  await page.keyboard.press('Shift+Tab');
  assert.equal(await sheet.getByRole('button', { name: '继续查看', exact: true }).evaluate(node => node.getRootNode().activeElement === node), true);
  await page.keyboard.press('Tab');
  assert.equal(await sheet.getByRole('button', { name: '关闭说明', exact: true }).evaluate(node => node.getRootNode().activeElement === node), true);
  await page.keyboard.press('Escape');
  await sheet.waitFor({ state: 'detached' });
  assert.equal(await sheet.count(), 0);
  await page.waitForFunction(node => node.getRootNode().activeElement === node, await explanation.elementHandle());
  assert.equal(await explanation.evaluate(node => node.getRootNode().activeElement === node), true);
  checks.push({ name: '页面说明关闭及实际键盘焦点维护', result: 'PASS' });
  await page.getByRole('button', { name: '核验访问权限', exact: true }).click();
  await page.waitForURL(`${origin}/support/login`);
  await page.getByRole('textbox', { name: '账号', exact: true }).waitFor();
  assert.equal(await page.getByLabel('密码', { exact: true }).getAttribute('type'), 'password');
  assert.equal(await page.getByLabel('密码', { exact: true }).getAttribute('autocomplete'), 'current-password');
  const loginsBefore = requests.filter(request => request.url.endsWith('/api/v2/support/session/login')).length;
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  assert.equal(await page.locator('form').evaluate(form => form.checkValidity()), false);
  assert.equal(requests.filter(request => request.url.endsWith('/api/v2/support/session/login')).length, loginsBefore);
  checks.push({ name: '真实登录入口与空输入拒绝', result: 'PASS', credentials_submitted: false });
  await page.reload();
  await page.getByRole('textbox', { name: '账号', exact: true }).waitFor();
  assert.equal(await page.getByRole('textbox', { name: '账号', exact: true }).inputValue(), '');
  assert.equal(await page.getByLabel('密码', { exact: true }).inputValue(), '');
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  checks.push({ name: '刷新登录页面保持无凭证保存', result: 'PASS' });
  const runtime = JSON.parse(await readFile('../.planning-work/t40-runtime.json', 'utf8'));
  assert.equal(runtime.status, 'PASS');
  const scoped = new URL('/support/data-use', origin);
  scoped.searchParams.set('file_id', runtime.original_probe.file_id);
  scoped.searchParams.set('purpose', 'external_recognition');
  const scopedDenial = page.waitForResponse(response => response.url() === `${origin}/api/v2/support/session` && response.status() === 401);
  await page.goto(scoped.href);
  await scopedDenial;
  await page.getByRole('heading', { name: '当前访问关系需要核验', exact: true }).waitFor();
  assert.equal(await page.getByRole('button', { name: '确认生成副本', exact: true }).count(), 0);
  assert.equal(await page.getByLabel('用途授权', { exact: true }).count(), 0);
  assert.equal(requests.some(request => request.url.includes('/purposes/external_recognition/options')), false);
  assert.equal(await page.locator('.pair').count(), 0);
  checks.push({ name: '真实原件标识不取得创建或用途范围读取权限', result: 'PASS', file_id: runtime.original_probe.file_id });
  assert.deepEqual(pageErrors, []);
  assert.equal(requests.some(request => new URL(request.url).origin !== origin), false);
  assert.equal(requests.some(request => request.url.includes('/api/v1/relations')), false);
  assert.equal(responses.filter(response => response.url.endsWith('/api/v2/support/session')).every(response => response.status === 401 && response.cache_control === 'no-store, private'), true);
  const tokenHash = createHash('sha256').update(await readFile('../ui-delivery/v2/tokens.json')).digest('hex');
  const styleHash = createHash('sha256').update(await readFile('../ui-delivery/v2/src/styles.css')).digest('hex');
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'DOM_PERMISSION_AND_NAVIGATION',
    checks, api_responses: responses, page_errors: pageErrors, loaded_assets: await Promise.all(loadedAssets), token_sha256: tokenHash, style_sha256: styleHash,
    provider_called: false, clinical_data_created: false, positive_business_validation: 'NOT_RUN',
    visual_validation: 'NOT_RUN', mobile_keyboard_validation: 'NOT_RUN', device_validation: 'NOT_RUN', wechat_validation: 'NOT_RUN' };
  await writeFile('../.planning-work/t40-browser.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report, null, 2) + '\n');
} finally { await browser.close(); }
