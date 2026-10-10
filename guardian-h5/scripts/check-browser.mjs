import assert from 'node:assert/strict';
import { mkdir, readFile, readdir, stat, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { chromium } from 'playwright';

const baseURL = 'http://127.0.0.1:4173';
const startedAt = new Date().toISOString();
const checks = [];
const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];
const remoteRequests = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, colorScheme: 'light' });
context.setDefaultTimeout(10000);
context.setDefaultNavigationTimeout(10000);
const page = await context.newPage();
page.on('console', message => {
  if (message.type() === 'error') consoleErrors.push({ message: message.text(), location: message.location() });
});
page.on('pageerror', error => pageErrors.push(error.message));
page.on('requestfailed', request => failedRequests.push(request.url()));
page.on('request', request => {
  if (!request.url().startsWith(baseURL)) remoteRequests.push(request.url());
});

const routes = [
  ['/home', '首页', 'S03'],
  ['/login', '登录', 'S01'],
  ['/associations', '患儿关联', 'S02'],
  ['/associations/new', '关联申请', 'S02'],
  ['/associations/unavailable', '关联详情', 'S02'],
  ['/register/observations', '体温与症状', 'S04'],
  ['/register/reports', '添加检验报告', 'S05'],
  ['/register/therapies', '治疗资料', 'S06'],
  ['/records', '记录', 'S07'],
  ['/records/unavailable', '记录详情', 'S08'],
  ['/records/analytes/unavailable', '历次检验', 'S09'],
  ['/records/unavailable/correct', '更正记录', 'S10'],
  ['/records/unavailable/history', '更正历史', 'S10'],
  ['/alerts', '预警', 'S11'],
  ['/alerts/unavailable', '预警详情', 'S11'],
  ['/summaries', '就医摘要', 'S12'],
  ['/summaries/unavailable', '摘要详情', 'S12'],
  ['/account', '我的', 'S13'],
  ['/account/data-use', '资料使用说明', 'S13'],
  ['/drafts', '我的草稿', 'S14'],
  ['/access-denied', '暂时无法访问', 'S15'],
  ['/service-unavailable', '服务暂不可用', 'S15'],
  ['/unavailable-page', '页面不存在', 'S15'],
  ['/prototype', '原型页面目录', 'S00'],
];

for (const [path, title, screenId] of routes) {
  const response = await page.goto(`${baseURL}${path}`);
  assert.equal(response.status(), 200, path);
  await page.locator(`[data-screen-id="${screenId}"]`).waitFor();
  assert.equal(await page.locator('h1').innerText(), title, path);
  assert.equal(await page.title(), `${title} · sJIA 守护者联盟`, path);
  assert.equal(await page.locator('main').count(), 1);
  assert.equal(await page.locator('a[href="#"]').count(), 0);
  assert.deepEqual(await page.getByRole('navigation', { name: '主要导航' }).getByRole('link').allTextContents(), ['首页', '记录', '我的']);
  assert.equal(await page.locator('a[href="/prototype"]').count(), 0);
  assert.equal(await page.locator('.patient-context a').count(), 0);
  const needsPatient = ['/records', '/summaries', '/drafts', '/register', '/alerts'].some(prefix => path === prefix || path.startsWith(`${prefix}/`));
  assert.equal(await page.locator('.patient-context').count(), needsPatient ? 1 : 0, path);
  const cacheControl = response.headers()['cache-control'];
  assert.ok(cacheControl.includes('no-store'), path);
  checks.push({ name: '直接访问路由与页面状态', path, screenId, result: 'PASS' });
}

await page.goto(baseURL);
await page.waitForURL(`${baseURL}/home`);
checks.push({ name: '根入口进入首页', result: 'PASS' });

for (const [label, path] of [['记录', '/records'], ['我的', '/account'], ['首页', '/home']]) {
  const link = page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: label, exact: true });
  await link.click();
  await page.waitForURL(`${baseURL}${path}`);
  await page.getByRole('heading', { level: 1, name: label, exact: true }).waitFor();
  assert.equal(await link.getAttribute('aria-current'), 'page');
  assert.equal(await page.getByRole('heading', { level: 1 }).innerText(), label);
}
checks.push({ name: '三项导航点击与当前页面标记', result: 'PASS' });

