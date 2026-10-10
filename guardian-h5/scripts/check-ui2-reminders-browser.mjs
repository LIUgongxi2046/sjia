import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
const checks = [];
try {
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  for (const width of [320, 360, 390, 430, 580]) {
    await page.setViewportSize({ width, height: 844 });
    const anonymous = page.waitForResponse(response => new URL(response.url()).pathname === '/api/session' && response.status() === 401);
    await page.goto('http://127.0.0.1:4173/account/reminders');
    await anonymous;
    await page.locator('h1').waitFor();
    assert.equal(await page.locator('[data-screen-id="UI2-32"]').count(), 1);
    assert.equal(await page.getByRole('heading', { name: '资料补充提醒', level: 1, exact: true }).count(), 1);
    assert.equal(await page.getByRole('navigation', { name: '家庭主导航', exact: true }).count(), 0);
    const enabled = page.getByRole('switch', { name: '启用资料补充提醒', exact: true });
    assert.equal(await enabled.isChecked(), false);
    assert.equal(await enabled.isDisabled(), true);
    assert.deepEqual(await page.getByLabel('提醒对象', { exact: true }).locator('option').allTextContents(), ['已产生的报告', '未完成的草稿', '本人记录任务']);
    for (const label of ['提醒对象', '提醒时间', '时区']) assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
    assert.equal(await page.getByLabel('提醒时间', { exact: true }).getAttribute('type'), 'datetime-local');
    assert.equal(await page.getByLabel('时区', { exact: true }).getAttribute('placeholder'), '选择本人所在时区');
    await page.getByText('安静时段与延后设置', { exact: true }).click();
    for (const label of ['安静时段开始', '安静时段结束', '延后至']) assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
    for (const name of ['保存提醒设置', '暂停提醒', '关闭提醒']) assert.equal(await page.getByRole('button', { name, exact: true }).isDisabled(), true);
    assert.equal(await page.getByText('只提醒已经产生的报告或本人设置的记录任务。', { exact: true }).count(), 1);
    assert.equal(await page.getByText('提醒未处理不会改变临床评估，不生成检查安排。', { exact: true }).count(), 1);
    assert.equal(await page.locator('img[data-private-source]').count(), 0);
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    const geometry = await page.locator('[data-screen-id="UI2-32"]').evaluate(node => {
      const content = node.shadowRoot?.querySelector('.phone-scroll');
      if (!content) throw new Error('提醒页面缺少内容区域。');
      return { width: content.clientWidth, scroll_width: content.scrollWidth };
    });
    assert.ok(geometry.scroll_width <= geometry.width);
    checks.push({ viewport_width: width, geometry, result: 'PASS' });
  }
  const report = { status: 'PASS', checks, credentials_submitted: false, clinical_data_created: false,
    reminder_business_validation: 'NOT_RUN', visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' };
  await writeFile('../.planning-work/t54-reminders-browser.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(report) + '\n');
} finally {
  await context.close();
  await browser.close();
}
