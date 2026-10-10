import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { chromium } from '../guardian-h5/node_modules/playwright/index.mjs';

const root = new URL('./', import.meta.url);
const baseURL = 'http://127.0.0.1:5192/';
const data = JSON.parse(await readFile(new URL('current-patient/case.json', root), 'utf8'));
const manifest = JSON.parse(await readFile(new URL('current-patient/source-manifest.json', root), 'utf8'));
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const errors = [];
const checks = [];
try {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => errors.push(request.url()));
  page.on('request', request => assert.ok(request.url().startsWith(baseURL), '页面不得请求外部服务'));
  const response = await page.goto(baseURL);
  assert.equal(response.status(), 200);
  await page.locator('#content[data-loaded="true"]').waitFor();
  assert.equal(await page.locator('h1').innerText(), '当前患儿病例资料');
  assert.ok((await page.locator('#patient-summary').innerText()).includes(data.patient.reported_age.raw_text));
  assert.equal(await page.locator('#measurements tbody tr').count(), data.measurement_panels.reduce((count, panel) => count + panel.items.length, 0));
  assert.equal(await page.locator('#microbiology tbody tr').count(), data.microbiology.reduce((count, group) => count + group.results.length, 0));
  assert.equal(await page.locator('#therapy article').count(), data.therapy_events.length);
  assert.equal(await page.locator('#data-issues article').count(), data.data_issues.length);
  assert.equal(await page.locator('#followups article').count(), data.followup_records.length);
  assert.ok((await page.locator('[data-followup-id="response-antiviral"]').innerText()).includes('复查CMV DNA阴性'));
  assert.ok((await page.locator('[data-followup-id="response-induction"]').innerText()).includes('铁蛋白：逐步下降至正常'));
  assert.ok((await page.locator('[data-followup-id="followup-latest"]').innerText()).includes('线上门诊长期随访'));
  const scoreRows = await page.locator('#disease-activity tbody tr').allTextContents();
  assert.equal(scoreRows.length, data.disease_activity.rows.length);
  assert.equal(await page.locator('#disease-activity tbody tr').last().locator('td').nth(1).innerText(), '6');
  for (const panel of data.measurement_panels) {
    const article = page.locator('#measurements article').filter({ has: page.getByRole('heading', { name: panel.label, exact: true }) });
    const rows = article.locator('tbody tr');
    for (let position = 0; position < panel.items.length; position++) {
      assert.equal(await rows.nth(position).locator('td').nth(1).innerText(), panel.items[position].raw_text);
    }
  }
  checks.push('全部测量结果、病原学行数、治疗记录及来源差异正确显示');
  const images = page.locator('#imaging img');
  assert.equal(await images.count(), 4);
  await page.waitForFunction(() => [...document.querySelectorAll('#imaging img')].every(image => image.complete && image.naturalWidth > 0));
  for (const source of manifest.sources) {
    const response = await page.request.get(new URL(`current-patient/${source.path}`, baseURL).href);
    assert.equal(response.status(), 200);
    assert.equal(createHash('sha256').update(await response.body()).digest('hex'), source.sha256);
  }
  checks.push('五个来源文件通过 HTTP 读取并与 SHA-256 清单一致；四张图片正常加载');
  const imageLink = page.locator('#imaging figure a').first();
  const popupPromise = page.waitForEvent('popup');
  await imageLink.click();
  const popup = await popupPromise;
  await popup.waitForLoadState('domcontentloaded');
  assert.equal(popup.url(), await imageLink.getAttribute('href'));
  await popup.close();
  await page.getByText('展开病例原文', { exact: true }).click();
  const original = await readFile(new URL('current-patient/sources/已粘贴的文本.txt', root), 'utf8');
  assert.equal(await page.locator('#original pre').textContent(), original);
  assert.equal(await page.locator('#original details').getAttribute('open'), '');
  checks.push('原图链接和完整原文展开正常');
  for (const width of [390, 1120]) {
    await page.setViewportSize({ width, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), '页面不应横向溢出');
  }
  assert.deepEqual(errors, []);
  checks.push('移动与桌面宽度无页面横向溢出；没有脚本错误或失败请求');
  const files = ['index.html', 'preview.css', 'preview.mjs', 'load-current-patient.mjs', 'check-preview.mjs', 'current-patient/case.json', 'current-patient/source-manifest.json'];
  const hashes = {};
  for (const file of files) hashes[file] = createHash('sha256').update(await readFile(new URL(file, root))).digest('hex');
  await writeFile(new URL('preview-validation-report.json', root), JSON.stringify({ status: 'PASS', checked_at: new Date().toISOString(), browser: await browser.version(), checks, inputs_sha256: hashes, visual_validation: 'NOT_RUN', production_api_integration: 'NOT_RUN' }, null, 2) + '\n');
  console.log(JSON.stringify({ status: 'PASS', checks: checks.length, images: 4, score_rows: scoreRows.length }));
} finally {
  await browser.close();
}
