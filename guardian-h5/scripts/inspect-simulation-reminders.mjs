import { chromium } from 'playwright';

process.loadEnvFile('../.simulation-work/environment.env');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
const origin = 'http://127.0.0.1:5183';
const bootstrap = await (await context.request.get(origin + '/api/auth/bootstrap')).json();
const login = await context.request.post(origin + '/api/auth/login', { headers: { 'X-CSRFToken': bootstrap.csrf_token },
  data: { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD } });
if (!login.ok()) throw new Error('模拟账号登录失败');
const page = await context.newPage();
page.on('response', async response => {
  const path = new URL(response.url()).pathname;
  if (path.startsWith('/api/')) {
    const body = await response.json();
    process.stdout.write(JSON.stringify({ path, status: response.status(), code: body.code,
      fields: Object.keys(body), ...(response.status() >= 400 ? { message: body.message } : {}) }) + '\n');
  }
});
page.on('pageerror', error => process.stdout.write(error.message + '\n'));
await page.goto(origin + '/account/reminders');
await new Promise(resolve => setTimeout(resolve, 15000));
process.stdout.write(JSON.stringify({ alerts: await page.getByRole('alert').allTextContents(),
  states: await page.getByRole('status').allTextContents(), disabled: await page.getByLabel('提醒对象').isDisabled() }) + '\n');
await context.close();
await browser.close();
