import { chromium } from 'playwright';

const baseURL = 'http://127.0.0.1:5173';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });

await page.goto(`${baseURL}/login`);
await page.waitForSelector('input[placeholder="账号"]');
await page.fill('input[placeholder="账号"]', 'guardian-1');
await page.fill('input[type="password"]', 'demo-pass-1');
await page.getByRole('button', { name: '登录', exact: true }).click();
await page.waitForURL(`${baseURL}/home`, { timeout: 10000 });
await page.waitForSelector('.warning-hero h2');
await page.waitForFunction(() => {
  const text = document.querySelector('.warning-hero p')?.textContent ?? '';
  return text.includes('暂无记录') || text.includes('暂无法评估') || text.includes('需要关注');
}, null, { timeout: 10000 });
const heading = await page.locator('.warning-hero h2').innerText();
const body = await page.locator('.warning-hero p').innerText();
const hasPatient = (await page.getByText('已关联患儿', { exact: true }).count()) > 0;
const hasLogin = (await page.getByText('已登录', { exact: true }).count()) > 0;
console.log(JSON.stringify({ heading, body, hasPatient, hasLogin, pageErrors: errors }));
await browser.close();
