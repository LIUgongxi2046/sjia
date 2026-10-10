import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { promisify } from 'node:util';
import { resolve } from 'node:path';
import { chromium, expect as baseExpect } from 'playwright/test';
import { createServer } from 'vite';

process.loadEnvFile('../.simulation-work/environment.env');
const expect = baseExpect.configure({ timeout: 15000 });
const execute = promisify(execFile);
const fixture = process.argv.includes('--saved-routes') ? JSON.parse(await readFile('../.planning-work/t54-route-fixture.json', 'utf8'))
  : JSON.parse((await execute('../backend/.venv/bin/python', ['../.planning-work/prepare_t54_browser.py'],
    { env: { ...process.env, TMPDIR: process.cwd() + '/../.planning-work/tmp' }, timeout: 120000 })).stdout);
assert.equal(fixture.mode, 'SIMULATION'); assert.equal(fixture.transaction_read_only, true);
const compiler = await createServer({ server: { middlewareMode: true, hmr: false, ws: false } });
const expectedStyles = (await compiler.ssrLoadModule('/@fs/' + resolve('../ui-delivery/v2/src/styles.css') + '?inline')).default;
await compiler.close();
const previous = process.argv.includes('--resume') ? JSON.parse(await readFile('../.planning-work/t54-simulation-browser.json', 'utf8')) : null;
if (previous) { assert.equal(previous.mode, 'SIMULATION'); assert.equal(previous.scope, 'ALL_39_ROUTES_AND_SHARED_INTERACTIONS'); }
const recheck = (process.env.SJIA_RECHECK_SCREENS ?? '').split(',').filter(Boolean);
const retainedRoutes = previous?.inspected_routes.filter(id => !recheck.includes(id)) ?? [];
const origin = 'http://127.0.0.1:5183', checks = previous?.checks.filter(name => !recheck.some(id => name.startsWith(id + ' '))) ?? [],
  geometry = previous?.geometry.filter(item => retainedRoutes.includes(item.screen_id)) ?? [],
  responses = previous?.responses ?? [], errors = [], inspectedRoutes = retainedRoutes;
