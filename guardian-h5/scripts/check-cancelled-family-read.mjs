import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from '../node_modules/playwright/test.mjs';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const target = '/api/patients/dff4286d-b457-43b1-b53d-64bc40b224a2/home';
const requests = [];
page.on('request', request => {
  if (new URL(request.url()).pathname === target) requests.push(request.method());
});
try {
  await page.goto('http://127.0.0.1:5183/login');
  const result = await page.evaluate(async path => {
    const { fetchJson } = await import('/src/api/client.ts');
    const controller = new AbortController();
    const pending = fetchJson(path, { signal: controller.signal });
    controller.abort();
    const [settled] = await Promise.allSettled([pending]);
    return { status: settled.status,
      error_name: settled.status === 'rejected' ? settled.reason.name : null };
  }, target);
  assert.deepEqual(result, { status: 'rejected', error_name: 'AbortError' });
  assert.deepEqual(requests, []);
  const output = '../.planning-work/coordination/root/cancelled-family-read.json';
  const report = { mode: 'SIMULATION_ONLY', status: 'PASS', target,
    error_name: result.error_name, request_count: requests.length, saved_and_read: true,
    visual: 'NOT_RUN', actual_device: 'NOT_RUN', wechat: 'NOT_RUN' };
  await writeFile(output, JSON.stringify(report, null, 2));
  assert.deepEqual(JSON.parse(await readFile(output, 'utf8')), report);
  process.stdout.write(`${JSON.stringify(report)}\n`);
} finally {
  await context.close();
  await browser.close();
}