await page.getByRole('link', { name: '拍照添加资料', exact: true }).click();
await page.waitForURL(`${baseURL}/register/reports`);
assert.equal(await page.getByRole('button', { name: '拍照添加', exact: true }).isDisabled(), true);
assert.equal(await page.getByRole('button', { name: '从相册选择', exact: true }).isDisabled(), true);
await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
assert.equal(await page.getByRole('button', { name: '确认并保存', exact: true }).isDisabled(), true);
assert.ok(await page.locator('input, textarea').count() > 0);
assert.equal(await page.locator('input:enabled, textarea:enabled').count(), 0);
await page.getByRole('link', { name: '返回', exact: true }).click();
await page.waitForURL(`${baseURL}/home`);
await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '记录', exact: true }).click();
await page.waitForURL(`${baseURL}/records`);
await page.getByRole('link', { name: '就医摘要', exact: true }).click();
await page.waitForURL(`${baseURL}/summaries`);
await page.goBack();
await page.waitForURL(`${baseURL}/records`);
await page.goForward();
await page.waitForURL(`${baseURL}/summaries`);
await page.reload();
await page.getByRole('heading', { name: '就医摘要', exact: true }).waitFor();
checks.push({ name: '入口点击、页面返回、浏览器返回前进与刷新', result: 'PASS' });

await page.goto(`${baseURL}/home`);
await page.getByRole('link', { name: '尚未选择患儿', exact: true }).click();
await page.waitForURL(`${baseURL}/associations`);
await page.getByRole('link', { name: '关联申请 查看申请入口状态' }).click();
await page.waitForURL(`${baseURL}/associations/new`);
assert.equal(await page.getByRole('button', { name: '暂时无法申请' }).isDisabled(), true);
checks.push({ name: '患儿选择入口与未开放关联申请', result: 'PASS' });

await page.goto(`${baseURL}/home`);
await page.locator('.home-sections').waitFor();
assert.equal(await page.locator('.home-sections > :first-child').getAttribute('class'), 'warning-hero');
assert.equal(await page.locator('.patient-context').count(), 0);
assert.equal(await page.getByText('服务尚未启用，当前无法评估风险。', { exact: true }).count(), 1);
assert.equal(await page.locator('.warning-hero').getByRole('heading').innerText(), '暂未生成预警');
const heroPosition = await page.locator('.warning-hero').boundingBox();
const photoPosition = await page.locator('.photo-entry').boundingBox();
assert.ok(heroPosition.y < photoPosition.y);
assert.ok(photoPosition.y + photoPosition.height < 760, '预警与拍照入口应在首屏可见');
assert.equal(await page.locator('main .photo-entry').count(), 1);
assert.equal(await page.getByText('暂无记录', { exact: true }).count(), 0);
assert.equal(await page.locator('input, textarea').count(), 0);
checks.push({ name: '首页预警结果优先、拍照入口及未评估状态', result: 'PASS' });

await page.goto(`${baseURL}/account/data-use`);
const explanation = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: '资料来源与核对状态' }) });
assert.equal(await explanation.getAttribute('open'), null);
await explanation.locator('summary').click();
assert.notEqual(await explanation.getAttribute('open'), null);
assert.equal(await explanation.locator('dt').allTextContents().then(items => items.join('、')), '资料来源、核对状态、需要补充');
await explanation.locator('summary').focus();
await page.keyboard.press('Space');
assert.equal(await explanation.getAttribute('open'), null);
checks.push({ name: '资料说明展开、键盘收起及字段区分', result: 'PASS' });

for (const [label, path] of [
  ['记录体温与症状', '/register/observations'],
  ['拍照添加资料', '/register/reports'],
  ['补充治疗资料', '/register/therapies'],
  ['登录查看', '/login'],
  ['查看预警状态', '/alerts'],
]) {
  await page.goto(`${baseURL}/home`);
  await page.getByRole('link', { name: label, exact: true }).click();
  await page.waitForURL(`${baseURL}${path}`);
  await page.goBack();
  await page.waitForURL(`${baseURL}/home`);
  await page.locator('[data-screen-id="S03"]').waitFor();
  assert.equal(await page.locator('[data-screen-id="S03"]').count(), 1);
}
checks.push({ name: '首页五个任务入口及浏览器返回', result: 'PASS' });

