import { chromium } from 'playwright';

const baseURL = 'http://127.0.0.1:5173';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

async function login() {
  await page.goto(`${baseURL}/login`);
  await page.fill('input[placeholder="账号"]', 'guardian-1');
  await page.fill('input[type="password"]', 'demo-pass-1');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL(`${baseURL}/home`, { timeout: 10000 });
}

async function recordObservation() {
  await page.goto(`${baseURL}/register/observations`);
  await page.fill('#obs-temp', '38.0');
  await page.fill('#obs-desc', '精神状态欠佳');
  await page.getByRole('button', { name: '保存本次记录', exact: true }).click();
  await page.waitForURL(`${baseURL}/records`, { timeout: 10000 });
  await page.waitForSelector('.record-list li');
  return await page.locator('.record-list li strong').first().innerText();
}

async function generateSummary() {
  await page.goto(`${baseURL}/summaries`);
  await page.getByLabel('开始日期', { exact: true }).fill('2026-09-27');
  await page.getByLabel('结束日期', { exact: true }).fill('2026-09-27');
  await page.getByRole('button', { name: '生成就医摘要', exact: true }).click();
  await page.waitForFunction(() => document.body.textContent.includes('已生成摘要'), null, { timeout: 10000 });
  return await page.locator('.form-result').innerText();
}

await login();
const recordKind = await recordObservation();
const summaryResult = await generateSummary();
console.log(JSON.stringify({ recordKind, summaryResult, pageErrors: errors }));
await browser.close();
