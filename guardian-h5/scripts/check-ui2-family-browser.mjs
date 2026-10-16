import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { emptyTime, timeInputValue, timePayload } from '../src/components/medicalTimeValue.ts';
import { reportFieldValue } from '../src/features/registration/reportFieldValue.ts';

const origin = 'http://127.0.0.1:4173';
const existing = JSON.parse((await promisify(execFile)('../backend/.venv/bin/python', ['../backend/tests/verify_record_search_runtime.py', '--route-json'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })).stdout);
const relations = JSON.parse((await promisify(execFile)('../backend/.venv/bin/python', ['../backend/tests/verify_family_read_runtime.py', '--route-json'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })).stdout);
const summary = JSON.parse((await promisify(execFile)('../backend/.venv/bin/python', ['../backend/tests/verify_summary_runtime.py', '--route-json'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })).stdout);
const savedDates = JSON.parse((await promisify(execFile)('../backend/.venv/bin/python', ['../backend/tests/read_saved_month_input.py'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })).stdout);
const savedSources = JSON.parse((await promisify(execFile)('../backend/.venv/bin/python', ['../backend/tests/read_saved_report_input.py'], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 })).stdout);
assert.equal(savedSources.transaction_read_only, true);
assert.equal(savedSources.clinical_data_created, false);
assert.equal(savedDates.transaction_read_only, true);
assert.equal(savedDates.clinical_data_created, false);
const timeValidator = new Ajv2020({ strict: true, strictTypes: false, strictRequired: false, allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(timeValidator);
timeValidator.addSchema(JSON.parse(await readFile('../data-design/dataset.schema.json', 'utf8')), 'dataset.schema.json');
timeValidator.addSchema(JSON.parse(await readFile('../data-design/case-extension.schema.json', 'utf8')), 'case-extension.schema.json');
timeValidator.addSchema(JSON.parse(await readFile('../backend/recognition/report.schema.json', 'utf8')), 'report-recognition.schema.json');
timeValidator.addSchema(JSON.parse(await readFile('../backend/recognition/therapy.schema.json', 'utf8')), 'therapy-recognition.schema.json');
assert.ok(timeValidator.getSchema('report-recognition.schema.json'));
assert.ok(timeValidator.getSchema('therapy-recognition.schema.json'));
const validTime = timeValidator.getSchema('dataset.schema.json#/$defs/medicalTime');
assert.ok(validTime);
const routes = [
  { path: '/account/reminders', id: 'UI2-32', title: '资料补充提醒', primary: false },
  { path: '/archives/grouping', id: 'UI2-28', title: '文书与就诊归组', primary: false },
  { path: '/archives/source', id: 'UI2-33', title: '原始资料', primary: false },
  { path: '/home', id: 'UI2-01', title: '陪伴孩子，健康成长', primary: true },
  { path: '/register/observations', id: 'UI2-02', title: '体温与症状', primary: false },
  { path: '/register/reports', id: 'UI2-03', title: '拍照添加报告', primary: false },
  { path: '/register/therapies', id: 'UI2-04', title: '治疗资料', primary: false },
  { path: '/alerts', id: 'UI2-11', title: '预警记录', primary: false },
  { path: '/alerts/current', id: 'UI2-12', title: '提示详情', primary: false },
  { path: '/records', id: 'UI2-05', title: '记录', primary: true },
  { path: `/records/${existing.record_id}`, id: 'UI2-06', title: '记录详情', primary: false },
  { path: `/records/analytes/${encodeURIComponent(existing.analyte_id)}`, id: 'UI2-07', title: '历次检验', primary: false },
  { path: `/records/${existing.record_id}/correct`, id: 'UI2-08', title: '更正记录', primary: false },
  { path: `/records/${existing.record_id}/history`, id: 'UI2-09', title: '更正历史', primary: false },
  { path: `/records/versions/${existing.record_version_id}`, id: 'UI2-10', title: '历史版本', primary: false },
  { path: '/summaries', id: 'UI2-13', title: '准备就医摘要', primary: false },
  { path: `/summaries/${summary.summary_id}`, id: 'UI2-14', title: '就医摘要', primary: false },
  { path: '/account', id: 'UI2-15', title: '我的', primary: true },
  { path: '/account/data-use', id: 'UI2-16', title: '资料使用说明', primary: false },
  { path: '/login', id: 'UI2-17', title: '登录', primary: false },
  { path: '/associations', id: 'UI2-18', title: '患儿管理', primary: false },
  { path: '/associations/new', id: 'UI2-19', title: '关联申请', primary: false },
  { path: `/associations/${relations.relation_id}`, id: 'UI2-20', title: '关联详情', primary: false },
  { path: '/drafts', id: 'UI2-21', title: '我的草稿', primary: false },
  { path: '/access-denied', id: 'UI2-22', title: '访问受限', primary: false },
  { path: '/service-unavailable', id: 'UI2-23', title: '服务暂不可用', primary: false },
  { path: '/page-not-found', id: 'UI2-24', title: '页面不存在', primary: false },
];
const digest = data => createHash('sha256').update(data).digest('hex');
const logoHash = digest(await readFile('../ui-delivery/identity/2x/logo-embrace@2x.png'));
const childHash = digest(await readFile('../ui-delivery/assets/2x/child-neutral-front@2x.png'));
const checks = [], requests = [], apiResponses = [], pageErrors = [], assets = [];
const startedAt = performance.now();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext();
try {
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  page.setDefaultNavigationTimeout(15000);
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => requests.push({ url: request.url(), method: request.method() }));
  page.on('response', response => {
    const url = new URL(response.url());
    if (url.pathname.startsWith('/api/')) apiResponses.push({ path: url.pathname, status: response.status(), cache_control: response.headers()['cache-control'] });
    if (url.origin === origin && url.pathname.startsWith('/assets/') && ['.js', '.css', '.png'].includes(path.extname(url.pathname))) {
      assets.push((async () => {
        const data = await response.body();
        const file = `dist${url.pathname}`;
        assert.equal(digest(data), digest(await readFile(file)));
        if (url.pathname.includes('logo-embrace')) assert.equal(digest(data), logoHash);
        if (url.pathname.includes('child-neutral-front')) assert.equal(digest(data), childHash);
        return { path: file, sha256: digest(data), size_bytes: data.length };
      })());
    }
  });
  for (const width of [320, 360, 390, 430, 580]) {
    await page.setViewportSize({ width, height: 844 });
    for (const route of routes) {
      const denial = page.waitForResponse(response => new URL(response.url()).pathname === '/api/session' && response.status() === 401);
      await page.goto(`${origin}${route.path}`);
      await denial;
      const frame = page.locator(`[data-screen-id="${route.id}"]`);
      await frame.waitFor();
      assert.equal(await page.getByRole('heading', { name: route.title, level: 1, exact: true }).count(), 1);
      assert.equal(await page.locator('.app-shell').count(), 0);
      assert.equal(await page.getByRole('navigation', { name: '家庭主导航', exact: true }).count(), route.primary ? 1 : 0);
      assert.equal(await page.locator('.phone-topline').count(), 0);
      const geometry = await frame.evaluate(node => {
        if (!node.shadowRoot) throw new Error('当前家庭页面缺少样式容器。');
        const device = node.shadowRoot.querySelector('.device');
        const content = node.shadowRoot.querySelector('.phone-scroll');
        if (!device || !content) throw new Error('当前家庭页面缺少内容区域。');
        return { ink: getComputedStyle(node).getPropertyValue('--ink'), canvas: getComputedStyle(node).getPropertyValue('--canvas'),
          styles: node.shadowRoot.querySelectorAll('style').length, device_width: device.clientWidth,
          viewport_width: innerWidth, content_scroll_width: content.scrollWidth, content_width: content.clientWidth };
      });
      assert.equal(geometry.ink.toLowerCase(), '#243b55'); assert.equal(geometry.canvas.toLowerCase(), '#f1f5fa'); assert.equal(geometry.styles, 1);
      assert.ok(geometry.device_width <= Math.min(width, 580));
      assert.ok(geometry.content_scroll_width <= geometry.content_width);
      assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
      assert.equal(await page.locator('.patient, .current-alert').count(), ['UI2-02', 'UI2-03', 'UI2-04', 'UI2-07', 'UI2-08', 'UI2-28', 'UI2-32', 'UI2-33'].includes(route.id) ? 1 : 0);
      if (route.id === 'UI2-32') {
        const enabled = page.getByRole('switch', { name: '启用资料补充提醒', exact: true });
        assert.equal(await enabled.isChecked(), false); assert.equal(await enabled.isDisabled(), true);
        assert.deepEqual(await page.getByLabel('提醒对象', { exact: true }).locator('option').allTextContents(), ['已产生的报告', '未完成的草稿', '本人记录任务']);
        for (const label of ['提醒对象', '提醒时间', '时区']) assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('提醒时间', { exact: true }).getAttribute('type'), 'datetime-local');
        assert.equal(await page.getByLabel('时区', { exact: true }).getAttribute('placeholder'), '选择本人所在时区');
        await page.getByText('安静时段与延后设置', { exact: true }).click();
        for (const label of ['安静时段开始', '安静时段结束', '延后至']) assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
        for (const name of ['保存提醒设置', '暂停提醒', '关闭提醒']) assert.equal(await page.getByRole('button', { name, exact: true }).isDisabled(), true);
        assert.equal(await page.getByText('提醒未处理不会改变临床评估，不生成检查安排。', { exact: true }).count(), 1);
      }
      if (route.id === 'UI2-28') {
        await page.getByLabel('文书类型', { exact: true }).waitFor();
        assert.equal(await page.getByRole('heading', { name: '文书归属', exact: true }).count(), 1);
        assert.equal(await page.getByRole('button', { name: '查看原文依据', exact: true }).isDisabled(), true);
        assert.deepEqual(await page.getByLabel('文书类型', { exact: true }).locator('option').allTextContents(), ['请选择', '检验报告', '住院病历', '出院记录', '门诊记录', '治疗资料', '影像报告文字', '影像附件', '待分类']);
        assert.deepEqual(await page.getByLabel('就诊类型', { exact: true }).locator('option').allTextContents(), ['请选择', '住院', '门诊', '未知']);
        assert.equal(await page.getByLabel('文书类型', { exact: true }).inputValue(), '');
        assert.equal(await page.getByLabel('就诊类型', { exact: true }).inputValue(), '');
        for (const label of ['文书类型', '来源医院', '就诊类型', '开始日期', '结束日期', '归属说明']) assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
        for (const label of ['开始日期', '结束日期']) assert.equal(await page.getByLabel(label, { exact: true }).getAttribute('type'), 'date');
        assert.equal(await page.getByLabel('归属说明', { exact: true }).getAttribute('placeholder'), '同份文书记载多次就诊时，分别确认每项事实');
        assert.equal(await page.getByText('文书形成时间与事实发生时间分别保留。', { exact: true }).count(), 1);
        assert.equal(await page.getByRole('button', { name: '确认归组', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('heading', { name: '文书归组已经保存', exact: true }).count(), 0);
        assert.equal(await page.locator('img[src^="blob:"], img[src^="/api/"]').count(), 0);
      }
      if (route.id === 'UI2-33') {
        assert.equal(await page.getByRole('heading', { name: '尚未选择原始文书', exact: true }).count(), 1);
        assert.equal(await page.locator('.source-view').count(), 1);
        assert.equal(await page.getByRole('button', { name: '选择照片', exact: true }).isDisabled(), true);
        assert.deepEqual(await page.locator('.form-card .pair > span').allTextContents(), ['文书类型', '来源医院', '原件版本', '文书形成时间', '平台取得时间', '允许用途', '副本复核状态']);
        assert.deepEqual(await page.locator('.form-card .pair > strong').allTextContents(), Array(7).fill('待读取'));
        assert.equal(await page.getByRole('button', { name: '逐项内容与时间', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '查看文书归组', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('dialog', { name: '原图查看器', exact: true }).count(), 0);
        assert.equal(await page.locator('img[src^="blob:"], img[src^="/api/"]').count(), 0);
      }
      if (route.id === 'UI2-08') {
        assert.equal(await page.getByText('更正产生新版本，专业核对需重新完成。', { exact: true }).count(), 1);
        assert.equal(await page.getByLabel('体温原值', { exact: true }).inputValue(), '');
        assert.equal(await page.getByLabel('体温原值', { exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('测量时间', { exact: true }).inputValue(), '');
        assert.equal(await page.getByLabel('测量时间', { exact: true }).isDisabled(), true);
        assert.deepEqual(await page.getByLabel('时间精度', { exact: true }).first().locator('option').allTextContents(), ['准确时间', '只知道日期', '只知道月份', '无法确定']);
        assert.equal(await page.getByLabel('时间精度', { exact: true }).first().inputValue(), 'unknown');
        assert.deepEqual(await page.locator('.symptoms h3').allTextContents(), ['发热表现', '皮疹', '关节疼痛或肿胀']);
        for (const label of ['发热表现', '皮疹', '关节疼痛或肿胀']) {
          const tabs = page.getByRole('tablist', { name: label, exact: true });
          assert.deepEqual(await tabs.getByRole('tab').allTextContents(), ['观察到', '未观察到', '未知']);
          for (const tab of await tabs.getByRole('tab').all()) assert.equal(await tab.isDisabled(), true);
        }
        assert.equal(await page.getByRole('button', { name: '说一说观察到的情况 转成文字后，由本人确认', exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('更正原因', { exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('更正原因', { exact: true }).getAttribute('placeholder'), '说明需要更正的内容与依据');
        assert.equal(await page.getByRole('button', { name: '保存更正', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '保存为草稿', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('dialog', { name: '语音记录与逐项核对', exact: true }).count(), 0);
      }
      if (route.id === 'UI2-07') {
        const project = page.getByLabel('检验项目', { exact: true });
        assert.equal(await project.isDisabled(), true);
        assert.equal(await project.inputValue(), '');
        assert.deepEqual(await project.locator('option').allTextContents(), ['获准项目尚未读取', '铁蛋白', '血小板计数', 'AST', '甘油三酯', '纤维蛋白原', 'LDH']);
        assert.equal(await page.getByText('原始变化', { exact: true }).count(), 1);
        assert.equal(await page.getByRole('heading', { name: '尚无可比较的结果', exact: true }).count(), 0);
        assert.equal(await page.getByRole('heading', { name: '历次结果', exact: true }).count(), 0);
        assert.equal(await page.getByRole('button', { name: '查看原始资料', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('link', { name: '查看观察时段 采样时间与上传时间分别保存', exact: true }).getAttribute('href'), '/records/period');
        assert.equal(await page.locator('canvas, img[src^="/api/"]').count(), 0);
        assert.equal(await page.getByRole('dialog', { name: '查看原始资料', exact: true }).count(), 0);
      }
      if (route.id === 'UI2-04') {
        assert.equal(await page.getByRole('heading', { name: '添加处方或治疗资料', exact: true }).count(), 1);
        assert.equal(await page.getByRole('button', { name: '拍照或选择照片', exact: true }).isDisabled(), true);
        assert.deepEqual(await page.locator('.step-track span').allTextContents(), ['提供原件', '逐项确认', '保存资料']);
        const status = page.getByLabel('实际使用状态', { exact: true });
        assert.deepEqual(await status.locator('option').allTextContents(), ['持续使用', '已结束', '无法确定']);
        assert.equal(await status.inputValue(), '无法确定');
        for (const label of ['原药名', '开始日期', '结束日期', '资料说明']) {
          assert.equal(await page.getByLabel(label, { exact: true }).inputValue(), '');
          assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
        }
        for (const label of ['开始日期', '结束日期']) assert.equal(await page.getByLabel(label, { exact: true }).getAttribute('type'), 'date');
        assert.equal(await page.getByLabel('原药名', { exact: true }).getAttribute('placeholder'), '按原文填写');
        assert.equal(await page.getByLabel('资料说明', { exact: true }).getAttribute('placeholder'), '保留原文与实际使用情况');
        assert.equal(await page.getByRole('button', { name: '确认并保存资料', exact: true }).isDisabled(), true);
        await page.getByText('草稿与近期资料', { exact: true }).click();
        assert.equal(await page.getByRole('button', { name: '保存当前草稿', exact: true }).isDisabled(), true);
        await page.getByText('草稿与近期资料', { exact: true }).click();
        assert.equal(await page.locator('input[type="file"]').count(), 2);
        for (const input of await page.locator('input[type="file"]').all()) assert.equal(await input.isDisabled(), true);
        assert.equal(await page.locator('.selected-file, img[src^="/api/"]').count(), 0);
        assert.equal(await page.getByLabel('时间精度', { exact: true }).count(), 2);
        for (const precision of await page.getByLabel('时间精度', { exact: true }).all()) {
          assert.deepEqual(await precision.locator('option').allTextContents(), ['准确时间', '只知道日期', '只知道月份', '无法确定']);
          assert.equal(await precision.inputValue(), 'unknown');
          assert.equal(await precision.isDisabled(), true);
        }
        await page.getByText('资料出处与确认', { exact: true }).click();
        assert.equal(await page.getByLabel('资料来源', { exact: true }).inputValue(), 'self_report');
        assert.equal(await page.getByLabel('资料来源', { exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('来源描述或引用', { exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('已核对患儿归属、原始资料与登记来源', { exact: true }).isChecked(), false);
        assert.equal(await page.getByLabel('已核对患儿归属、原始资料与登记来源', { exact: true }).isDisabled(), true);
        await page.getByText('资料出处与确认', { exact: true }).click();
      }
      if (route.id === 'UI2-03') {
        assert.equal(await page.getByRole('heading', { name: '把报告原件保留下来', exact: true }).count(), 1);
        assert.equal(await page.getByText('文字清晰、页面完整，按文书顺序选择。', { exact: true }).count(), 1);
        assert.equal(await page.getByRole('button', { name: '拍照或选择照片', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '逐项确认内容 每个项目保留自己的采样时间', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '保存当前草稿', exact: true }).isDisabled(), true);
        assert.deepEqual(await page.locator('.step-track span').allTextContents(), ['提供原件', '逐项确认', '保存资料']);
        assert.equal(await page.locator('input[type="file"]').count(), 2);
        for (const input of await page.locator('input[type="file"]').all()) assert.equal(await input.isDisabled(), true);
        assert.equal(await page.locator('input[type="file"][multiple]').getAttribute('accept'), 'image/jpeg,image/png,image/webp');
        assert.equal(await page.locator('.selected-file').count(), 0);
        assert.equal(await page.locator('img[src^="/api/"]').count(), 0);
        assert.equal(await page.getByRole('dialog', { name: '逐项确认内容', exact: true }).count(), 0);
      }
      if (route.id === 'UI2-02') {
        assert.equal(await page.getByLabel('体温原值', { exact: true }).inputValue(), '');
        assert.equal(await page.getByLabel('体温原值', { exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('测量时间', { exact: true }).inputValue(), '');
        assert.equal(await page.getByLabel('测量时间', { exact: true }).isDisabled(), true);
        const precision = page.getByLabel('时间精度', { exact: true }).first();
        assert.deepEqual(await precision.locator('option').allTextContents(), ['准确时间', '只知道日期', '只知道月份', '无法确定']);
        assert.equal(await precision.inputValue(), 'unknown');
        assert.equal(await precision.isDisabled(), true);
        assert.deepEqual(await page.locator('.symptoms h3').allTextContents(), ['发热表现', '皮疹', '关节疼痛或肿胀']);
        for (const label of ['发热表现', '皮疹', '关节疼痛或肿胀']) {
          const tabs = page.getByRole('tablist', { name: label, exact: true });
          assert.deepEqual(await tabs.getByRole('tab').allTextContents(), ['观察到', '未观察到', '未知']);
          for (const tab of await tabs.getByRole('tab').all()) assert.equal(await tab.isDisabled(), true);
        }
        assert.equal(await page.getByRole('button', { name: '说一说观察到的情况 转成文字后，由本人确认', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '确认并保存', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '保存为草稿', exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('补充说明', { exact: true }).isDisabled(), true);
        await page.getByText('测量方式与补充信息', { exact: true }).click();
        assert.equal(await page.getByLabel('体温单位', { exact: true }).inputValue(), '尚未选择');
        assert.equal(await page.getByLabel('症状发生时间', { exact: true }).inputValue(), '');
        assert.equal(await page.getByRole('dialog', { name: '语音记录与逐项核对', exact: true }).count(), 0);
      }
      if (route.path === '/home') {
        assert.equal(await page.getByRole('img', { name: '通用儿童插画', exact: true }).count(), 1);
        assert.equal(await page.locator('.observed-tags button').count(), 3);
        assert.equal(await page.locator('.observed-tags').textContent(), '体温需要登录并核验权限皮疹需要登录并核验权限关节表现需要登录并核验权限');
        assert.equal(await page.locator('.home-alert').count(), 0);
        assert.equal(await page.getByRole('button', { name: '健康档案', exact: true }).count(), 0);
      }
      if (route.path === '/drafts') {
        const tabs = page.getByRole('tablist', { name: '草稿类别', exact: true });
        assert.deepEqual(await tabs.getByRole('tab').allTextContents(), ['全部', '体温症状', '报告', '治疗', '记录更正']);
        assert.equal(await page.getByRole('heading', { name: '没有可恢复的草稿', exact: true }).count(), 0);
        assert.equal(await page.getByRole('link', { name: '继续编辑', exact: true }).count(), 0);
        assert.equal(await page.getByRole('button', { name: '放弃草稿', exact: true }).count(), 0);
        await tabs.getByRole('tab', { name: '报告', exact: true }).click();
        assert.equal(await tabs.getByRole('tab', { name: '报告', exact: true }).getAttribute('aria-selected'), 'true');
        await page.getByText('恢复草稿时需要注意什么', { exact: true }).click();
        await tabs.getByRole('tab', { name: '记录更正', exact: true }).click();
        assert.equal(await tabs.getByRole('tab', { name: '记录更正', exact: true }).getAttribute('aria-selected'), 'true');
        assert.equal(await page.getByRole('dialog', { name: '放弃草稿', exact: true }).count(), 0);
      }
      if (['UI2-06', 'UI2-10'].includes(route.id)) {
        const tabs = page.getByRole('tablist', { name: '记录详情内容', exact: true });
        assert.deepEqual(await tabs.getByRole('tab').allTextContents(), ['原始内容', '来源与状态', '时间与版本']);
        assert.equal(await page.getByRole('heading', { name: '尚未选择记录', exact: true }).count(), 1);
        for (const label of ['来源与状态', '时间与版本']) {
          await tabs.getByRole('tab', { name: label, exact: true }).click();
          assert.equal(await tabs.getByRole('tab', { name: label, exact: true }).getAttribute('aria-selected'), 'true');
        }
        assert.equal(await page.getByRole('button', { name: '查看原始资料', exact: false }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '历次检验', exact: true }).isDisabled(), true);
        assert.equal(await page.locator('img[src^="/api/"]').count(), 0);
      }
      if (route.id === 'UI2-09') {
        assert.equal(await page.getByRole('heading', { name: '尚无版本记录', exact: true }).count(), 0);
        assert.equal(await page.getByRole('button', { name: '查看当前记录', exact: true }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '查看历史版本', exact: true }).isDisabled(), true);
      }
      if (route.id === 'UI2-18') {
        assert.equal(await page.getByRole('button', { name: '撤回申请', exact: true }).count(), 0);
        assert.equal(await page.getByRole('button', { name: '确认撤回申请', exact: true }).count(), 0);
        assert.equal(await page.getByLabel('撤回说明', { exact: true }).count(), 0);
        assert.equal(await page.getByRole('dialog', { name: '本人申请详情', exact: true }).count(), 0);
        assert.equal(await page.getByRole('heading', { name: '尚无可访问的患儿', exact: true }).count(), 0);
        assert.equal(await page.getByRole('button', { name: '查看关联详情', exact: false }).isDisabled(), true);
        assert.equal(await page.getByRole('button', { name: '选择此患儿', exact: false }).count(), 0);
        await page.getByText('多名监护人如何使用', { exact: true }).click();
        assert.equal(await page.getByText('每名监护人与患儿的关联分别记录。本人获得的访问范围，不会自动授予其他监护人。', { exact: true }).isVisible(), true);
      }
      if (route.id === 'UI2-20') {
        assert.equal(await page.getByText('尚未选择关联', { exact: true }).count(), 1);
        assert.equal(await page.getByText('待核验', { exact: true }).count(), 6);
        assert.equal(await page.getByText('已获准', { exact: true }).count(), 0);
      }
      if (route.id === 'UI2-19') {
        assert.equal(await page.getByLabel('申请人说明', { exact: true }).isDisabled(), true);
        const selector = page.getByLabel('申请权限', { exact: true });
        assert.equal(await selector.isDisabled(), true);
        assert.deepEqual(await selector.locator('option').allTextContents(), ['查看资料', '查看与登记', '按核验结果确定']);
        const submit = page.getByRole('button', { name: '提交关联申请', exact: true });
        assert.equal(await submit.isDisabled(), true);
        assert.equal(await page.getByRole('heading', { name: '申请已经保存', exact: true }).count(), 0);
        const card = await page.locator('form .card').boundingBox(), button = await submit.boundingBox();
        assert.ok(card && button);
        assert.equal(button.y - (card.y + card.height), 16);
      }
      if (route.id === 'UI2-13') {
        assert.equal(await page.getByLabel('开始日期', { exact: true }).isDisabled(), true);
        assert.equal(await page.getByLabel('结束日期', { exact: true }).isDisabled(), true);
        assert.deepEqual(await page.locator('form .checkbox-row').allTextContents(), ['体温与可观察症状', '检验原值与单位', '实际治疗记录', '来源与核对状态']);
        for (const label of ['体温与可观察症状', '检验原值与单位', '实际治疗记录', '来源与核对状态']) {
          assert.equal(await page.getByLabel(label, { exact: true }).isChecked(), true);
          assert.equal(await page.getByLabel(label, { exact: true }).isDisabled(), true);
        }
        assert.equal(await page.getByRole('button', { name: '生成就医摘要', exact: true }).isDisabled(), true);
        assert.equal(await page.getByText('尚无已生成的摘要', { exact: true }).count(), 0);
        assert.equal(await page.getByRole('heading', { name: '已经保存结果', exact: true }).count(), 0);
      }
      if (route.id === 'UI2-14') {
        assert.deepEqual(await page.locator('.summary-paper > .summary-section h3').allTextContents(), ['体温与症状', '检验报告', '治疗资料', '资料来源与缺项']);
        assert.equal(await page.getByText('尚无当前可见资料', { exact: true }).count(), 4);
        assert.equal(await page.getByRole('link', { name: '查看摘要依据版本', exact: true }).count(), 0);
        assert.equal(await page.getByText('尚未生成有效内容', { exact: true }).count(), 1);
      }
      checks.push({ name: '真实匿名身份与家庭容器', route: route.path, screen_id: route.id, viewport_width: width, geometry, result: 'PASS' });
    }
    process.stderr.write(JSON.stringify({ phase: 'VIEWPORT_CHECKED', viewport_width: width, checks: checks.length, elapsed_ms: performance.now() - startedAt }) + '\n');
  }
  await page.goto(`${origin}/account`);
  process.stderr.write(JSON.stringify({ phase: 'INTERACTION_STARTED', checks: checks.length, elapsed_ms: performance.now() - startedAt }) + '\n');
  await page.getByRole('heading', { name: '管理我的资料', exact: true }).waitFor();
  const navigation = page.getByRole('navigation', { name: '家庭主导航' });
  assert.deepEqual(await navigation.getByRole('button').allTextContents(), ['首页', '记录', '我的']);
  assert.equal(await navigation.getByRole('button', { name: '我的', exact: true }).getAttribute('aria-current'), 'page');
  assert.equal(await page.getByRole('button', { name: '退出登录', exact: true }).count(), 0);
  const explanation = page.getByRole('button', { name: '页面说明', exact: true });
  await explanation.click();
  const sheet = page.getByRole('dialog', { name: '页面说明', exact: true });
  await sheet.waitFor();
  assert.equal(await page.locator('.phone-scroll').getAttribute('inert'), '');
  assert.equal(await navigation.getAttribute('inert'), '');
  await page.keyboard.press('Shift+Tab');
  assert.equal(await sheet.getByRole('button', { name: '继续查看', exact: true }).evaluate(node => node.getRootNode().activeElement === node), true);
  await page.keyboard.press('Tab');
  assert.equal(await sheet.getByRole('button', { name: '关闭说明', exact: true }).evaluate(node => node.getRootNode().activeElement === node), true);
  await page.keyboard.press('Escape'); await sheet.waitFor({ state: 'detached' });
  await page.waitForFunction(node => node.getRootNode().activeElement === node, await explanation.elementHandle(), { timeout: 15000 });
  assert.equal(await navigation.getAttribute('inert'), null);
  checks.push({ name: '说明弹层隔离背景并维护实际键盘焦点', result: 'PASS' });
  process.stderr.write(JSON.stringify({ phase: 'EXPLANATION_CHECKED', checks: checks.length, elapsed_ms: performance.now() - startedAt }) + '\n');
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await page.waitForURL(`${origin}/login`);
  process.stderr.write(JSON.stringify({ phase: 'LOGIN_NAVIGATED', checks: checks.length, elapsed_ms: performance.now() - startedAt }) + '\n');
  await page.getByRole('textbox', { name: '账号', exact: true }).waitFor();
  const writesBefore = requests.filter(request => request.method !== 'GET').length;
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  assert.equal(await page.locator('form').evaluate(form => form.checkValidity()), false);
  assert.equal(requests.filter(request => request.method !== 'GET').length, writesBefore);
  assert.equal(await page.getByLabel('密码', { exact: true }).getAttribute('autocomplete'), 'current-password');
  await page.getByRole('button', { name: '没有账号，去注册', exact: true }).click();
  assert.equal(await page.getByLabel('密码', { exact: true }).getAttribute('autocomplete'), 'new-password');
  assert.equal(await page.getByLabel('密码', { exact: true }).getAttribute('minlength'), '8');
  await page.getByRole('button', { name: '注册并登录', exact: true }).click();
  assert.equal(await page.locator('form').evaluate(form => form.checkValidity()), false);
  assert.equal(requests.filter(request => request.method !== 'GET').length, writesBefore);
  await page.reload();
  await page.getByRole('textbox', { name: '账号', exact: true }).waitFor();
  process.stderr.write(JSON.stringify({ phase: 'LOGIN_RELOADED', checks: checks.length, elapsed_ms: performance.now() - startedAt }) + '\n');
  assert.equal(await page.getByRole('textbox', { name: '账号', exact: true }).inputValue(), '');
  assert.equal(await page.getByLabel('密码', { exact: true }).inputValue(), '');
  checks.push({ name: '登录与注册原生空输入拒绝、模式及刷新凭据状态', result: 'PASS', credentials_submitted: false });
  await page.getByRole('button', { name: '返回上级页面', exact: true }).click();
  await page.waitForURL(`${origin}/account`);
  process.stderr.write(JSON.stringify({ phase: 'ACCOUNT_RETURNED', checks: checks.length, elapsed_ms: performance.now() - startedAt }) + '\n');
  await page.getByRole('button', { name: '资料补充提醒', exact: false }).click();
  await page.waitForURL(`${origin}/account/reminders`);
  await page.getByRole('heading', { name: '资料补充提醒', level: 1, exact: true }).waitFor();
  assert.equal(await page.getByRole('switch', { name: '启用资料补充提醒', exact: true }).isDisabled(), true);
  checks.push({ name: '提醒入口连接准确页面并核验当前匿名操作权限', result: 'PASS', reminder_business_validation: 'NOT_RUN' });
  await page.goto(`${origin}/summaries`);
  await page.getByRole('heading', { name: '准备就医摘要', exact: true }).waitFor();
  await page.getByRole('link', { name: '查看摘要页面结构', exact: true }).click();
  await page.waitForURL(`${origin}/summaries/structure`);
  await page.getByRole('heading', { name: '就医摘要', level: 1, exact: true }).waitFor();
  assert.equal(await page.getByText('尚未生成有效内容', { exact: true }).count(), 1);
  await page.getByRole('button', { name: '重新选择范围', exact: true }).click();
  await page.waitForURL(`${origin}/summaries`);
  checks.push({ name: '摘要结构入口和范围返回使用实际路由，匿名状态不生成资料', result: 'PASS', generation_business_validation: 'NOT_RUN' });
  await page.goto(`${origin}/archives/confirm`);
  await page.locator('[data-screen-id="REPORT-CONFIRMATION"]').waitFor();
  assert.equal(await page.locator('[data-screen-id="UI2-27"]').count(), 0);
  assert.equal(await page.getByLabel('报告记载的采样时间', { exact: true }).inputValue(), '');
  assert.equal(await page.getByLabel('报告记载的采样时间', { exact: true }).isDisabled(), true);
  assert.equal(await page.getByLabel('报告出具时间', { exact: true }).inputValue(), '');
  for (const select of await page.getByLabel('时间精度', { exact: true }).all()) {
    assert.equal(await select.inputValue(), 'unknown'); assert.equal(await select.isDisabled(), true);
  }
  assert.equal(await page.getByRole('button', { name: '确认并保存', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('button', { name: '保存当前草稿', exact: true }).isDisabled(), true);
  assert.equal(await page.locator('#recognition-candidates .card').count(), 0);
  await page.getByRole('button', { name: '返回上级页面', exact: true }).click();
  await page.waitForURL(`${origin}/register/reports`);
  await page.locator('[data-screen-id="UI2-03"]').waitFor();
  checks.push({ name: '逐项确认的正式路径保留实际报告流程，匿名时间未知且禁止提交', result: 'PASS', full_ui2_27_validation: 'NOT_RUN', business_payload_submitted: false });
  await page.getByRole('button', { name: '添加长期资料 多次住院与门诊可分批整理', exact: true }).click();
  await page.waitForURL(`${origin}/archives/batches/new`);
  await page.getByRole('heading', { name: '这个页面暂不可用', exact: true }).waitFor();
  checks.push({ name: '批次创建路径尚待业务接入时准确呈现当前页面状态', result: 'PASS', batch_business_validation: 'NOT_RUN' });
  process.stderr.write(JSON.stringify({ phase: 'NAVIGATION_CHECKED', checks: checks.length, elapsed_ms: performance.now() - startedAt }) + '\n');
  assert.deepEqual(pageErrors, []);
  assert.equal(requests.some(request => new URL(request.url).origin !== origin), false);
  assert.equal(requests.some(request => request.method !== 'GET'), false);
  assert.equal(apiResponses.filter(response => response.path === '/api/session').every(response => response.status === 401), true);
  const loadedAssets = await Promise.all(assets);
  assert.ok(loadedAssets.some(asset => asset.sha256 === logoHash));
  assert.ok(loadedAssets.some(asset => asset.sha256 === childHash));
  assert.equal(apiResponses.some(response => response.path.includes('/patients/') || response.path.includes('/alerts/') || response.path.includes('/records/') || response.path.includes('/record-versions/') || response.path.includes('/files/')), false);
  const protectedReads = [];
  for (const target of [
    `/api/records/${existing.record_id}`, `/api/records/${existing.record_id}/history`,
    `/api/record-versions/${existing.record_version_id}`,
    `/api/record-versions/${existing.record_version_id}/fields/${existing.field_id}/source`,
    `/api/files/${existing.file_id}/content`,
  ]) {
    const response = await context.request.get(`${origin}${target}`);
    assert.equal(response.status(), 401);
    assert.ok(response.headers()['cache-control'].includes('no-store'));
    assert.ok(response.headers()['cache-control'].includes('private'));
    assert.equal((await response.json()).code, 'AUTH_REQUIRED');
    protectedReads.push({ path: target, status: response.status(), cache_control: response.headers()['cache-control'], result: 'PASS' });
  }
  checks.push({ name: '实际记录、历史、字段来源及原件接口拒绝匿名读取', result: 'PASS', protected_reads: protectedReads });
  const originalMetadata = await context.request.get(`${origin}/api/record-versions/${existing.record_version_id}/originals`);
  assert.equal(originalMetadata.status(), 401);
  assert.equal((await originalMetadata.json()).code, 'AUTH_REQUIRED');
  assert.ok(originalMetadata.headers()['cache-control'].includes('private'));
  assert.ok(originalMetadata.headers()['cache-control'].includes('no-store'));
  checks.push({ name: '准确记录版本的原件元数据拒绝匿名读取', result: 'PASS', original_bytes_read: false, authenticated_business_validation: 'NOT_RUN' });
  const analyteResponse = await context.request.post(`${origin}/api/patients/${existing.patient_id}/analytes/${encodeURIComponent(existing.analyte_id)}/search`, { data: {} });
  assert.equal(analyteResponse.status(), 401);
  assert.ok(analyteResponse.headers()['cache-control'].includes('no-store'));
  assert.ok(analyteResponse.headers()['cache-control'].includes('private'));
  assert.equal((await analyteResponse.json()).code, 'AUTH_REQUIRED');
  checks.push({ name: '已有患儿与项目的实际历次检验接口拒绝匿名读取', result: 'PASS', authenticated_analyte_validation: 'NOT_RUN' });
  const protectedRelations = [];
  const relationPaths = ['/api/relations', `/api/relations/${relations.relation_id}`, '/api/relation-applications', '/api/requests', `/api/requests/${relations.intent_id}`];
  if (relations.application_id) relationPaths.push(`/api/relation-applications/${relations.application_id}`);
  for (const target of relationPaths) {
    const response = await context.request.get(`${origin}${target}`);
    assert.equal(response.status(), 401);
    assert.ok(response.headers()['cache-control'].includes('no-store'));
    assert.ok(response.headers()['cache-control'].includes('private'));
    assert.equal((await response.json()).code, 'AUTH_REQUIRED');
    protectedRelations.push({ path: target, status: response.status(), cache_control: response.headers()['cache-control'], result: 'PASS' });
  }
  checks.push({ name: '本人关联及申请接口拒绝匿名读取', result: 'PASS', protected_reads: protectedRelations });
  const protectedSummaries = [];
  for (const target of [`/api/patients/${summary.patient_id}/summaries`, `/api/summaries/${summary.summary_id}`, `/api/tasks/${summary.task_id}`,
    `/api/patients/${summary.patient_id}/requests?operation_id=summary_create`]) {
    const response = await context.request.get(`${origin}${target}`);
    assert.equal(response.status(), 401);
    assert.ok(response.headers()['cache-control'].includes('no-store'));
    assert.ok(response.headers()['cache-control'].includes('private'));
    assert.equal((await response.json()).code, 'AUTH_REQUIRED');
    protectedSummaries.push({ path: target, status: response.status(), cache_control: response.headers()['cache-control'], result: 'PASS' });
  }
  checks.push({ name: '已有摘要、任务及原请求目录拒绝匿名读取', result: 'PASS', protected_reads: protectedSummaries });
  const protectedWrites = [];
  for (const target of ['/api/write-intents', '/api/relation-applications', `/api/relation-applications/${relations.application_id}/withdraw`, `/api/write-intents/${relations.intent_id}/retry`, `/api/patients/${summary.patient_id}/summaries`, `/api/records/${existing.record_id}/corrections`]) {
    const response = await context.request.post(`${origin}${target}`, { data: {} });
    assert.equal(response.status(), 401);
    assert.ok(response.headers()['cache-control'].includes('no-store'));
    assert.ok(response.headers()['cache-control'].includes('private'));
    assert.equal((await response.json()).code, 'AUTH_REQUIRED');
    protectedWrites.push({ path: target, status: response.status(), cache_control: response.headers()['cache-control'], result: 'PASS' });
  }
  checks.push({ name: '实际匿名请求无法准备、提交、撤回、重试申请或更正记录', result: 'PASS', protected_writes: protectedWrites, business_payload_submitted: false });
  assert.equal(timePayload(emptyTime), null);
  assert.equal(timePayload(timeInputValue(emptyTime, { value: '', valueAsDate: null }, true)), null);
  for (const saved of savedDates.values) {
    const native = await page.evaluate(value => {
      const input = document.createElement('input'); input.type = 'date'; input.value = value.local_date;
      const date = input.valueAsDate;
      if (!date) throw new Error('浏览器未确认已有资料的保存日期');
      return { value: input.value, timestamp: date.getTime() };
    }, saved);
    const confirmed = timeInputValue(emptyTime, { value: native.value, valueAsDate: new Date(native.timestamp) }, true);
    const payload = timePayload(confirmed);
    assert.equal(confirmed.precision, 'date');
    assert.equal(payload.local_date, saved.local_date); assert.equal(payload.raw_text, saved.local_date);
    assert.equal(payload.local_datetime, null); assert.equal(payload.timezone_name, null);
    assert.equal(validTime(payload), true, JSON.stringify(validTime.errors));
  }
  for (const saved of savedDates.minute_values) {
    const native = await page.evaluate(value => {
      const input = document.createElement('input'); input.type = 'datetime-local'; input.value = value.local_datetime;
      if (!input.value) throw new Error('浏览器未确认已有资料的准确时间');
      return { value: input.value };
    }, saved);
    assert.equal(native.value, saved.local_datetime);
    const confirmed = timeInputValue({ precision: 'minute', value: saved.local_datetime, timezone: saved.timezone ?? '' }, { value: native.value, valueAsDate: null });
    const payload = timePayload(confirmed);
    assert.equal(payload.precision, 'minute'); assert.equal(payload.local_datetime, saved.local_datetime);
    assert.equal(payload.instant, null); assert.equal(payload.utc_offset_minutes, null);
    assert.equal(validTime(payload), true, JSON.stringify(validTime.errors));
  }
  checks.push({ name: '已有医学时间通过实际输入与序列化，空白时间保持未知', result: 'PASS',
    actual_saved_dates: savedDates.values.length, actual_saved_minutes: savedDates.minute_values.length,
    saved_minute_validation: savedDates.minute_values.length ? 'PASS' : 'NOT_RUN',
    clinical_data_created: false, business_payload_submitted: false });
  for (const saved of savedDates.values) {
    const month = await page.evaluate(value => {
      const dateInput = document.createElement('input'); dateInput.type = 'date'; dateInput.value = value.local_date;
      const date = dateInput.valueAsDate;
      if (!date) throw new Error('浏览器未确认当前保存日期');
      const year = date.getUTCFullYear(), month = date.getUTCMonth() + 1;
      const monthInput = document.createElement('input'); monthInput.type = 'month';
      monthInput.value = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
      const confirmed = monthInput.valueAsDate;
      if (!confirmed) throw new Error('浏览器未确认原生月份输入');
      return { precision: 'month', value: monthInput.value, timezone: value.timezone ?? '',
        localYear: confirmed.getUTCFullYear(), localMonth: confirmed.getUTCMonth() + 1 };
    }, saved);
    const payload = timePayload(month);
    assert.equal(validTime(payload), true, JSON.stringify(validTime.errors));
    assert.equal(payload.local_year, month.localYear); assert.equal(payload.local_month, month.localMonth);
    assert.equal(payload.raw_text, month.value);
    for (const key of ['local_date', 'local_datetime', 'instant', 'timezone_name', 'utc_offset_minutes', 'end_year', 'end_month', 'relative_text']) assert.equal(payload[key], null);
    assert.throws(() => timePayload({ ...month, value: '' }), /请填写/);
  }
  checks.push({ name: '已有字段日期经原生月份控件取得年月，实际序列化通过医学时间 Schema', result: 'PASS',
    existing_date_fields: savedDates.values.length, business_payload_submitted: false, month_write_validation: 'NOT_RUN' });
  for (const saved of savedSources.values) {
    const field = reportFieldValue({ ...saved, sampleTime: saved.sampleTime ?? undefined });
    assert.equal(field.source_text, saved.source_text);
    assert.equal(field.raw_text, saved.value === null || String(saved.value).trim() === '' ? null : String(saved.value));
    if (field.raw_numeric !== null) assert.equal(field.raw_numeric, saved.value);
    if (saved.sampleTime) assert.equal(validTime(field.medical_time), true, JSON.stringify(validTime.errors));
    const unknown = reportFieldValue({ ...saved, sampleTime: undefined });
    assert.equal(unknown.medical_time, null); assert.equal(unknown.sample_time_origin, 'unknown');
    assert.throws(() => reportFieldValue({ ...saved, source_text: undefined }), /缺少对应原文/);
  }
  checks.push({ name: '已有来源字段的原值、原文及各自时间通过实际报告序列化，缺少原文准确拒绝', result: 'PASS',
    existing_source_fields: savedSources.values.length, source_guard_checks: savedSources.source_guard_checks, numeric_checks: savedSources.numeric_checks,
    candidate_schema_version: savedSources.candidate_schema_version,
    report_schema_compiled: true, business_payload_submitted: false, report_recognition_validation: 'NOT_RUN', report_creation_validation: 'NOT_RUN' });
  const report = { checked_at: new Date().toISOString(), status: 'PASS', scope: 'PARTIAL_FAMILY_ANONYMOUS_DOM',
    adopted_family_screens: routes.map(route => route.id), required_screen_count: 39, required_state_count: 328,
    checks, api_responses: apiResponses, page_errors: pageErrors, loaded_assets: loadedAssets,
    token_sha256: digest(await readFile('../ui-delivery/v2/tokens.json')), stylesheet_sha256: digest(await readFile('../ui-delivery/v2/src/styles.css')),
    provider_called: false, clinical_data_created: false, credentials_submitted: false,
    authenticated_business_validation: 'NOT_RUN', full_ui2_validation: 'NOT_RUN', visual_validation: 'NOT_RUN',
    therapy_schema_compiled: true, therapy_recognition_validation: 'NOT_RUN', therapy_creation_validation: 'NOT_RUN',
    font_200_percent_validation: 'NOT_RUN', mobile_keyboard_validation: 'NOT_RUN', device_validation: 'NOT_RUN', wechat_validation: 'NOT_RUN' };
  await writeFile('../.planning-work/t54-browser.json', JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify({ status: report.status, scope: report.scope, checks: checks.length, adopted_family_screens: report.adopted_family_screens,
    visual_validation: report.visual_validation, authenticated_business_validation: report.authenticated_business_validation }) + '\n');
} finally {
  process.stderr.write(JSON.stringify({ phase: 'CONTEXT_RELEASE_START', elapsed_ms: performance.now() - startedAt, resources: process.getActiveResourcesInfo() }) + '\n');
  await context.request.dispose();
  await context.close();
  process.stderr.write(JSON.stringify({ phase: 'BROWSER_RELEASE_START', elapsed_ms: performance.now() - startedAt, resources: process.getActiveResourcesInfo() }) + '\n');
  await browser.close();
  process.stderr.write(JSON.stringify({ phase: 'RESOURCES_RELEASED', elapsed_ms: performance.now() - startedAt, resources: process.getActiveResourcesInfo() }) + '\n');
}