const homeScroll = await page.evaluate(() => window.scrollY);
await page.getByRole('navigation', { name: '主要导航' }).getByRole('link', { name: '记录', exact: true }).click();
await page.waitForURL(`${baseURL}/records`);
await page.goBack();
await page.waitForURL(`${baseURL}/home`);
await page.waitForFunction(position => Math.abs(window.scrollY - position) < 2, homeScroll);
checks.push({ name: '首页返回后恢复浏览位置', result: 'PASS' });

for (const [path, expectedLinks] of [
  ['/home', ['/alerts', '/associations', '/login', '/register/reports', '/register/observations', '/register/therapies']],
  ['/records', ['/summaries', '/drafts', '/login']],
  ['/account', ['/login', '/associations', '/account/data-use']],
  ['/alerts', ['/home']],
]) {
  await page.goto(`${baseURL}${path}`);
  await page.locator('h1').waitFor();
  assert.deepEqual(await page.locator('main a').evaluateAll(links => links.map(link => link.getAttribute('href'))), expectedLinks, path);
}
await page.goto(`${baseURL}/account`);
assert.equal(await page.getByText('尚未登录', { exact: true }).count(), 1);
assert.equal(await page.getByRole('button', { name: '退出登录', exact: true }).count(), 0);
await page.getByRole('link', { name: '登录账号', exact: true }).click();
await page.waitForURL(`${baseURL}/login`);
await page.getByRole('link', { name: '返回', exact: true }).click();
await page.waitForURL(`${baseURL}/account`);
await page.getByRole('link', { name: '患儿管理', exact: true }).click();
await page.waitForURL(`${baseURL}/associations`);
await page.getByRole('link', { name: '返回', exact: true }).click();
await page.getByRole('link', { name: '资料使用说明', exact: true }).click();
await page.waitForURL(`${baseURL}/account/data-use`);
checks.push({ name: '主要功能入口归属与我的页面单一账号区域', result: 'PASS' });

for (const title of ['就医摘要', '我的草稿']) {
  await page.goto(`${baseURL}/records`);
  await page.getByRole('link', { name: title, exact: true }).click();
  await page.getByRole('heading', { level: 1, name: title, exact: true }).waitFor();
  assert.equal(await page.locator('.tab-link[aria-current]').innerText(), '记录');
  await page.getByRole('link', { name: '返回', exact: true }).click();
  await page.waitForURL(`${baseURL}/records`);
}
for (const path of ['/records/unavailable/correct', '/records/unavailable/history']) {
  await page.goto(`${baseURL}${path}`);
  await page.getByRole('link', { name: '返回', exact: true }).click();
  await page.waitForURL(`${baseURL}/records/unavailable`);
}
await page.goto(`${baseURL}/alerts`);
assert.equal(await page.locator('.tab-link[aria-current]').innerText(), '首页');
await page.getByRole('link', { name: '返回', exact: true }).click();
await page.waitForURL(`${baseURL}/home`);
checks.push({ name: '摘要草稿集中入口、详情返回与所属导航', result: 'PASS' });

for (const path of ['/register/observations', '/register/reports', '/register/therapies']) {
  await page.goto(`${baseURL}${path}`);
  if (await page.locator('.guided-flow').count()) await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
  assert.equal(await page.locator('.form-actions button').count(), 1);
  assert.equal(await page.locator('main a[href="/drafts"]').count(), 0);
  assert.equal(await page.locator('summary').filter({ hasText: '资料来源与核对状态' }).count(), 0);
}
checks.push({ name: '登记页面单一保存操作与帮助说明归属', result: 'PASS' });

await page.goto(`${baseURL}/register/reports`);
assert.equal(await page.locator('input[capture="environment"]').isDisabled(), true);
assert.equal(await page.locator('input[type="file"][multiple]').isDisabled(), true);
assert.equal(await page.locator('.guide-panel:visible').count(), 1);
await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
assert.equal(await page.evaluate(() => document.activeElement.textContent), '确认保存');
assert.equal(await page.getByRole('heading', { name: '尚未取得识别结果', exact: true }).isVisible(), true);
await page.locator('summary').filter({ hasText: '逐项核对检验内容' }).click();
await page.getByRole('button', { name: '添加检验项目', exact: true }).click();
assert.equal(await page.locator('.report-item').count(), 2);
await page.getByRole('button', { name: '添加检验项目', exact: true }).click();
assert.equal(await page.locator('.report-item').count(), 3);
await page.getByRole('button', { name: '移除项目 2', exact: true }).click();
assert.equal(await page.locator('.report-item').count(), 2);
assert.equal(await page.evaluate(() => document.activeElement.textContent.trim()), '添加检验项目');
await page.getByRole('button', { name: '移除项目 2', exact: true }).click();
assert.equal(await page.locator('.report-item').count(), 1);
assert.equal(await page.getByRole('button', { name: /移除项目/ }).count(), 0);
checks.push({ name: '空白报告项目增加、移除与焦点恢复', result: 'PASS' });

