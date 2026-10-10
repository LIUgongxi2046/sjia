import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect } from 'playwright/test';

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t45-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const origin = 'http://127.0.0.1:5183', checks = [], requests = [], run = crypto.randomUUID();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(30000);
page.on('pageerror', error => { throw error; });
page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push({ url: request.url(), method: request.method() }); });
function checked(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + '/api' + path, { method, ...(data ? { data } : {}), ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`);
  return response.json();
}

try {
  const bootstrap = await api('GET', '/auth/bootstrap');
  const account = await api('POST', '/auth/login', { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
  await page.goto(origin + '/archives');
  await page.getByRole('heading', { name: '长期档案', exact: true }).waitFor();
  await expect(page.getByText('符合条件 4 份文书；本次读取 4 份。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '添加一批资料 原始资料直接整理' })).toBeVisible();
  await expect(page.getByText('没有新增资料，不代表当前健康状况正常。', { exact: true })).toBeVisible();
  checked('UI2长期档案读取实际文书并保留设计内容');
  assert.ok(!requests.some(value => /\/files\/[^/]+\/content/.test(value.url)));
  const input = page.getByRole('searchbox', { name: '查找文书', exact: true });
  await input.fill('医院2'); await input.press('Enter');
  await expect(page.getByText('符合条件 2 份文书；本次读取 2 份。', { exact: true })).toBeVisible();
  assert.ok(!page.url().includes('医院2') && !requests.some(value => new URL(value.url).search.includes('医院2')));
  checked('查找条件实际提交正文且无原件预加载');
  await page.getByRole('button', { name: '筛选', exact: true }).click();
  await page.getByLabel('来源医院', { exact: true }).fill('医院2');
  await page.getByLabel('来源医院', { exact: true }).press('Enter');
  await expect(page.getByText('符合条件 2 份文书；本次读取 2 份。', { exact: true })).toBeVisible();
  const first = page.getByRole('link', { name: '定位原文依据', exact: true }).first();
  await first.click();
  await page.getByRole('heading', { name: '原始资料', exact: true }).waitFor();
  await expect(page.getByText('已保存原始文书', { exact: true })).toBeVisible();
  const documentId = new URL(page.url()).searchParams.get('document');
  assert.ok(fixture.document_ids.includes(documentId));
  const originalResponse = page.waitForResponse(response => /\/api\/files\/[^/]+\/content$/.test(new URL(response.url()).pathname) && response.status() === 200);
  await page.getByRole('button', { name: '查看原件', exact: true }).click();
  const original = await originalResponse;
  const headers = await original.allHeaders();
  assert.ok(Number(headers['content-length']) > 0);
  assert.equal(headers['content-type'], 'image/png');
  await expect(page.getByRole('dialog', { name: '原件与页序', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '查看原始资料' })).toBeEnabled();
  await page.getByRole('button', { name: '关闭原件与页序', exact: true }).click();
  await expect(page.getByRole('button', { name: '查看原件', exact: true })).toBeFocused();
  checked('原文依据定位准确文书并读取实际原件字节');
  await page.getByRole('button', { name: '返回上级页面', exact: true }).click();
  await page.getByRole('heading', { name: '长期档案', exact: true }).waitFor();
  await expect(page.getByRole('searchbox', { name: '查找文书', exact: true })).toHaveValue('医院2');
  await expect(page.getByLabel('来源医院', { exact: true })).toHaveValue('医院2');
  await expect(page.getByText('符合条件 2 份文书；本次读取 2 份。', { exact: true })).toBeVisible();
  checked('返回目录保留本人筛选及结果范围');
  await page.getByRole('button', { name: '清除筛选', exact: true }).click();
  await expect(page.getByText('符合条件 4 份文书；本次读取 4 份。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '待归组', exact: true }).click();
  await expect(page.getByText('符合条件 1 份文书；本次读取 1 份。', { exact: true })).toBeVisible();
  await expect(page.getByText('来源医院未知 · 文书版本 1', { exact: true })).toBeVisible();
  checked('未知时间医院及就诊资料仍可读取');
  await page.getByRole('button', { name: '全部', exact: true }).click();
  await input.fill('目录'); await input.press('Enter');
  await expect(page.getByText('符合条件 4 份文书；本次读取 4 份。', { exact: true })).toBeVisible();
  await expect(page.getByLabel('已确认原文片段')).toHaveCount(4);
  checked('确认原文实际查询且片段显示对应当前文书');
  await page.getByRole('button', { name: '清除筛选', exact: true }).click();
  for (const width of [320, 360, 390, 430, 580]) {
    await page.setViewportSize({ width, height: 844 });
    const geometry = await page.evaluate(() => {
      const host = document.querySelector('[data-screen-id="UI2-25"]');
      const container = host?.shadowRoot?.querySelector('.phone-scroll');
      if (!container) throw new Error('缺少长期档案页面容器');
      return { width: container.clientWidth, scroll: container.scrollWidth, storage: localStorage.length + sessionStorage.length };
    });
    assert.ok(geometry.scroll <= geometry.width + 1, `${width}: ${JSON.stringify(geometry)}`); assert.equal(geometry.storage, 0);
  }
  checked('五种手机宽度无横向溢出且私密资料不进入持久存储');
  await page.setViewportSize({ width: 390, height: 844 });
  await input.focus(); await input.press('Tab');
  await expect(page.getByRole('button', { name: '全部', exact: true })).toBeFocused();
  checked('查找输入及分类按钮支持键盘访问');
  const count = requests.length;
  await page.waitForTimeout(1200); assert.equal(requests.length, count);
  checked('稳定目录没有持续查询循环');
  await page.reload();
  await expect(page.getByText('符合条件 4 份文书；本次读取 4 份。', { exact: true })).toBeVisible();
  checked('刷新能够重新读取数据库当前目录');
  const directory = await api('POST', `/v2/patients/${fixture.patient_id}/archives/search`, { limit: 1 }, account.csrf_token);
  assert.equal(directory.matched_count, 4); assert.ok(directory.next_cursor);
  await writeFile('../.planning-work/t45-simulation-browser.json', JSON.stringify({ status: 'PASS', mode: 'SIMULATION', task_id: 'T45', run_id: run, checks, check_count: checks.length, checked_at: new Date().toISOString(), visual_comparison: 'NOT_RUN', real_device: 'NOT_RUN', wechat: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${checks.length} 项实际目录浏览器检查\n`);
} finally {
  await browser.close();
}
