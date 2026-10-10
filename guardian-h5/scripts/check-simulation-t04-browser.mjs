import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium, expect as baseExpect } from 'playwright/test';

process.loadEnvFile('../.simulation-work/environment.env');
const fixture = JSON.parse(await readFile('../.planning-work/t04-field-fixture.json', 'utf8'));
const sourcesOnly = process.argv.includes('--sources-only');
assert.equal(fixture.mode, 'SIMULATION');
const expect = baseExpect.configure({ timeout: 30000 });
const checks = [], responses = [], errors = [], savedRecords = [];
const origin = 'http://127.0.0.1:5183', marker = 'SIMULATION T04 ' + randomUUID();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const family = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await family.newPage();
let complete = false;
function check(name) { checks.push(name); process.stdout.write('PASS ' + name + '\n'); }
function observe(target) {
  target.setDefaultTimeout(30000); target.setDefaultNavigationTimeout(30000);
  target.on('pageerror', value => errors.push(value.message));
  target.on('dialog', value => value.accept());
  target.on('response', value => { const path = new URL(value.url()).pathname;
    if (path.startsWith('/api/')) responses.push({ method: value.request().method(), path, status: value.status() }); });
}
observe(page);
async function login(target, support = false) {
  await target.goto(origin + (support ? '/support/login' : '/login'));
  await target.getByLabel('账号', { exact: true }).fill(support ? 'simulation-support' : 'simulation-family');
  await target.getByLabel('密码', { exact: true }).fill(process.env.SJIA_SIMULATION_PASSWORD);
  await target.getByRole('button', { name: '登录账号', exact: true }).click();
  await target.waitForURL(url => url.pathname === (support ? '/support/data-use' : '/home'));
}
async function saveRecord(button, kind) {
  const response = page.waitForResponse(value => value.request().method() === 'POST'
    && new URL(value.url()).pathname === `/api/patients/${fixture.patient_id}/records`);
  await expect(button).toBeEnabled(); await button.click();
  const actual = await response; assert.equal(actual.status(), 201, await actual.text());
  const value = await actual.json(); savedRecords.push(value);
  await page.waitForURL(url => url.pathname === '/records');
  const read = await family.request.get(origin + '/api/records/' + value.record_id);
  assert.equal(read.status(), 200); const result = await read.json();
  assert.equal(result.kind, kind); assert.equal(result.record_version_id, value.record_version_id);
  await page.reload(); await page.waitForLoadState('networkidle');
  return result;
}
try {
  if (!sourcesOnly) {
  await login(page);
  await page.goto(origin + '/register/observations');
  await expect(page.getByLabel('体温原值', { exact: true })).toBeEnabled();
  await page.getByLabel('体温原值', { exact: true }).fill('38.0');
  await page.getByText('测量方式与补充信息', { exact: true }).click();
  await page.getByLabel('体温单位', { exact: true }).selectOption('摄氏度（°C）');
  await page.getByRole('textbox', { name: '补充说明', exact: true }).fill(marker);
  const observation = await saveRecord(page.getByRole('button', { name: '确认并保存', exact: true }), 'observation');
  assert.deepEqual(observation.fields.map(value => value.field_code), ['temp', 'observation_note']);
  assert.equal(observation.fields[0].raw_numeric, '38.0'); assert.equal(observation.fields[1].raw_text, marker);
  check('本人观察表单采用准确字段配置，保存两项并重新读取及刷新');
  await page.goto(origin + '/register/reports');
  await page.getByRole('button', { name: '逐项确认内容 每个项目保留自己的采样时间', exact: true }).click();
  await page.getByRole('button', { name: '手动添加检验项目', exact: true }).click();
  await page.getByLabel('原项目名称', { exact: true }).fill(marker + ' 原项目名称');
  await page.getByLabel('原始数值', { exact: true }).fill('3.20');
  await page.getByLabel('原始单位', { exact: true }).fill('SIM/unit');
  await page.getByText('确认项目身份', { exact: true }).click();
  await page.getByRole('searchbox', { name: '查找获准检验项目', exact: true }).fill('ＳＩＭ ＰＬＴ');
  await page.getByRole('button', { name: '查询获准字典', exact: true }).click();
  await expect(page.getByText('存在多个匹配项目，请核对定义后本人选择。', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '模拟血小板 · SIM PLT', exact: true }).click();
  await page.getByRole('checkbox', { name: '已对照原图逐项核对', exact: true }).check();
  const report = await saveRecord(page.getByRole('button', { name: '确认并保存', exact: true }), 'report');
  assert.equal(report.fields[0].original_name, marker + ' 原项目名称');
  assert.equal(report.fields[0].concept_code, 'simulation_platelet'); assert.ok(report.fields[0].dictionary_entry_id);
  assert.equal(report.fields[0].raw_text, '3.20'); assert.equal(report.fields[0].raw_numeric, '3.20');
  assert.equal(report.fields[0].raw_unit, 'SIM/unit'); assert.equal(report.fields[0].medical_time, null);
  check('报告实际查询 Unicode 别名，由本人确认歧义项目，原文原值及独立未知时间保存重读');
  await page.goto(origin + '/register/therapies');
  await expect(page.getByLabel('原药名', { exact: true })).toBeEnabled();
  await page.getByLabel('原药名', { exact: true }).fill(marker + ' 原药名');
  await page.getByLabel('实际使用状态', { exact: true }).selectOption('已结束');
  await page.getByRole('textbox', { name: '资料说明', exact: true }).fill(marker + ' 治疗说明');
  await page.getByText('资料出处与确认', { exact: true }).click();
  await page.getByLabel('来源描述或引用', { exact: true }).fill(marker + ' 本人来源');
  await page.getByRole('checkbox', { name: '已核对患儿归属、原始资料与登记来源', exact: true }).check();
  const therapy = await saveRecord(page.getByRole('button', { name: '确认并保存资料', exact: true }), 'therapy');
  assert.equal(therapy.fields.find(value => value.field_code === 'therapy_status').categorical_value, 'ended');
  assert.equal(therapy.fields.find(value => value.field_code === 'therapy_start').medical_time, null);
  assert.equal(therapy.fields.find(value => value.field_code === 'therapy_end').medical_time, null);
  assert.equal(therapy.fields.find(value => value.field_code === 'therapy_start').value_state, 'unknown');
  assert.equal(therapy.fields.find(value => value.field_code === 'therapy_end').value_state, 'unknown');
  check('治疗表单保存获准结束状态、独立时间及本人来源，刷新可以读取');
  const session = await (await family.request.get(origin + '/api/session')).json();
  const search = await family.request.post(origin + `/api/patients/${fixture.patient_id}/records/search`, {
    headers: { 'X-CSRFToken': session.csrf_token }, data: { original_name: marker } });
  assert.equal(search.status(), 200); assert.ok((await search.json()).records.some(value => value.record_id === report.record_id));
  check('查询使用本人字段与记录范围，保存报告可以按原名称读取');
  } else await login(page);
  const support = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const controlled = await support.newPage(); observe(controlled); await login(controlled, true);
  await controlled.goto(origin + '/support/sources');
  await expect(controlled.getByLabel('来源维护操作', { exact: true })).toBeEnabled();
  const sourceRows = await support.request.get(origin + '/api/support/sources');
  assert.equal(sourceRows.status(), 200);
  const existing = (await sourceRows.json()).items.find(value => value.id === fixture.source_id);
  const revision = existing.revisions.find(value => value.id === fixture.source_revision_id);
  let intentId;
  async function sourceOperation(operation, targetId, body) {
    if (intentId) await controlled.getByRole('button', { name: '建立新的维护请求', exact: true }).click();
    await controlled.getByLabel('来源维护操作', { exact: true }).selectOption(operation);
    if (targetId) await controlled.getByLabel('准确目标标识', { exact: true }).fill(targetId);
    await controlled.getByRole('textbox', { name: /^完整来源操作正文/ }).fill(JSON.stringify(body));
    const name = { source_register: '登记来源', source_revision_register: '登记来源版本', source_revision_approve: '批准来源版本',
      source_revision_revoke: '撤销来源版本', retention_policy_create: '保存保留政策', retention_policy_activate: '批准并启用保留政策' }[operation];
    await controlled.getByRole('button', { name, exact: true }).click();
    await controlled.waitForURL(url => url.searchParams.has('intent_id'));
    intentId = new URL(controlled.url()).searchParams.get('intent_id');
    await expect(controlled.getByRole('status')).toContainText('已经保存');
    const request = await support.request.get(origin + '/api/requests/' + intentId); assert.equal(request.status(), 200);
    const actual = await request.json(); assert.equal(actual.state, 'committed'); assert.equal(actual.operation_id, operation);
    assert.deepEqual(actual.controlled_input, body); assert.equal(actual.receipt.request_id, actual.request_id);
    return actual;
  }
  const sourceBody = { source_key: marker, owner: 'SIMULATION', location: existing.location, purpose: 'SIMULATION',
    sensitivity: 'SIMULATION', update_frequency: 'manual', evidence_ref: fixture.reference };
  const registered = await sourceOperation('source_register', null, sourceBody);
  const sourceId = registered.receipt.resource_id;
  await controlled.reload(); await expect(controlled.getByRole('status')).toContainText(sourceId);
  check('支持页面实际登记来源，持久意图、原始回执及刷新恢复一致');
  const declared = await sourceOperation('source_revision_register', sourceId, { version: 'SIMULATION-1', checksum: revision.checksum,
    license: 'user-authorized-local-simulation', allowed_use: ['configuration_approval', 'family_observation'],
    rights_evidence_ref: fixture.reference, retention_policy_id: fixture.retention_policy_id });
  const revisionId = declared.receipt.resource_id;
  let reread = (await (await support.request.get(origin + '/api/support/sources')).json()).items.find(value => value.id === sourceId);
  assert.equal(reread.revisions[0].status, 'pending'); check('来源版本登记保持待批准，完整许可与保留引用保存');
  await sourceOperation('source_revision_approve', revisionId, { expected_state: 'pending', reason: 'SIMULATION用户授权的来源批准' });
  reread = (await (await support.request.get(origin + '/api/support/sources')).json()).items.find(value => value.id === sourceId);
  assert.equal(reread.status, 'approved'); assert.equal(reread.revisions[0].status, 'approved');
  assert.equal(reread.revisions[0].rights_evidence_ref, fixture.reference);
  check('支持页面批准准确来源版本，许可依据及来源状态实际生效');
  await sourceOperation('source_revision_revoke', revisionId, { expected_state: 'approved', reason: 'SIMULATION撤销独立检查来源' });
  reread = (await (await support.request.get(origin + '/api/support/sources')).json()).items.find(value => value.id === sourceId);
  assert.equal(reread.revisions[0].status, 'revoked'); check('准确来源撤销后状态重新读取一致');
  const retention = await sourceOperation('retention_policy_create', null, { policy_key: marker, data_class: 'SIMULATION',
    schema_version: 'retention-1', rules: { retention_days: null, disposition: 'retain' }, evidence_ref: fixture.reference });
  let policy = (await (await support.request.get(origin + '/api/support/retention-policies')).json()).items.find(value => value.id === retention.receipt.resource_id);
  assert.equal(policy.status, 'draft'); check('保留政策严格校验，登记保存为草稿');
  await sourceOperation('retention_policy_activate', policy.id, { expected_state: 'draft', expected_version: policy.version, reason: 'SIMULATION批准继续保留' });
  policy = (await (await support.request.get(origin + '/api/support/retention-policies')).json()).items.find(value => value.id === policy.id);
  assert.equal(policy.status, 'active'); assert.ok(policy.approved_at); check('保留政策明确批准后生效并重新读取');
  const anonymous = await browser.newContext();
  assert.equal((await anonymous.request.post(origin + '/api/dictionaries/search', { data: { category: 'laboratory', query: 'SIM PLT' } })).status(), 401);
  assert.equal((await family.request.get(origin + '/api/support/sources')).status(), 403);
  check('匿名字典查询与家庭来源维护权限准确拒绝');
  for (const width of [320, 390, 768]) for (const scale of [1, 2]) {
    await controlled.setViewportSize({ width, height: 844 });
    await controlled.evaluate(value => { document.documentElement.style.fontSize = `${16 * value}px`; }, scale);
    const geometry = await controlled.evaluate(() => ({ viewport: innerWidth, content: document.documentElement.scrollWidth }));
    assert.ok(geometry.content <= geometry.viewport + 1, JSON.stringify({ width, scale, geometry }));
  }
  check('来源维护手机宽度及实际两倍文字没有水平溢出');
  assert.deepEqual(errors, []); complete = true;
} finally {
  const paths = ['../backend/dictionaries/service.py', '../backend/configurations/field_config.py', '../backend/sources/operations.py',
    '../backend/api/views_sources.py', 'src/components/DictionarySearchField.tsx', 'src/features/support/SourcesPage.tsx'];
  const sha256 = Object.fromEntries(await Promise.all(paths.map(async path => [path, createHash('sha256').update(await readFile(path)).digest('hex')])));
  await writeFile(`../.planning-work/t04-simulation-${sourcesOnly ? 'sources' : 'browser'}.json`, JSON.stringify({ mode: 'SIMULATION', status: complete ? 'PASS' : 'FAIL',
    checked_at: new Date().toISOString(), checks, responses, errors, saved_records: savedRecords, source_sha256: sha256,
    clinical_approval: 'NOT_RUN', visual: 'NOT_RUN', real_devices: 'NOT_RUN', wechat: 'NOT_RUN' }, null, 2));
  await browser.close();
}
