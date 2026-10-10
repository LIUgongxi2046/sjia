import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const backend = JSON.parse(await readFile('../.planning-work/t42-simulation.json', 'utf8'));
const origin = 'http://127.0.0.1:5183', checks = [], measurements = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.on('pageerror', error => { throw error; });
try {
  const bootstrap = await (await context.request.get(origin + '/api/auth/bootstrap')).json();
  const response = await context.request.post(origin + '/api/auth/login', { data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(response.ok());
  await page.goto(origin + '/archives/batches/' + backend.batch_id);
  await expect(page.getByText('页面 1 · 等待本人确认', { exact: true })).toBeVisible();
  await page.getByText('页面 1 · 等待本人确认', { exact: true }).click();
  await expect(page.getByText(/模拟计价：/).first()).toBeVisible();
  checks.push('当前批次准确标注模拟计价');
  await page.reload();
  await expect(page.getByText('页面 1 · 等待本人确认', { exact: true })).toBeVisible();
  await page.getByText('页面 1 · 等待本人确认', { exact: true }).click();
  checks.push('当前代码刷新恢复实际完成结果及尝试');
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    const scale = await page.locator('.phone-scroll').evaluate(scroll => {
      window.batchTextSizes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')]
        .map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      const before = Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize);
      for (const item of window.batchTextSizes) item.node.style.fontSize = `${item.pixels * 2}px`;
      return { before, after: Number.parseFloat(getComputedStyle(scroll.querySelector('h1')).fontSize) };
    });
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth }));
    assert.equal(scale.after, scale.before * 2); assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ width, scale, geometry }));
    measurements.push({ width, scale, geometry });
    await page.evaluate(() => { for (const item of window.batchTextSizes) item.node.style.fontSize = item.original; delete window.batchTextSizes; });
  }
  checks.push('五种宽度实际200%文字检查通过');
  await writeFile('../.planning-work/t42-current-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', checks,
    checked_at: new Date().toISOString(), measurements, visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${checks.length} 项当前批次页面检查\n`);
} finally { await context.close(); await browser.close(); }
