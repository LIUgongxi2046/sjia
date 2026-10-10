import { chromium } from 'playwright';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto('http://127.0.0.1:5173/home');
await page.waitForSelector('.app-header');
const badgeCount = await page.locator('.prototype-badge').count();
const headerText = await page.locator('.app-header').innerText();
console.log(JSON.stringify({ badgeCount, headerText }));
await browser.close();