await page.locator('summary').filter({ hasText: '查看需要确认的报告信息' }).click();
await page.getByLabel('采样时间精度', { exact: true }).selectOption('仅知道日期');
assert.equal(await page.getByLabel('采样时间', { exact: true }).getAttribute('type'), 'date');
assert.equal(await page.getByLabel('采样时间', { exact: true }).inputValue(), '');
await page.getByLabel('采样时间精度', { exact: true }).selectOption('知道日期与时间');
assert.equal(await page.getByLabel('采样时间', { exact: true }).getAttribute('type'), 'datetime-local');
assert.equal(await page.getByLabel('采样时间', { exact: true }).inputValue(), '');
await page.getByLabel('采样时间精度', { exact: true }).selectOption('无法确定');
assert.equal(await page.getByLabel('采样时间', { exact: true }).count(), 0);
await page.locator('summary').filter({ hasText: '检测方法与参考区间' }).click();
assert.equal(await page.getByLabel('检测方法', { exact: true }).isVisible(), true);
assert.equal(await page.getByLabel('参考区间原文', { exact: true }).isDisabled(), true);
await page.locator('summary').filter({ hasText: '本项目的采样时间不同' }).click();
await page.getByLabel('项目 1 采样时间精度', { exact: true }).selectOption('仅知道日期');
assert.equal(await page.getByLabel('项目 1 采样时间', { exact: true }).inputValue(), '');
assert.equal(await page.locator('input:enabled, textarea:enabled').count(), 0);
assert.equal(await page.getByLabel('原报告比较符', { exact: true }).inputValue(), '尚未选择');
checks.push({ name: '报告时间精度、项目独立时间、原始字段与未授权输入限制', result: 'PASS' });

await page.goto(`${baseURL}/register/observations`);
assert.equal(await page.getByLabel('观察状态', { exact: true }).inputValue(), '无法确定');
assert.equal(await page.getByLabel('体温单位', { exact: true }).inputValue(), '尚未选择');
assert.equal(await page.getByLabel('体温数值', { exact: false }).getAttribute('inputmode'), 'decimal');
assert.equal(await page.locator('input:enabled, textarea:enabled').count(), 0);
checks.push({ name: '体温单位不预填、观察未知状态及十进制键盘属性', result: 'PASS' });

await page.goto(`${baseURL}/register/therapies`);
await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
await page.locator('summary').filter({ hasText: '使用时间与补充说明' }).click();
await page.locator('summary').filter({ hasText: '查看不同使用状态的时间字段' }).click();
await page.getByLabel('时间字段展示方式', { exact: true }).selectOption('已结束');
await page.getByLabel('结束时间精度', { exact: true }).selectOption('仅知道日期');
assert.equal(await page.getByLabel('结束时间', { exact: true }).inputValue(), '');
assert.equal(await page.getByLabel('实际使用状态', { exact: true }).inputValue(), '无法确定');
await page.getByLabel('时间字段展示方式', { exact: true }).selectOption('持续使用');
assert.equal(await page.getByLabel('结束时间精度', { exact: true }).isVisible(), false);
await page.getByLabel('时间字段展示方式', { exact: true }).selectOption('无法确定');
assert.equal(await page.getByLabel('结束时间精度', { exact: true }).isVisible(), true);
assert.equal(await page.getByLabel('结束时间精度', { exact: true }).inputValue(), '仅知道日期');
await page.getByLabel('开始时间精度', { exact: true }).selectOption('知道日期与时间');
assert.equal(await page.getByLabel('开始时间', { exact: true }).inputValue(), '');
assert.equal(await page.getByLabel('结束时间', { exact: true }).inputValue(), '');
assert.equal(await page.getByLabel('实际使用状态', { exact: true }).inputValue(), '无法确定');
checks.push({ name: '治疗时间结构切换不改变实际使用状态', result: 'PASS' });
checks.push({ name: '未知治疗状态独立起止时间及切换后精度保留', result: 'PASS' });