let completed = false;
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const family = await browser.newContext({ viewport: { width: 390, height: 844 } });
const support = await browser.newContext({ viewport: { width: 390, height: 844 } });
const anonymous = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await family.newPage(), supportPage = await support.newPage(), anonymousPage = await anonymous.newPage();
for (const target of [page, supportPage, anonymousPage]) {
  target.setDefaultTimeout(15000); target.setDefaultNavigationTimeout(30000);
  target.on('pageerror', error => errors.push(error.message));
  target.on('dialog', dialog => dialog.accept());
  target.on('response', response => { const url = new URL(response.url());
    if (url.pathname.startsWith('/api/')) responses.push({ path: url.pathname, status: response.status(), method: response.request().method() }); });
}
function check(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(context, method, path, data, csrf) {
  const response = await context.request.fetch(origin + path, { method, ...(data ? { data } : {}),
    ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${path}: ${response.status()} ${await response.text()}`);
  assert.ok(response.headers()['cache-control'].includes('no-store'));
  return response.json();
}
async function saveReport(status) {
  const sourceFiles = ['src/components/UI2Frame.tsx', 'src/components/UI2ReadState.tsx',
    'src/features/support/SupportApp.tsx', 'src/features/registration/ReportsPage.tsx',
    'src/features/registration/TherapiesPage.tsx', 'src/components/MedicalTimeInput.tsx',
    'src/state/useFormDraft.tsx', '../backend/api/views_family.py', '../backend/relations/application_requests.py',
    '../backend/summaries/submission.py', 'scripts/check-simulation-ui2-browser.mjs'];
  const sourceChecksums = Object.fromEntries(await Promise.all(sourceFiles.map(async path =>
    [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile('../.planning-work/t54-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status,
    task_id: 'T54', checked_at: new Date().toISOString(), checks, geometry, responses, errors,
    scope: process.env.SJIA_CHECK_SCREEN ? 'SELECTED_SCREEN_GEOMETRY' : 'ALL_39_ROUTES_AND_SHARED_INTERACTIONS',
    required_route_count: fixture.routes.length, inspected_routes: inspectedRoutes,
    required_state_count: fixture.routes.reduce((count, route) => count + route.required_states.length, 0),
    route_states: fixture.routes.map(route => ({ screen_id: route.screen_id, path: route.path, required_states: route.required_states,
      visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat: 'NOT_RUN' })),
    source_sha256: sourceChecksums,
    styles_sha256: createHash('sha256').update(await readFile('../ui-delivery/v2/src/styles.css')).digest('hex'),
    visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN', wechat: 'NOT_RUN' }, null, 2));
}
try {
  await page.goto(origin + '/login');
  await expect(page.getByLabel('账号', { exact: true })).toBeEnabled();
  await page.getByLabel('账号', { exact: true }).fill('simulation-family');
  await page.getByLabel('密码', { exact: true }).fill(process.env.SJIA_SIMULATION_PASSWORD);
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await expect(page.getByRole('heading', { name: '陪伴孩子，健康成长', exact: true })).toBeVisible();
  const session = await api(family, 'GET', '/api/session');
  assert.equal(session.audience, 'family');
  check('家庭登录页面实际认证并取得本人会话');
  const bootstrap = await api(support, 'GET', '/api/auth/bootstrap');
  await api(support, 'POST', '/api/v2/support/session/login', { username: 'simulation-support', password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
  for (const route of fixture.routes.filter(route => (!process.env.SJIA_CHECK_SCREEN || route.screen_id === process.env.SJIA_CHECK_SCREEN)
    && !inspectedRoutes.includes(route.screen_id))) {
    const target = route.audience === 'support' ? supportPage : page;
    await target.goto(origin + route.path);
    const frame = target.locator(`[data-screen-id="${route.screen_id}"]`);
    await expect(frame).toBeVisible();
    if (['UI2-32', 'UI2-35'].includes(route.screen_id)) {
      await expect(frame.locator('.status-panel.loading')).toHaveCount(0, { timeout: 60000 });
    } else await target.waitForLoadState('networkidle');
    await expect(frame.locator('h1')).toHaveCount(1);
    assert.equal(await frame.getByRole('navigation', { name: '家庭主导航', exact: true }).count(), route.layout === 'primary' ? 1 : 0);
    assert.equal(await target.evaluate(() => localStorage.length + sessionStorage.length), 0);
    assert.equal(await frame.evaluate(node => node.shadowRoot.querySelector('style').textContent), expectedStyles);
    for (const width of [320, 360, 390, 430, 768]) {
      await target.setViewportSize({ width, height: 844 });
      for (const scale of [1, 2]) {
        if (scale === 2) await frame.evaluate(host => {
          const root = host.shadowRoot;
          window.ui2TextSizes = [...root.querySelectorAll('.phone-scroll *')]
            .filter(node => node instanceof HTMLElement && !['SVG', 'PATH', 'STYLE'].includes(node.tagName))
            .map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
          for (const item of window.ui2TextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
        });
        const measured = await frame.evaluate(host => { const root = host.shadowRoot, scroll = root.querySelector('.phone-scroll'), device = root.querySelector('.device');
          const bounds = scroll.getBoundingClientRect();
          return { width: scroll.clientWidth, content_width: scroll.scrollWidth, device_width: device.clientWidth, viewport_width: innerWidth,
            scroll_left: scroll.scrollLeft, bounds: { left: bounds.left, right: bounds.right },
            overflow: [...root.querySelectorAll('.phone-scroll *')].filter(node => node.getClientRects().length)
              .filter(node => node.getBoundingClientRect().right > bounds.right + 1 || node.scrollWidth > node.clientWidth + 1)
              .map(node => ({ tag: node.tagName, class: node.className, text: node.textContent.slice(0, 70),
                right: node.getBoundingClientRect().right, min_width: getComputedStyle(node).minWidth, flex: getComputedStyle(node).flex })) }; });
        geometry.push({ screen_id: route.screen_id, width, text_scale: scale, ...measured });
        if (scale === 2) await target.evaluate(() => { for (const item of window.ui2TextSizes) item.node.style.fontSize = item.original; delete window.ui2TextSizes; });
        if (measured.content_width > measured.width + 1) await saveReport('FAIL');
        assert.ok(measured.content_width <= measured.width + 1, JSON.stringify(geometry.at(-1)));
        assert.ok(measured.device_width <= width, JSON.stringify(geometry.at(-1)));
      }
    }
    await target.setViewportSize({ width: 390, height: 844 });
    if (route.layout !== 'primary' || route.screen_id !== 'UI2-01') {
      await frame.getByRole('button', { name: '页面说明', exact: true }).click();
      const dialog = frame.getByRole('dialog', { name: '页面说明', exact: true }); await expect(dialog).toBeVisible();
      await target.keyboard.press('Shift+Tab');
      assert.ok(await dialog.evaluate(node => node.contains(node.getRootNode().activeElement)));
      await target.keyboard.press('Tab'); await target.keyboard.press('Escape'); await expect(dialog).toHaveCount(0);
    }
    check(`${route.screen_id}正式入口、当前身份、原稿样式、五种宽度与200%文字及说明弹层`);
    inspectedRoutes.push(route.screen_id);
    await saveReport('IN_PROGRESS');
  }
  if (!process.env.SJIA_CHECK_SCREEN) {
  await page.goto(origin + '/home');
  const nav = page.getByRole('navigation', { name: '家庭主导航', exact: true });
  for (const [label, path] of [['记录', '/records'], ['我的', '/account'], ['首页', '/home']]) {
    await nav.getByRole('button', { name: label, exact: true }).click(); await page.waitForURL(origin + path);
    await expect(nav.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
  }
  check('三项主导航连接准确页面并保留当前选中状态');
  await page.goto(origin + '/associations/new');
  const evidence = 'SIMULATION家庭页面申请依据 ' + randomUUID();
  await expect(page.getByLabel('申请人说明', { exact: true })).toBeEnabled();
  await page.getByLabel('申请人说明', { exact: true }).fill(evidence);
  await page.getByLabel('申请权限', { exact: true }).selectOption('read_write');
  const submitted = page.waitForResponse(response => response.request().method() === 'POST'
    && new URL(response.url()).pathname === '/api/relation-applications');
  await page.getByRole('button', { name: '提交关联申请', exact: true }).click();
  const submittedResponse = await submitted; assert.equal(submittedResponse.status(), 201);
  const receipt = await submittedResponse.json();
  await expect(page.getByRole('heading', { name: '申请已经保存', exact: true })).toBeVisible();
  const application = await api(family, 'GET', '/api/relation-applications/' + receipt.resource_id);
  assert.equal(application.evidence_ref, evidence); assert.equal(application.requested_access, 'read_write'); assert.equal(application.status, 'pending');
  await page.reload(); await page.getByText('本人申请保存请求', { exact: true }).click();
  await expect(page.getByRole('button').filter({ hasText: '关联申请' }).last()).toBeVisible();
  const directory = await api(family, 'GET', '/api/requests');
  const original = directory.requests.find(item => item.request_id === receipt.request_id); assert.ok(original);
  assert.equal(original.state, 'committed'); assert.equal(original.application_input.evidence_ref, evidence);
  check('家庭关联申请实际保存准确正文、权限、回执和刷新后的原请求');
  await anonymousPage.goto(origin + '/register/observations');
  await expect(anonymousPage.getByLabel('体温原值', { exact: true })).toBeDisabled();
  await expect(anonymousPage.getByRole('button', { name: '确认并保存', exact: true })).toBeDisabled();
  await page.goto(origin + '/account');
  await page.getByRole('button', { name: '退出登录', exact: true }).click(); await page.waitForURL(origin + '/home');
  const denied = await family.request.get(origin + '/api/session'); assert.equal(denied.status(), 401);
  await expect(page.getByRole('heading', { name: '当前患儿', exact: true })).toHaveCount(0);
  assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
  check('匿名登记禁止提交、实际退出会话及当前资料清理');
  }
  assert.deepEqual(errors, []); completed = true; await saveReport('PASS');
} finally { if (!completed) await saveReport('FAIL'); await family.close(); await support.close(); await anonymous.close(); await browser.close(); }
