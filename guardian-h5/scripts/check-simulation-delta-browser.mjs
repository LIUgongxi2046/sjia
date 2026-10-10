import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

const expect = baseExpect.configure({ timeout: 30000 });

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.simulation-work/t46-fixture.json', 'utf8'));
assert.equal(fixture.mode, 'SIMULATION');
const origin = 'http://127.0.0.1:5183', checks = [], requests = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(30000);
page.on('pageerror', error => { throw error; });
page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/')) requests.push({ path: new URL(request.url()).pathname, method: request.method() }); });
function checked(name) { checks.push(name); process.stdout.write(`PASS ${name}\n`); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + '/api' + path, { method, ...(data ? { data } : {}), ...(csrf ? { headers: { 'X-CSRFToken': csrf } } : {}) });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`); return response.json();
}

try {
  const bootstrap = await api('GET', '/auth/bootstrap');
  await api('POST', '/auth/login', { username: 'simulation-family', password: process.env.SJIA_SIMULATION_PASSWORD }, bootstrap.csrf_token);
  await page.goto(origin + '/archives/changes');
  await expect(page.getByRole('heading', { name: '这次新增了什么', exact: true })).toBeVisible();
  await expect(page.getByText('本次资料更新', { exact: true })).toBeVisible();
  await expect(page.getByText('每项变化，都能返回原始文书。', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '尚无可生成的内容', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: '查看就医摘要 按时间范围整理原始资料' })).toBeVisible();
  checked('UI2-30保留原稿内容并读取实际批次目录');
  await page.getByRole('button', { name: '选择当前批次', exact: true }).click();
  const current = page.getByLabel('当前批次', { exact: true }), baseline = page.getByLabel('比较基准', { exact: true });
  await expect(current.locator(`option[value="${fixture.current_batch_id}"]`)).toBeAttached();
  await expect(current.locator(`option[value="${fixture.baseline_batch_id}"]`)).toBeAttached();
  await expect(current).toBeEnabled();
  await current.selectOption(fixture.current_batch_id);
  await expect(baseline).toBeEnabled();
  await page.getByRole('button', { name: '生成并保存新增内容摘要', exact: true }).click();
  await expect(page.getByText('尚未指定比较基准，仅整理本批资料和待处理事项。', { exact: true })).toBeVisible();
  const first = new URL(page.url()).searchParams.get('delta');
  assert.ok(first);
  assert.ok(!page.url().includes('SIMULATION') && !page.url().includes('950'));
  checked('无基准通过实际意图、任务和保存回执整理本批内容');
  await page.reload();
  await expect(page.getByText('尚未指定比较基准，仅整理本批资料和待处理事项。', { exact: true })).toBeVisible();
  assert.equal(new URL(page.url()).searchParams.get('delta'), first);
  checked('刷新按保存摘要身份重新读取实际结果');
  await page.getByRole('button', { name: '待核对', exact: true }).click();
  await expect(page.getByRole('heading', { name: '事实时间尚未明确', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '事实尚未完成专业核对', exact: true })).toBeVisible();
  checked('未知时间、文书归组和专业核对状态准确显示');
  await page.getByRole('button', { name: '选择其他资料生成摘要', exact: true }).click();
  await page.getByRole('button', { name: '选择当前批次', exact: true }).click();
  await expect(baseline).toBeEnabled();
  await baseline.selectOption(fixture.baseline_batch_id);
  await page.getByRole('button', { name: '生成并保存新增内容摘要', exact: true }).click();
  await page.getByRole('button', { name: '新增事实', exact: true }).click();
  await expect(page.getByRole('heading', { name: '独立检查原值比较', exact: true })).toBeVisible();
  const resultId = new URL(page.url()).searchParams.get('delta');
  assert.ok(resultId && resultId !== first);
  await expect(page.getByText('仅展示两次检查原值，不生成临床等级。', { exact: true })).toBeVisible();
  checked('明确基准后实际生成符合资格的前后原值');
  const requestId = new URL(page.url()).searchParams.get('request');
  assert.ok(requestId);
  await page.goto(`${origin}/archives/changes?request=${requestId}`);
  await expect(page.getByRole('heading', { name: '独立检查原值比较', exact: true })).toBeVisible();
  assert.equal(new URL(page.url()).searchParams.get('delta'), resultId);
  checked('独立打开原请求能够恢复准确正文与已保存结果');
  const sourceLink = page.getByRole('link', { name: '查看原文依据 1', exact: true }).first();
  await sourceLink.click();
  await expect(page.getByRole('heading', { name: '原始资料', exact: true })).toBeVisible();
  await page.getByText('原文依据与准确版本', { exact: true }).click();
  const sourceVersion = new URL(page.url()).searchParams.get('document_version');
  assert.ok(sourceVersion);
  await expect(page.getByText(`引用文书版本`, { exact: false }).filter({ hasText: sourceVersion })).toBeVisible();
  const content = page.waitForResponse(response => /\/api\/files\/[^/]+\/content$/.test(new URL(response.url()).pathname) && response.status() === 200);
  await page.getByRole('button', { name: '查看原件', exact: true }).click();
  const bytes = await content;
  assert.ok(Number((await bytes.allHeaders())['content-length']) > 0);
  await expect(page.getByRole('button', { name: '查看原始资料' })).toBeEnabled();
  await page.getByRole('button', { name: '关闭原件与页序', exact: true }).click();
  await expect(page.getByRole('button', { name: '查看原件', exact: true })).toBeFocused();
  checked('逐条来源读取准确历史文书及实际原件字节');
  await page.getByRole('button', { name: '返回上级页面', exact: true }).click();
  await expect(page.getByRole('heading', { name: '独立检查原值比较', exact: true })).toBeVisible();
  assert.equal(new URL(page.url()).searchParams.get('delta'), resultId);
  checked('返回摘要保留准确结果身份');
  for (const width of [320, 360, 390, 430, 580]) {
    await page.setViewportSize({ width, height: 844 });
    const geometry = await page.evaluate(() => {
      const host = document.querySelector('[data-screen-id="UI2-30"]');
      const container = host?.shadowRoot?.querySelector('.phone-scroll');
      if (!container) throw new Error('缺少新增内容摘要页面容器');
      return { width: container.clientWidth, scroll: container.scrollWidth, storage: localStorage.length + sessionStorage.length };
    });
    assert.ok(geometry.scroll <= geometry.width + 1, `${width}: ${JSON.stringify(geometry)}`); assert.equal(geometry.storage, 0);
  }
  checked('五种手机宽度无横向溢出且私密资料没有进入持久存储');
  await page.getByRole('button', { name: '新增事实', exact: true }).focus();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: '重复资料', exact: true })).toBeFocused();
  checked('分类与表单支持键盘访问');
  const count = requests.length;
  await page.waitForTimeout(1200); assert.equal(requests.length, count);
  checked('保存结果稳定后没有持续请求循环');
  await writeFile('../.planning-work/t46-simulation-browser.json', JSON.stringify({ mode: 'SIMULATION', status: 'PASS', task_id: 'T46',
    run_id: crypto.randomUUID(), checked_at: new Date().toISOString(), checks, check_count: checks.length,
    visual_comparison: 'NOT_RUN', real_device: 'NOT_RUN', wechat: 'NOT_RUN' }, null, 2));
  process.stdout.write(`PASS ${checks.length} 项实际摘要浏览器检查\n`);
} finally { await browser.close(); }