await page.goto(`${baseURL}/records`);
await page.getByRole('button', { name: '检验报告', exact: true }).click();
await page.locator('summary').filter({ hasText: '筛选记录' }).click();
await page.getByLabel('资料来源', { exact: true }).selectOption('报告转录');
await page.getByLabel('核对状态', { exact: true }).selectOption('未核对');
await page.getByLabel('检验项目名称', { exact: true }).fill('报告原项目名称');
await page.getByLabel('开始日期', { exact: true }).fill('2026-09-27');
await page.getByLabel('结束日期', { exact: true }).fill('2026-09-26');
assert.equal(await page.getByRole('alert').innerText(), '结束日期不能早于开始日期。');
await page.getByLabel('结束日期', { exact: true }).fill('2026-09-27');
assert.equal(await page.getByRole('alert').count(), 0);
assert.equal(await page.getByLabel('结束日期', { exact: true }).evaluate(element => document.activeElement === element), true);
const filterURL = page.url();
await page.getByRole('link', { name: '查看登录入口', exact: true }).click();
await page.waitForURL(`${baseURL}/login`);
await page.goBack();
await page.waitForURL(filterURL);
assert.equal(await page.getByRole('button', { name: '检验报告', exact: true }).getAttribute('aria-pressed'), 'true');
await page.reload();
assert.equal(await page.locator('details[open] summary').filter({ hasText: '筛选记录' }).count(), 1);
assert.equal(await page.getByLabel('资料来源', { exact: true }).inputValue(), '报告转录');
assert.equal(await page.getByLabel('核对状态', { exact: true }).inputValue(), '未核对');
assert.equal(await page.getByLabel('开始日期', { exact: true }).inputValue(), '2026-09-27');
assert.equal(await page.getByLabel('检验项目名称', { exact: true }).inputValue(), '报告原项目名称');
await page.getByRole('button', { name: '治疗资料', exact: true }).click();
assert.equal(await page.getByLabel('检验项目名称', { exact: true }).count(), 0);
await page.getByRole('button', { name: '检验报告', exact: true }).click();
assert.equal(await page.getByLabel('检验项目名称', { exact: true }).inputValue(), '报告原项目名称');
for (const label of ['就医摘要', '我的草稿']) {
  await page.getByRole('link', { name: label, exact: true }).scrollIntoViewIfNeeded();
  const recordScroll = await page.evaluate(() => window.scrollY);
  await page.getByRole('link', { name: label, exact: true }).click();
  await page.getByRole('heading', { level: 1, name: label, exact: true }).waitFor();
  assert.equal(await page.getByRole('link', { name: '返回', exact: true }).getAttribute('href'), new URL(filterURL).pathname + new URL(filterURL).search);
  await page.reload();
  await page.getByRole('link', { name: '返回', exact: true }).click();
  await page.waitForURL(filterURL);
  await page.waitForFunction(position => Math.abs(window.scrollY - position) < 2, recordScroll);
  assert.equal(await page.getByRole('button', { name: '检验报告', exact: true }).getAttribute('aria-pressed'), 'true');
}
assert.equal(await page.locator('details[open] summary').filter({ hasText: '筛选记录' }).count(), 1);
assert.equal(await page.getByLabel('检验项目名称', { exact: true }).inputValue(), '报告原项目名称');
await page.getByRole('button', { name: '清除全部筛选', exact: true }).click();
await page.getByRole('button', { name: '全部', exact: true, pressed: true }).waitFor();
assert.equal(new URL(page.url()).search, '');
assert.equal(await page.getByRole('button', { name: '全部', exact: true }).getAttribute('aria-pressed'), 'true');
assert.equal(await page.getByLabel('开始日期', { exact: true }).inputValue(), '');
assert.equal(await page.getByLabel('检验项目名称', { exact: true }).inputValue(), '');
assert.equal(await page.getByText('暂无记录', { exact: true }).count(), 0);
checks.push({ name: '记录筛选、日期校验、焦点保持、返回刷新恢复及清除', result: 'PASS' });
checks.push({ name: '项目名称条件、类别适用范围与统一清除', result: 'PASS' });
checks.push({ name: '摘要草稿刷新后返回原筛选条件', result: 'PASS' });

