import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, statfsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';

const { values } = parseArgs({ options: Object.fromEntries(
  ['origin', 'credentials', 'source', 'build-dir', 'report'].map(name => [name, { type: 'string' }])) });
for (const name of ['origin', 'credentials', 'source', 'build-dir', 'report']) assert.ok(values[name], name);
const origin = new URL(values.origin);
assert.equal(origin.hostname, '127.0.0.1');
assert.equal(origin.protocol, 'http:');
assert.equal(origin.href, origin.origin + '/');
assert.equal(existsSync(values.report), false);
const root = resolve(import.meta.dirname, '../..');
const space = statfsSync(root);
assert.ok(space.bavail * space.bsize >= 2 * 1024 ** 3 + 32 * 1024 ** 2);
const source = JSON.parse(await readFile(values.source, 'utf8'));
const credentials = JSON.parse(await readFile(values.credentials, 'utf8'));
assert.equal(source.result, 'PASS');
assert.equal(source.source_kind, 'USER_APPROVED_ORIGINAL');
const sourceHash = createHash('sha256').update(await readFile(import.meta.filename)).digest('hex');
const index = await readFile(resolve(values['build-dir'], 'index.html'));
const report = { result: 'IN_PROGRESS', started_at: new Date().toISOString(), origin: origin.origin,
  script_sha256: sourceHash, index_sha256: createHash('sha256').update(index).digest('hex'),
  source_manifest_sha256: source.source_manifest_sha256, pages: [], page_errors: [],
  minimum_input_height: 52, visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN',
  new_model_calls: 0, new_clinical_writes: 0 };
await writeFile(values.report, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disk-cache-size=8388608'] });
const routes = ['/home', '/records', '/account', '/register/observations', '/register/reports',
  '/register/reports/confirm', '/register/therapies', '/archives', '/archives/batches', '/summaries',
  '/associations', '/account/data-use'];
const fields = '.field input:not([type=checkbox]):not([type=radio]),.field select,.field textarea,.search-field,.rich-theme-preference select';
async function save() { await writeFile(values.report, JSON.stringify(report, null, 2)); }

async function inspect(page, route, width, scheme) {
  const host = page.locator('[data-screen-id]');
  await page.locator('[data-screen-id] .phone-scroll').waitFor({ state: 'visible' });
  assert.equal(await host.count(), 1);
  assert.equal(await host.getAttribute('data-theme'), scheme);
  assert.equal(await host.getAttribute('data-theme-preference'), 'system');
  // 展开包含输入控件的真实区域，尺寸检查只采用当前可见控件。
  for (const detail of await page.locator('details').all()) {
    const required = await detail.evaluate((element, selector) => !element.open && !!element.querySelector(selector), fields);
    if (required) await detail.locator(':scope > summary').click();
  }
  const geometry = await host.evaluate((element, selector) => {
    const root = element.shadowRoot;
    if (!root) throw new Error('页面缺少样式容器。');
    const scroll = root.querySelector('.phone-scroll');
    if (!scroll) throw new Error('页面缺少滚动容器。');
    const controls = [...root.querySelectorAll(selector)].filter(node =>
      node.checkVisibility({ checkVisibilityCSS: true, contentVisibilityAuto: true }) && node.getClientRects().length)
      .map(node => ({ tag: node.tagName, classes: node.className, height: node.getBoundingClientRect().height,
        width: node.getBoundingClientRect().width, disabled: node.matches(':disabled') }));
    const image = root.querySelector('.home-child-view>img');
    const companion = image ? { height: image.getBoundingClientRect().height,
      object_fit: getComputedStyle(image).objectFit, complete: image.complete, natural_width: image.naturalWidth } : null;
    return { screen_id: element.getAttribute('data-screen-id'), controls, companion,
      scroll_width: scroll.scrollWidth, client_width: scroll.clientWidth,
      pins: [...root.querySelectorAll('.symptom-pin')].map(node => ({ width: node.getBoundingClientRect().width,
        height: node.getBoundingClientRect().height })),
      navigation_buttons: root.querySelectorAll('.bottom-nav button').length };
  }, fields);
  const entry = { route, width, scheme, ...geometry, keyboard_checked: false };
  report.pages.push(entry);
  const violations = geometry.controls.filter(control => control.height < report.minimum_input_height);
  if (violations.length) {
    report.result = 'FAIL';
    await save();
    assert.equal(violations.length, 0, JSON.stringify({ route, width, scheme, violations }));
  }
  assert.ok(geometry.scroll_width <= geometry.client_width + 1, route);
  assert.equal(geometry.navigation_buttons, ['/home', '/records', '/account'].includes(route) ? 3 : 0);
  if (route === '/home') {
    assert.ok(geometry.companion?.complete && geometry.companion.natural_width > 0);
    assert.equal(geometry.companion.object_fit, 'contain');
    assert.ok(geometry.companion.height >= 232 && geometry.companion.height <= 300);
    assert.equal(geometry.pins.length, 3);
    assert.ok(geometry.pins.every(pin => pin.height >= 44 && pin.width >= 44));
  }
  const help = page.getByRole('button', { name: '页面说明', exact: true });
  if (await help.count()) {
    await help.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: '页面说明', exact: true });
    await dialog.waitFor({ state: 'visible' });
    assert.ok(await dialog.evaluate(element => element.contains(element.getRootNode().activeElement)));
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert.ok(await help.evaluate(element => element.getRootNode().activeElement === element));
    entry.keyboard_checked = true;
  }
  await save();
}

try {
  for (const width of [320, 390, 580]) {
    for (const scheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width, height: 844 }, colorScheme: scheme, reducedMotion: 'reduce' });
      const page = await context.newPage();
      page.setDefaultTimeout(30000);
      page.on('pageerror', error => report.page_errors.push(error.message));
      try {
        const response = await context.request.get(origin.origin + '/home');
        assert.equal(response.status(), 200);
        assert.deepEqual(await response.body(), index);
        await page.goto(origin.origin + '/login', { waitUntil: 'networkidle' });
        await inspect(page, '/login', width, scheme);
        await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
        await page.locator('input[type="password"]').fill(credentials.password);
        await page.getByRole('button', { name: '登录', exact: true }).click();
        await page.waitForURL('**/home');
        const session = await context.request.get(origin.origin + '/api/session');
        assert.equal(session.status(), 200);
        const identity = await session.json();
        assert.equal(identity.principal_id, source.actor_id);
        const selected = await context.request.post(origin.origin + '/api/patient-selection', {
          data: { patient_id: source.patient_id }, headers: { Origin: origin.origin, 'X-CSRFToken': identity.csrf_token } });
        assert.equal(selected.status(), 200);
        for (const route of routes) {
          await page.goto(origin.origin + route, { waitUntil: 'networkidle', timeout: 60000 });
          await inspect(page, route, width, scheme);
        }
      } finally {
        await context.close();
      }
    }
  }
  assert.equal(report.pages.length, 78);
  assert.equal(report.page_errors.length, 0);
  assert.equal(createHash('sha256').update(await readFile(import.meta.filename)).digest('hex'), sourceHash);
  report.result = 'PASS';
  report.finished_at = new Date().toISOString();
  report.browser_version = browser.version();
  console.log(JSON.stringify({ result: report.result, pages: report.pages.length,
    input_controls: report.pages.reduce((count, page) => count + page.controls.length, 0),
    keyboard_checks: report.pages.filter(page => page.keyboard_checked).length,
    new_model_calls: 0, visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' }));
} finally {
  await save();
  await browser.close();
}
