import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { openRealBrowser } from './lib/real-browser.mjs';

const { values } = parseArgs({ options: { report: { type: 'string' }, credentials: { type: 'string' },
  origin: { type: 'string', default: 'http://127.0.0.1:5178' } } });
assert.ok(values.report && values.credentials);
const output = resolve(values.report);
assert.equal(existsSync(output), false);
const credentials = JSON.parse(await readFile(resolve(values.credentials), 'utf8'));
const report = { result: 'IN_PROGRESS', checks: [], layouts: [], page_errors: [],
  started_at: new Date().toISOString(), test_sha256: createHash('sha256').update(await readFile(import.meta.filename)).digest('hex'),
  visual_validation: 'NOT_RUN' };
await writeFile(output, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const native = await openRealBrowser({ port: 9240, headless: true });
const page = await native.context.newPage();
page.setDefaultTimeout(60000);
page.on('pageerror', error => report.page_errors.push(error.message));
async function save() { await writeFile(output, JSON.stringify(report, null, 2)); }
async function check(name) { report.checks.push({ name, result: 'PASS' }); await save(); }
async function layout() {
  return page.locator('.button, .text-action, .rich-theme-choice, .feature-grid>button, .alert-link').evaluateAll(elements =>
    elements.filter(element => element.getClientRects().length && element.getBoundingClientRect().width > 0).map(element => {
      const style = getComputedStyle(element), box = element.getBoundingClientRect();
      const range = document.createRange(); range.selectNodeContents(element);
      const children = range.getBoundingClientRect();
      return { label: element.textContent.trim(), display: style.display, justify_content: style.justifyContent,
        align_items: style.alignItems, text_align: style.textAlign, height: box.height, width: box.width,
        center_delta: Math.abs(children.x + children.width / 2 - box.x - box.width / 2) };
    }));
}
try {
  await page.goto(values.origin + '/login');
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  const login = page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/login');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  assert.equal((await login).status(), 200);
  await page.waitForURL('**/home');
  await page.goto(values.origin + '/account');
  await page.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  report.initial_controls = await layout(); await save();
  const group = page.getByRole('group', { name: '外观设置', exact: true });
  await group.waitFor();
  assert.equal(await group.getByRole('radio').count(), 3);
  const systemTheme = await page.evaluate(() => matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  for (const [label, preference, theme] of [['浅色', 'light', 'light'], ['深色', 'dark', 'dark'], ['跟随系统', 'system', systemTheme]]) {
    await group.getByRole('radio', { name: label, exact: true }).check();
    await page.locator(`.device[data-theme="${theme}"]`).waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('sjia.ui.theme')), preference);
    await page.reload();
    await group.getByRole('radio', { name: label, exact: true }).waitFor();
    assert.equal(await group.getByRole('radio', { name: label, exact: true }).isChecked(), true);
  }
  await check('NATIVE_APPEARANCE_OPTIONS_PERSIST_AFTER_RELOAD');
  await group.getByRole('radio', { name: '浅色', exact: true }).check();
  await group.getByRole('radio', { name: '浅色', exact: true }).focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await group.getByRole('radio', { name: '深色', exact: true }).isChecked(), true);
  await page.locator('.device[data-theme="dark"]').waitFor();
  await check('APPEARANCE_RADIOS_SUPPORT_NATIVE_KEYBOARD_SELECTION');
  const paths = [['/home', 'UI2-01'], ['/account', 'UI2-15'], ['/login', 'UI2-17'],
    ['/register/observations', 'UI2-02'], ['/register/reports', 'UI2-03'], ['/register/therapies', 'UI2-04'],
    ['/records', 'UI2-05'], ['/drafts', 'UI2-21'], ['/summaries', 'UI2-13'],
    ['/account/reminders', 'UI2-32'], ['/alerts/current', 'UI2-12'], ['/associations', 'UI2-18']];
  for (const theme of ['light', 'dark']) {
    await page.goto(values.origin + '/account');
    await group.getByRole('radio', { name: theme === 'light' ? '浅色' : '深色', exact: true }).check();
    for (const width of [320, 390, 430, 580]) {
      await page.setViewportSize({ width, height: 900 });
      for (const [path, screenId] of paths) {
        await page.goto(values.origin + path);
        await page.locator(`[data-screen-id="${screenId}"]`).waitFor();
        const loading = page.locator('.rich-read-state.loading');
        while (await loading.count()) await loading.first().waitFor({ state: 'hidden' });
        if (path === '/records') await page.getByRole('button', { name: '就医摘要 准备本次沟通资料', exact: true }).waitFor();
        if (path === '/home') await page.locator('.home-alert .alert-link').waitFor();
        const buttons = await layout();
        for (const item of buttons) {
          assert.equal(item.justify_content, 'center', `${path}:${item.label}`);
          assert.equal(item.align_items, 'center', `${path}:${item.label}`);
          assert.ok(item.center_delta <= 2, `${path}:${item.label}:${item.center_delta}`);
          assert.ok(item.height >= 44, `${path}:${item.label}`);
        }
        const overflow = await page.locator('.page-content').evaluate(element => element.scrollWidth - element.clientWidth);
        assert.ok(overflow <= 1, `${path}:${width}`);
        report.layouts.push({ path, theme, width, buttons });
      }
    }
  }
  await check('SHARED_ACTIONS_CENTERED_ACROSS_PAGES_WIDTHS_AND_THEMES');
  assert.deepEqual(report.page_errors, []);
  report.result = 'PASS';
} finally {
  if (report.result === 'IN_PROGRESS') report.result = 'FAIL';
  report.finished_at = new Date().toISOString();
  report.alerts = await page.getByRole('alert').allTextContents();
  await save(); await native.close();
  console.log(JSON.stringify({ result: report.result, checks: report.checks.length, layouts: report.layouts.length }));
}
