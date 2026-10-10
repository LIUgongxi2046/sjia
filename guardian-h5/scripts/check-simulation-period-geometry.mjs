import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });
process.loadEnvFile('../.simulation-work/environment.env');
const origin = 'http://127.0.0.1:5183', checks = [], measurements = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
try {
  const bootstrapResponse = await context.request.get(origin + '/api/auth/bootstrap');
  assert.ok(bootstrapResponse.ok()); const bootstrap = await bootstrapResponse.json();
  const login = await context.request.post(origin + '/api/auth/login', { data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD },
    headers: { 'X-CSRFToken': bootstrap.csrf_token } });
  assert.ok(login.ok());
  await page.goto(origin + '/records/period');
  await expect(page.getByLabel('开始日期', { exact: true })).toBeEnabled();
  for (const width of [320, 360, 390, 430, 768]) {
    await page.setViewportSize({ width, height: 844 });
    const scale = await page.locator('.phone-scroll').evaluate(scroll => {
      const nodes = [...scroll.querySelectorAll('h1,h2,h3,p,span,strong,small,label,input,select,textarea,summary,button')];
      const sizes = nodes.map(node => ({ node, original: node.style.fontSize, pixels: Number.parseFloat(getComputedStyle(node).fontSize) }));
      window.periodTextSizes = sizes;
      const heading = scroll.querySelector('h1'), before = Number.parseFloat(getComputedStyle(heading).fontSize);
      for (const item of sizes) item.node.style.fontSize = `${item.pixels * 2}px`;
      return { before, after: Number.parseFloat(getComputedStyle(heading).fontSize) };
    });
    assert.equal(scale.after, scale.before * 2);
    const geometry = await page.locator('.phone-scroll').evaluate(scroll => ({ width: scroll.clientWidth, content_width: scroll.scrollWidth,
      scroll_left: scroll.scrollLeft,
      overflowing: [...scroll.querySelectorAll('*')].filter(node => node.getClientRects().length && node.clientWidth > 0 && node.scrollWidth > node.clientWidth + 1)
        .map(node => ({ tag: node.tagName, class_name: node.className, width: node.clientWidth, content_width: node.scrollWidth })).slice(0, 18) }));
    assert.ok(geometry.content_width <= geometry.width + 1, JSON.stringify({ viewport: width, scale, geometry }));
    measurements.push({ viewport: width, scale, geometry });
    await page.evaluate(() => { for (const item of window.periodTextSizes) item.node.style.fontSize = item.original; delete window.periodTextSizes; });
  }
  checks.push('五种宽度在ShadowRoot内检查实际内容范围');
  checks.push('实际文字字号达到200%后检查内容宽度');
  await writeFile('../.planning-work/t47-simulation-geometry.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T47',
    checked_at: new Date().toISOString(), checks, measurements, visual_comparison: 'NOT_RUN', actual_devices: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${checks.length} 项实际内容与文字字号检查\n`);
} finally { await context.close(); await browser.close(); }
