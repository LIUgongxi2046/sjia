import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';
import { chromium } from 'playwright';

const names = ['origin', 'credentials', 'scope', 'batches', 'manifest', 'index', 'report'];
const { values } = parseArgs({ options: Object.fromEntries(names.map(name => [name, { type: 'string' }])) });
for (const name of names) assert.ok(values[name], `缺少参数：${name}`);
const origin = new URL(values.origin).origin;
assert.equal(new URL(origin).hostname, '127.0.0.1');
const credentials = JSON.parse(await readFile(values.credentials, 'utf8'));
const scope = JSON.parse(await readFile(values.scope, 'utf8'));
const prior = JSON.parse(await readFile(values.batches, 'utf8'));
const manifestBytes = await readFile(values.manifest);
const manifest = JSON.parse(manifestBytes);
assert.equal(scope.result, 'PASS');
assert.equal(scope.source_kind, 'USER_APPROVED_ORIGINAL');
assert.equal(prior.result, 'PASS');
assert.equal(manifest.original_attachment_comparison, 'PASS');
assert.equal(createHash('sha256').update(manifestBytes).digest('hex'), prior.source_manifest_sha256);
assert.equal(manifest.sources.length, 63);
const allowedChecksums = new Set(manifest.sources.map(item => item.sha256));
const patients = [...new Set(prior.batches.map(item => item.patient_id))];
assert.equal(patients.length, 5);
assert.equal(prior.batches.length, 5);
const report = { status: 'IN_PROGRESS', started_at: new Date().toISOString(), origin,
  checks: [], groups: [], responses: [], page_errors: [], new_model_calls: 0, clinical_writes: 0,
  visual_validation: 'NOT_RUN', device_validation: 'NOT_RUN' };