await page.goto(`${baseURL}/records/unavailable/correct`);
await page.locator('summary').filter({ hasText: '查看可以更正的内容' }).click();
for (const [type, field] of [['体温症状', '体温数值'], ['检验报告', '报告原项目名'], ['治疗资料', '原药名']]) {
  await page.getByRole('button', { name: type, exact: true }).click();
  assert.equal(await page.getByLabel(field, { exact: true }).isVisible(), true);
  assert.equal(await page.getByLabel(field, { exact: true }).isDisabled(), true);
}
await page.getByLabel('结束时间精度', { exact: true }).selectOption('仅知道日期');
await page.getByRole('button', { name: '检验报告', exact: true }).click();
await page.getByRole('button', { name: '治疗资料', exact: true }).click();
assert.equal(await page.getByLabel('结束时间精度', { exact: true }).inputValue(), '仅知道日期');
assert.equal(await page.getByRole('button', { name: '保存更正', exact: true }).isDisabled(), true);
assert.equal(await page.locator('input:enabled, textarea:enabled').count(), 0);
checks.push({ name: '三类更正字段、时间精度保留与未经授权输入限制', result: 'PASS' });

await page.goto(`${baseURL}/register/reports`);
await page.locator('summary').filter({ hasText: '查看照片整理方式' }).click();
assert.equal(await page.getByText('当前尚未开放照片服务，没有接收或保存照片。', { exact: true }).isVisible(), true);
await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
await page.locator('summary').filter({ hasText: '逐项核对检验内容' }).click();
await page.locator('summary').filter({ hasText: '对照照片原文' }).click();
await page.locator('summary').filter({ hasText: '查找项目与单位' }).click();
await page.locator('summary').filter({ hasText: /^检验项目$/ }).click();
await page.locator('summary').filter({ hasText: /^报告单位$/ }).click();
assert.equal(await page.getByLabel('查找检验项目', { exact: true }).isDisabled(), true);
assert.equal(await page.getByLabel('查找报告单位', { exact: true }).isDisabled(), true);
assert.equal(await page.locator('img').count(), 0);
assert.equal(await page.locator('.dictionary-options button').count(), 0);
checks.push({ name: '照片原文与字典入口按需展开且不生成替代资料', result: 'PASS' });

await page.goto(`${baseURL}/records/unavailable`);
await page.getByRole('button', { name: '来源与状态', exact: true }).click();
assert.equal(await page.getByRole('heading', { name: '资料依据', exact: true }).isVisible(), true);
await page.getByRole('button', { name: '时间与版本', exact: true }).click();
assert.equal(await page.getByText('当前版本', { exact: true }).isVisible(), true);
await page.getByRole('button', { name: '原始内容', exact: true }).click();
assert.equal(await page.getByRole('heading', { name: '原始资料', exact: true }).isVisible(), true);
assert.equal(await page.getByRole('button', { name: '更正这份记录', exact: true }).isDisabled(), true);
checks.push({ name: '记录详情分组切换与受限操作', result: 'PASS' });

await page.goto(`${baseURL}/summaries`);
await page.getByLabel('开始日期', { exact: true }).fill('2026-09-27');
await page.getByLabel('结束日期', { exact: true }).fill('2026-09-26');
assert.equal(await page.getByRole('alert').innerText(), '结束日期不能早于开始日期。');
await page.getByLabel('结束日期', { exact: true }).fill('2026-09-27');
assert.equal(await page.getByRole('alert').count(), 0);
assert.match(await page.getByRole('status').innerText(), /尚未生成摘要/);
await page.locator('summary').filter({ hasText: '摘要包含什么？' }).click();
assert.equal(await page.getByLabel('开始日期', { exact: true }).inputValue(), '2026-09-27');
assert.equal(await page.getByRole('button', { name: '生成就医摘要', exact: true }).isDisabled(), true);
assert.equal(await page.locator('.guided-flow').count(), 0);
checks.push({ name: '摘要范围校验与未生成状态', result: 'PASS' });

await page.goto(`${baseURL}/drafts`);
await page.getByRole('button', { name: '治疗资料', exact: true }).click();
assert.match(await page.getByRole('status').innerText(), /治疗资料/);
assert.equal(await page.getByRole('heading', { name: '当前无法读取草稿', exact: true }).isVisible(), true);
checks.push({ name: '草稿分类与未读取状态', result: 'PASS' });