await writeFile(values.report, JSON.stringify(report, null, 2), { flag: 'wx', mode: 0o600 });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await context.newPage();
page.setDefaultTimeout(30000);
page.on('pageerror', error => report.page_errors.push(error.message));
page.on('response', response => {
  const url = new URL(response.url());
  if (url.pathname.startsWith('/api/')) report.responses.push({ path: url.pathname,
    method: response.request().method(), status: response.status() });
});
async function save() { await writeFile(values.report, JSON.stringify(report, null, 2)); }
async function api(method, path, data, csrf) {
  const response = await context.request.fetch(origin + path, { method, data,
    headers: { Origin: origin, ...(csrf ? { 'X-CSRFToken': csrf } : {}) }, timeout: 60000 });
  assert.ok(response.ok(), `${method} ${path}: ${response.status()}`);
  return response.json();
}
function responseAt(path, method = 'GET') {
  return page.waitForResponse(response => new URL(response.url()).pathname === path
    && response.request().method() === method && response.status() === 200, { timeout: 60000 });
}
async function settled(screen) {
  await page.waitForFunction(screenId => {
    const root = document.querySelector(`[data-screen-id="${screenId}"]`)?.shadowRoot;
    return Boolean(root && !root.querySelector('.status-panel.loading'));
  }, screen);
}
async function openPatientPicker() {
  const picker = page.locator('details.home-patient');
  await picker.waitFor();
  if (!await picker.evaluate(element => element.open)) await picker.locator('summary').click();
  await picker.getByRole('combobox').waitFor();
  assert.equal(await picker.getByRole('combobox').count(), 1);
}
try {
  const index = await context.request.get(origin + '/home');
  assert.equal(index.status(), 200);
  const indexBytes = await index.body();
  assert.deepEqual(indexBytes, await readFile(values.index));
  report.index_sha256 = createHash('sha256').update(indexBytes).digest('hex');
  await page.goto(origin + '/login', { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('账号', { exact: true }).fill(credentials.username);
  await page.locator('input[type="password"]').fill(credentials.password);
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.waitForURL(url => url.pathname === '/home');
  const session = await api('GET', '/api/session');
  assert.equal(session.principal_id, scope.actor_id);
  await openPatientPicker();
  const initialPatient = await page.locator('details.home-patient').getByRole('combobox').inputValue();
  assert.ok(patients.includes(initialPatient));
  const relations = (await api('GET', '/api/relations')).relations;
  assert.ok(patients.every(id => relations.some(item => item.patient_id === id && item.status === 'active' && item.actions.includes('read_raw'))));
  const liveBatches = [];
  for (const reference of prior.batches) {
    const batch = await api('GET', `/api/v2/archive-batches/${reference.batch_id}`);
    assert.equal(batch.patient_id, reference.patient_id);
    assert.equal(batch.batch_id, reference.batch_id);
    assert.ok(batch.pages.every(item => item.batch_id === batch.batch_id && allowedChecksums.has(item.checksum)));
    liveBatches.push(batch);
  }
  assert.equal(liveBatches.reduce((total, batch) => total + batch.pages.length, 0), 290);
  const initial = patients.indexOf(initialPatient);
  const order = [...patients.slice(initial + 1), ...patients.slice(0, initial + 1)];
  let filteredGroups = 0, sourceNavigations = 0;
  for (const patientId of order) {
    const savedSelection = responseAt('/api/patient-selection', 'POST');
    const homeRead = responseAt(`/api/patients/${patientId}/home`);
    await page.locator('details.home-patient').getByRole('combobox').selectOption(patientId);
    assert.equal((await savedSelection).request().postDataJSON().patient_id, patientId);
    await homeRead;
    await settled('UI2-01');
    await openPatientPicker();
    assert.equal(await page.locator('details.home-patient').getByRole('combobox').inputValue(), patientId);
    assert.equal(await page.locator('details.home-patient').getByRole('combobox').isEnabled(), true);
    assert.equal((await api('GET', '/api/session')).selected_patient_id, patientId);
    const group = { patient_id: patientId, checks: [] };
    report.groups.push(group);
    group.checks.push('实际选择、会话、关联及首页采用同一资料分组');
    const recordsRead = responseAt(`/api/patients/${patientId}/records/search`, 'POST');
    await page.getByRole('button', { name: '记录', exact: true }).click();
    const records = await (await recordsRead).json();
    assert.ok(records.records.every(item => item.detail.patient_id === patientId));
    await settled('UI2-05');
    const recordIds = await page.locator('.rich-record-card a').evaluateAll(elements => elements.map(element => new URL(element.href).pathname.split('/').at(-1)));
    assert.deepEqual(recordIds.sort(), records.records.map(item => item.record_id).sort());
    group.record_count = records.records.length;
    group.checks.push('记录页面只采用当前接口返回的准确记录身份');
    const archiveRead = responseAt(`/api/v2/patients/${patientId}/archives/search`, 'POST');
    await page.getByRole('button', { name: /^长期档案\s*按医院与就诊查找$/ }).click();
    const directory = await (await archiveRead).json();
    assert.equal(directory.patient_id, patientId);
    assert.ok(directory.items.every(item => item.document.patient_id === patientId));
    await settled('UI2-25');
    assert.equal(await page.getByRole('searchbox', { name: '查找文书', exact: true }).inputValue(), '');
    await page.getByText(`符合条件 ${directory.matched_count} 份文书；本次读取 ${directory.items.length} 份。`, { exact: true }).waitFor();
    group.document_count = directory.matched_count;
    group.checks.push('切换分组后档案范围准确且原查询条件已清理');
    const textDocument = directory.items.find(item => item.document.confirmed_text?.trim());
    if (textDocument) {
      const documentId = textDocument.document.document_id;
      const word = [...new Intl.Segmenter('zh-CN', { granularity: 'word' }).segment(textDocument.document.confirmed_text)]
        .find(item => item.isWordLike && item.segment.length > 1);
      assert.ok(word && word.segment.length <= 200);
      const term = word.segment;
      const filtered = responseAt(`/api/v2/patients/${patientId}/archives/search`, 'POST');
      const search = page.getByRole('searchbox', { name: '查找文书', exact: true });
      await search.fill(term);
      assert.equal(await search.inputValue(), term);
      await search.press('Enter');
      const filteredResponse = await filtered;
      assert.equal(filteredResponse.request().postDataJSON().query, term);
      const result = await filteredResponse.json();
      assert.ok(result.items.some(item => item.document.document_id === documentId));
      await settled('UI2-25');
      const sourceRead = responseAt(`/api/v2/patients/${patientId}/archives/search`, 'POST');
      await page.getByRole('link', { name: '定位原文依据', exact: true }).and(page.locator(`a[href*="document=${documentId}"]`)).click();
      const sourceResponse = await sourceRead;
      assert.equal(sourceResponse.request().postDataJSON().document_id, documentId);
      const source = await sourceResponse.json();
      assert.equal(source.items.length, 1);
      assert.equal(source.items[0].document.document_id, documentId);
      assert.equal(source.items[0].document.version_id, textDocument.document.version_id);
      await page.getByRole('heading', { name: '已保存原始文书', exact: true }).waitFor();
      const restored = responseAt(`/api/v2/patients/${patientId}/archives/search`, 'POST');
      await page.getByRole('button', { name: '返回上级页面', exact: true }).click();
      await restored;
      await settled('UI2-25');
      assert.equal(await page.getByRole('searchbox', { name: '查找文书', exact: true }).inputValue(), term);
      group.checks.push('真实确认原文查询、准确文书版本定位及返回查询条件保持');
      filteredGroups += 1; sourceNavigations += 1;
    }
    const batch = liveBatches.find(item => item.patient_id === patientId);
    assert.ok(batch);
    const batchesRead = responseAt(`/api/v2/patients/${patientId}/archive-batches/search`, 'POST');
    await page.getByRole('button', { name: /^添加一批资料\s*原始资料直接整理$/ }).click();
    const batchList = await (await batchesRead).json();
    const batchIndex = batchList.items.findIndex(item => item.batch_id === batch.batch_id);
    assert.ok(batchIndex >= 0);
    assert.equal(new Set(batchList.items.map(item => item.batch_id)).size, batchList.items.length);
    const chooser = page.locator('details').filter({ has: page.locator('summary').filter({ hasText: /^选择已经接收的批次$/ }) });
    await chooser.locator('summary').click();
    const rows = chooser.locator('button.list-row');
    assert.equal(await rows.count(), batchList.items.length);
    const batchRead = responseAt(`/api/v2/archive-batches/${batch.batch_id}`);
    await rows.nth(batchIndex).click();
    const savedBatch = await (await batchRead).json();
    assert.equal(savedBatch.patient_id, patientId);
    assert.equal(savedBatch.batch_id, batch.batch_id);
    await settled('UI2-26');
    assert.equal(await page.locator('summary').filter({ hasText: /^页面 \d+ · / }).count(), batch.pages.length);
    group.page_count = batch.pages.length;
    group.visible_batch_count = batchList.items.length;
    group.checks.push('通过实际批次目录打开同组准确批次及全部原页状态');
    await page.getByRole('button', { name: '返回上级页面', exact: true }).click();
    await page.getByRole('heading', { name: '长期档案', exact: true }).waitFor();
    await page.getByRole('button', { name: '返回上级页面', exact: true }).click();
    await page.getByRole('heading', { name: '记录', exact: true }).waitFor();
    await page.getByRole('button', { name: '首页', exact: true }).click();
    await openPatientPicker();
    assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0);
    group.checks.push('实际返回与首页切换入口保持，私人浏览器存储为空');
    await save();
    process.stdout.write(JSON.stringify({ checked_groups: report.groups.length, checks: group.checks.length }) + '\n');
  }
  assert.equal((await api('GET', '/api/session')).selected_patient_id, initialPatient);
  assert.ok(filteredGroups > 0 && sourceNavigations > 0);
  assert.equal(report.page_errors.length, 0);
  assert.ok(report.responses.every(item => item.method === 'GET' || item.path === '/api/auth/login'
    || item.path === '/api/patient-selection' || /\/(records|archives|archive-batches)\/search$/.test(item.path)));
  report.status = 'PASS';
  report.checked_groups = report.groups.length;
  report.page_count = report.groups.reduce((total, group) => total + group.page_count, 0);
  report.source_navigations = sourceNavigations;
  report.filtered_groups = filteredGroups;
  report.browser_version = browser.version();
  process.stdout.write(JSON.stringify({ status: report.status, groups: report.checked_groups,
    pages: report.page_count, source_navigations: sourceNavigations, new_model_calls: 0, clinical_writes: 0 }) + '\n');
} finally {
  if (report.status !== 'PASS') report.status = 'FAIL';
  report.finished_at = new Date().toISOString();
  await save();
  await browser.close();
}