await page.goto(`${baseURL}/account/data-use`);
for (const summary of await page.locator('summary').all()) {
  await summary.focus();
  await page.keyboard.press('Enter');
  assert.equal(await summary.evaluate(element => element.parentElement.open), true);
  await page.keyboard.press('Space');
  assert.equal(await summary.evaluate(element => element.parentElement.open), false);
}
checks.push({ name: '资料使用说明的完整键盘展开收起', result: 'PASS' });

await page.goto(`${baseURL}/prototype`);
const catalogueLinks = await page.locator('main .list-link').evaluateAll(links => links.map(link => link.getAttribute('href')));
assert.equal(catalogueLinks.length, 23);
for (const path of catalogueLinks) {
  await page.goto(`${baseURL}/prototype`);
  await page.locator(`main .list-link[href="${path}"]`).click();
  await page.waitForURL(`${baseURL}${path}`);
  await page.locator('h1').waitFor();
  assert.equal(await page.locator('main article[data-screen-id]').count(), 1);
}
checks.push({ name: '页面目录的全部 23 个页面入口', result: 'PASS' });

for (const path of ['/register/reports', '/register/therapies']) {
  await page.goto(`${baseURL}${path}`);
  await page.locator('.guided-flow').waitFor();
  assert.equal(await page.locator('.guide-panel').count(), 2);
  assert.equal(await page.locator('.guide-panel:visible').count(), 1);
  await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
  assert.equal(await page.evaluate(() => document.activeElement.tagName), 'H2');
  assert.equal(await page.getByRole('button', { name: '确认并保存', exact: true }).isVisible(), true);
  assert.equal(await page.locator('.guide-panel:visible').count(), 1);
  await page.getByRole('button', { name: '返回照片', exact: true }).click();
  assert.equal(await page.getByRole('button', { name: '拍照添加', exact: true }).isVisible(), true);
  await page.reload();
  await page.locator('.guided-flow').waitFor();
  assert.equal(await page.getByRole('button', { name: '拍照添加', exact: true }).isVisible(), true);
  checks.push({ name: '照片与确认保存两个页面、返回、焦点与刷新', path, result: 'PASS' });
}
for (const [path, action] of [['/register/observations', '保存本次记录'], ['/associations/new', '暂时无法申请'], ['/records/unavailable/correct', '保存更正'], ['/summaries', '生成就医摘要']]) {
  await page.goto(`${baseURL}${path}`);
  await page.getByRole('button', { name: action, exact: true }).waitFor();
  assert.equal(await page.locator('.guided-flow').count(), 0);
  assert.equal(await page.getByRole('button', { name: action, exact: true }).isDisabled(), true);
  checks.push({ name: '单页任务直接显示当前操作且未开放写入', path, result: 'PASS' });
}

for (const width of [320, 360, 390, 430, 844]) {
  await page.setViewportSize({ width, height: 844 });
  for (const [path, title] of routes) {
    console.log(`检查宽度 ${width}：${path}`);
    await page.goto(`${baseURL}${path}`);
    await page.getByRole('heading', { level: 1, name: title, exact: true }).waitFor();
    const stepCount = await page.locator('.guide-panel').count();
    for (let step = 0; step < Math.max(stepCount, 1); step++) {
    if (step > 0) await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
    const metrics = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      content: document.documentElement.scrollWidth,
      targets: [...document.querySelectorAll('main a, main button:enabled, main select:enabled, main input:enabled, main summary, .tab-link, .list-link')]
        .filter(element => element.getClientRects().length > 0)
        .map(element => ({ label: element.textContent.trim(), width: element.getBoundingClientRect().width, height: element.getBoundingClientRect().height })),
    }));
    assert.ok(metrics.content <= metrics.viewport, `${width} ${path}`);
    assert.ok(metrics.targets.every(target => target.width >= 44 && target.height >= 44), `${width} ${path} ${JSON.stringify(metrics.targets.filter(target => target.width < 44 || target.height < 44))}`);
    }
  }
  checks.push({ name: 'DOM 宽度与触控区域检查', width, result: 'PASS' });
}

await page.setViewportSize({ width: 320, height: 844 });
await page.goto(`${baseURL}/home`);
await page.locator('h1').waitFor();
await page.addStyleTag({ content: ':root:root { font-size: 32px !important; }' });
const enlarged = await page.evaluate(() => ({
  viewport: document.documentElement.clientWidth,
  content: document.documentElement.scrollWidth,
  fontSize: getComputedStyle(document.documentElement).fontSize,
}));
assert.equal(enlarged.fontSize, '32px');
assert.ok(enlarged.content <= enlarged.viewport);
await page.getByRole('link', { name: '拍照添加资料', exact: true }).click();
await page.waitForURL(`${baseURL}/register/reports`);
checks.push({ name: '根文字 200% 放大后的内容宽度与操作', result: 'PASS' });

for (const [path] of routes) {
  await page.goto(`${baseURL}${path}`);
  await page.locator('h1').waitFor();
  await page.addStyleTag({ content: ':root:root { font-size: 32px !important; }' });
  const stepCount = await page.locator('.guide-panel').count();
  for (let step = 0; step < Math.max(stepCount, 1); step++) {
  if (step > 0) await page.getByRole('button', { name: '预览确认页面', exact: true }).click();
  for (const summary of await page.locator('main summary').all()) {
    if (await summary.isVisible()) await summary.click();
  }
  const dimensions = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, content: document.documentElement.scrollWidth }));
  assert.ok(dimensions.content <= dimensions.viewport, `200% ${path}: ${dimensions.content}`);
  }
}
checks.push({ name: '全部页面在 320px 与 200% 文字下展开说明无横向溢出', result: 'PASS' });

await page.goto(`${baseURL}/home`);
await page.locator('h1').waitFor();
await page.locator('h1').focus();
await page.keyboard.press('Tab');
assert.equal(await page.evaluate(() => document.activeElement.getAttribute('href')), '/alerts');
await page.keyboard.press('Enter');
await page.waitForURL(`${baseURL}/alerts`);
await page.getByRole('heading', { name: '预警', exact: true }).waitFor();
assert.equal(await page.evaluate(() => document.activeElement.tagName), 'H1');
checks.push({ name: '键盘进入与页面焦点', result: 'PASS' });

await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
const theme = await page.evaluate(() => ({
  background: getComputedStyle(document.documentElement).getPropertyValue('--page-background').trim(),
  motion: getComputedStyle(document.querySelector('.tab-link')).transitionDuration,
}));
assert.equal(theme.background, '#000');
assert.equal(theme.motion, '0s');
checks.push({ name: '系统深色模式与减少动态效果', result: 'PASS' });

const persistence = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
assert.deepEqual(persistence, { local: 0, session: 0 });
assert.deepEqual(remoteRequests, []);
assert.deepEqual(failedRequests, []);
assert.deepEqual(pageErrors, []);
assert.deepEqual(consoleErrors, []);
checks.push({ name: '无病例持久缓存、外部请求及浏览器错误', result: 'PASS' });

const packageData = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
const lock = await readFile(new URL('../package-lock.json', import.meta.url));
const fileHashes = {};
for (const folder of ['src', 'dist', 'scripts']) {
  const directory = new URL(`../${folder}/`, import.meta.url);
  for (const filename of await readdir(directory, { recursive: true })) {
    const file = new URL(filename, directory);
    if ((await stat(file)).isFile()) {
      fileHashes[`${folder}/${filename}`] = createHash('sha256').update(await readFile(file)).digest('hex');
    }
  }
}
const report = {
  capability: 'PROTOTYPE',
  scope: '三项导航、功能入口归属、上下文返回、照片确认保存与单页任务；不包含 OCR 或临床服务验收',
  baseURL,
  version: packageData.version,
  lockSha256: createHash('sha256').update(lock).digest('hex'),
  fileHashes,
  startedAt,
  completedAt: new Date().toISOString(),
  browser: browser.version(),
  node: process.version,
  checks,
  visual: 'NOT_RUN',
  actualMobile: 'NOT_RUN',
  business: 'NOT_RUN',
};
await mkdir(new URL('../.artifacts/', import.meta.url), { recursive: true });
await writeFile(new URL('../.artifacts/browser-check.json', import.meta.url), JSON.stringify(report, null, 2));
await browser.close();
console.log(JSON.stringify({ checks: checks.length, result: 'PASS', report: '.artifacts/browser-check.json' }));
