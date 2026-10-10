import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '../../node_modules/playwright/test.mjs';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectDirectory = resolve(scriptDirectory, '../../..');
process.loadEnvFile(resolve(projectDirectory, '.simulation-work/environment.env'));

const fixture = JSON.parse(await readFile(resolve(projectDirectory, '.simulation-work/t29-first-alert-business-fixture.json'), 'utf8'));
const notificationReference = JSON.parse(await readFile(resolve(projectDirectory,
  '.planning-work/parallel/window1/t23-native-delivery-first-alert-main-04-readback.json'), 'utf8'));
assert.equal(fixture.mode, 'SIMULATION_ONLY');
assert.equal(notificationReference.mode, 'SIMULATION_ONLY');
assert.equal(notificationReference.patient_id, fixture.patient_id);
assert.equal(notificationReference.status, 'NATIVE_SEND_DELIVERED_AND_READ');
const origin = process.env.SJIA_H5_BASE_URL ?? 'http://127.0.0.1:5183';
const evidenceDirectory = resolve(projectDirectory, '.planning-work/parallel/window2/evidence');
await mkdir(evidenceDirectory, { recursive: true });
const checks = [];
const apiReads = [];
const apiMutations = [];
const pageApiResponses = [];
const pageErrors = [];
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Asia/Shanghai' });
const page = await context.newPage();
page.on('pageerror', error => pageErrors.push(error.message));
page.on('response', response => {
  const url = new URL(response.url());
  if (url.pathname.startsWith('/api/')) pageApiResponses.push({
    method: response.request().method(), path: url.pathname, status: response.status(),
    body: url.pathname.endsWith('/records/search') && response.request().method() === 'POST'
      ? response.request().postDataJSON() : null,
    response,
  });
});
page.on('request', request => {
  const url = new URL(request.url());
  if (url.pathname.startsWith('/api/')) {
    const event = { method: request.method(), path: url.pathname, search: url.search };
    if (['GET', 'HEAD', 'OPTIONS'].includes(request.method()) || request.method() === 'POST' && url.pathname.endsWith('/records/search'))
      apiReads.push({ ...event, ...(request.method() === 'POST' ? { read_only_query: true } : {}) });
    else apiMutations.push(event);
  }
});

async function getJson(path, status = 200) {
  const response = await context.request.get(new URL(path, origin).toString());
  const body = await response.text();
  const value = body ? JSON.parse(body) : null;
  apiReads.push({ method: 'GET', path, status: response.status(), body: value });
  assert.equal(response.status(), status, `${path}: ${response.status()} ${body}`);
  return value;
}

function recordCheck(name, result) {
  checks.push({ name, result });
}

async function assertSourceVersionLinks(versionIds) {
  const counts = new Map();
  for (const versionId of versionIds) counts.set(versionId, (counts.get(versionId) ?? 0) + 1);
  for (const [versionId, count] of counts) {
    await expect(page.locator(`a[href="/records/versions/${versionId}"]`)).toHaveCount(count);
  }
}

function waitForPageApiResponse(path, method = 'GET', afterIndex = 0) {
  const captured = pageApiResponses.slice(afterIndex).find(item => item.path === path && item.method === method);
  if (captured) return Promise.resolve(captured.response);
  return page.waitForResponse(response => new URL(response.url()).pathname === path
    && response.request().method() === method);
}

async function waitForHomePage(homeResponsePromise, responseStart) {
  const response = await homeResponsePromise;
  const body = await response.text();
  assert.equal(response.status(), 200, `首页读取失败：${response.status()} ${body}`);
  const home = JSON.parse(body);
  assert.equal(home.current_alert_id, notificationReference.alert_id);
  await page.waitForLoadState('networkidle');
  await expect(page.locator('[data-screen-id="UI2-01"]')).toBeVisible();
  await expect(page.getByText('正在读取当前资料', { exact: true })).toHaveCount(0);
  await expect(page.locator('.observed-tags').getByText('正在读取', { exact: true })).toHaveCount(0);
  await expect(page.getByText('读取失败', { exact: true })).toHaveCount(0);
  await expect(page.getByText('暂时无法取得资料', { exact: true })).toHaveCount(0);
  const observationResponses = pageApiResponses.slice(responseStart).filter(item => item.path === `/api/patients/${fixture.patient_id}/records/search`
    && item.method === 'POST');
  for (const code of ['temp', 'rash', 'joint_discomfort']) {
    const matches = observationResponses.filter(item => item.body?.kind === 'observation' && item.body.original_name === code);
    assert.ok(matches.length, `首页没有完成 ${code} 观察读取请求`);
    for (const item of matches) assert.equal(item.status, 200, `首页 ${code} 观察读取失败：HTTP ${item.status}`);
  }
  const alertResponse = await waitForPageApiResponse(`/api/alerts/${home.current_alert_id}`, 'GET', responseStart);
  const alertBody = await alertResponse.text();
  assert.equal(alertResponse.status(), 200, `首页提示详情读取失败：HTTP ${alertResponse.status()} ${alertBody}`);
  await expect(page.getByText('正在读取获准提示内容', { exact: true })).toHaveCount(0);
  return home;
}

async function loginFamily() {
  await page.goto(new URL('/login', origin).toString());
  await page.getByLabel('账号', { exact: true }).fill(fixture.family_username);
  await page.getByLabel('密码', { exact: true }).fill(process.env.SJIA_SIMULATION_PASSWORD);
  const responseStart = pageApiResponses.length;
  const initialHomeResponse = waitForPageApiResponse(`/api/patients/${fixture.patient_id}/home`, 'GET', responseStart);
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await expect(page).toHaveURL(/\/home$/);
  await waitForHomePage(initialHomeResponse, responseStart);
  const relations = await getJson('/api/relations');
  const relation = relations.relations.find(item => item.patient_id === fixture.patient_id && item.status === 'active'
    && item.actions.includes('read_raw'));
  if (!relation) throw new Error('模拟家庭账号没有指定患儿的当前资料读取权限。');
  let session = await getJson('/api/session');
  if (session.selected_patient_id !== fixture.patient_id) {
    await page.goto(new URL('/associations', origin).toString());
    await expect(page.locator('[data-screen-id="UI2-18"]')).toBeVisible();
    const patientCard = page.locator('section.card.form-card').filter({
      has: page.getByRole('heading', { name: relation.display_id, exact: true }),
    });
    await expect(patientCard).toHaveCount(1);
    await page.waitForLoadState('networkidle');
    session = await getJson('/api/session');
    let returnedHome = false;
    if (session.selected_patient_id !== fixture.patient_id) {
      const currentTime = Date.now();
      const firstAvailable = relations.relations.find(item => item.status === 'active'
        && Date.parse(item.valid_from) <= currentTime
        && (item.valid_until === null || Date.parse(item.valid_until) > currentTime)
        && item.actions.includes('read_raw'));
      if (session.selected_patient_id === null && firstAvailable?.patient_id === fixture.patient_id) {
        await expect(patientCard.getByText('当前患儿', { exact: true })).toBeVisible();
      } else {
        const selectPatient = patientCard.getByRole('button', { name: '选择此患儿', exact: true });
        await expect(selectPatient).toBeEnabled();
        const selectionResponseStart = pageApiResponses.length;
        const selectionResponse = waitForPageApiResponse('/api/patient-selection', 'POST', selectionResponseStart);
        const selectedHomeResponse = waitForPageApiResponse(`/api/patients/${fixture.patient_id}/home`, 'GET', selectionResponseStart);
        await selectPatient.click();
        const selectionResult = await selectionResponse;
        const selectionBody = await selectionResult.json();
        assert.equal(selectionResult.status(), 200);
        assert.equal(selectionBody.selected_patient_id, fixture.patient_id);
        await expect(page).toHaveURL(/\/home$/);
        await waitForHomePage(selectedHomeResponse, selectionResponseStart);
        session = await getJson('/api/session');
        assert.equal(session.selected_patient_id, fixture.patient_id);
        returnedHome = true;
      }
    }
    if (!returnedHome) {
      const selectedHomeResponseStart = pageApiResponses.length;
      const selectedHomeResponse = waitForPageApiResponse(`/api/patients/${fixture.patient_id}/home`, 'GET', selectedHomeResponseStart);
      await page.goto(new URL('/home', origin).toString());
      await expect(page).toHaveURL(/\/home$/);
      await waitForHomePage(selectedHomeResponse, selectedHomeResponseStart);
    }
  }
  const selected = await getJson('/api/session');
  if (selected.selected_patient_id !== fixture.patient_id) {
    assert.equal(selected.selected_patient_id, null);
    const currentTime = Date.now();
    const firstAvailable = relations.relations.find(item => item.status === 'active'
      && Date.parse(item.valid_from) <= currentTime
      && (item.valid_until === null || Date.parse(item.valid_until) > currentTime)
      && item.actions.includes('read_raw'));
    assert.equal(firstAvailable?.patient_id, fixture.patient_id);
  }
}

async function searchAllRecords() {
  const session = await getJson('/api/session');
  assert.equal(session.principal_id, fixture.owner_id);
  assert.equal(session.audience, 'family');
  assert.ok(session.csrf_token);
  const records = [];
  let cursor = null;
  do {
    const payload = { limit: 100, ...(cursor ? { cursor } : {}) };
    const response = await context.request.post(new URL(`/api/patients/${fixture.patient_id}/records/search`, origin).toString(), {
      data: payload, headers: { 'X-CSRFToken': session.csrf_token },
    });
    const body = await response.text();
    assert.equal(response.status(), 200, `记录查询失败：${response.status()} ${body}`);
    const value = JSON.parse(body);
    apiReads.push({ method: 'POST', path: `/api/patients/${fixture.patient_id}/records/search`, read_only_query: true, body: value });
    records.push(...value.records);
    cursor = value.next_cursor;
  } while (cursor);
  return records;
}

function summaryDates(records) {
  const dates = records.flatMap(record => record.detail.fields.map(field => field.medical_time?.local_date ?? null))
    .filter(value => value !== null).sort();
  if (dates.length) return { from: dates[0], to: dates.at(-1), includesUnknownTime: true };
  const knownAt = new Date(fixture.known_at);
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(knownAt);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  const date = `${values.year}-${values.month}-${values.day}`;
  return { from: date, to: date, includesUnknownTime: true };
}

async function checkAlertFlow() {
  const home = await getJson(`/api/patients/${fixture.patient_id}/home`);
  const list = await getJson(`/api/patients/${fixture.patient_id}/alerts`);
  const eligible = list.alerts.filter(item => item.validity === 'valid');
  let alertId = home.current_alert_id;
  if (!alertId && eligible.length === 1) alertId = eligible[0].alert_id;
  if (!alertId || eligible.filter(item => item.alert_id === alertId).length !== 1) {
    recordCheck('UI2-12 提示正文与阅读', 'NOT_RUN：当前有效提示不能唯一确定');
    return null;
  }
  assert.equal(alertId, notificationReference.alert_id);
  const initial = await getJson(`/api/alerts/${alertId}`);
  const notice = await getJson(`/api/v2/alerts/${alertId}/notification`);
  assert.equal(notice.record?.recipient_id, notificationReference.recipient_id);
  assert.equal(notice.record?.delivery_status, 'delivered');
  assert.equal(notice.record?.read_at, notificationReference.family_api_record.read_at);
  const before = { alert_id: alertId, alert: initial, notification: notice.record };
  if (initial.validity !== 'valid' || initial.content_state !== 'available' || !initial.content?.approved_text) {
    recordCheck('UI2-12 提示正文与阅读', 'NOT_RUN：当前提示正文没有可读取的有效内容');
    return before;
  }

  const detailResponseStart = pageApiResponses.length;
  const detailResponsePromise = waitForPageApiResponse(`/api/alerts/${alertId}`, 'GET', detailResponseStart);
  await page.goto(new URL(`/alerts/${alertId}`, origin).toString());
  const detailResponse = await detailResponsePromise;
  const detailBody = await detailResponse.text();
  assert.equal(detailResponse.status(), 200, `提示页面读取失败：${detailResponse.status()} ${detailBody}`);
  const pageDetail = JSON.parse(detailBody);
  assert.equal(pageDetail.alert_id, alertId);
  assert.equal(pageDetail.validity, 'valid');
  assert.equal(pageDetail.content_state, 'available');
  assert.equal(pageDetail.content?.approved_text, initial.content.approved_text);
  assert.deepEqual(pageDetail.content?.source_refs, initial.content.source_refs);
  await expect(page.locator('[data-screen-id="UI2-12"]')).toBeVisible();
  await expect(page.getByText('正在读取当前资料', { exact: true })).toHaveCount(0);
  await expect(page.getByText('暂时无法取得资料', { exact: true })).toHaveCount(0);
  await expect(page.getByText('正在保存', { exact: true })).toHaveCount(0);
  assert.equal(pageApiResponses.slice(detailResponseStart).find(item => item.path === `/api/alerts/${alertId}`
    && item.method === 'GET')?.status, 200);
  await expect(page.getByText(initial.content.approved_text, { exact: true })).toBeVisible();
  const sourceVersionIds = initial.content.source_refs.map(item => item.record_version_id);
  await assertSourceVersionLinks(sourceVersionIds);
  const contact = initial.content.contact;
  if (contact.state === 'available') {
    await expect(page.getByText(contact.contact_name, { exact: true })).toBeVisible();
    await expect(page.getByText(contact.channel_value, { exact: true })).toBeVisible();
  } else {
    await expect(page.getByText('当前没有有效联系信息。', { exact: true })).toBeVisible();
  }
  await page.locator('.home-alert').scrollIntoViewIfNeeded();
  await page.waitForFunction(() => {
    const root = document.querySelector('[data-screen-id="UI2-12"]')?.shadowRoot;
    const pair = [...(root?.querySelectorAll('.pair') ?? [])].find(node => node.querySelector('span')?.textContent === '本人阅读时间');
    const value = pair?.querySelector('strong')?.textContent?.trim();
    return !!value && value !== '尚未记录' && value !== '正在保存';
  });
  const readOnce = await getJson(`/api/alerts/${alertId}`);
  assert.ok(readOnce.read_at, 'UI2-12 页面没有保存本人阅读时间');
  assert.equal(readOnce.alert_id, alertId);
  const visibleReadAt = await page.locator('.pair').filter({ hasText: '本人阅读时间' }).locator('strong').textContent();
  const refreshResponseStart = pageApiResponses.length;
  const refreshResponsePromise = waitForPageApiResponse(`/api/alerts/${alertId}`, 'GET', refreshResponseStart);
  await page.reload();
  const refreshResponse = await refreshResponsePromise;
  const refreshBody = await refreshResponse.text();
  assert.equal(refreshResponse.status(), 200, `提示刷新读取失败：${refreshResponse.status()} ${refreshBody}`);
  const refreshedPageDetail = JSON.parse(refreshBody);
  assert.equal(refreshedPageDetail.alert_id, alertId);
  assert.equal(refreshedPageDetail.content?.approved_text, initial.content.approved_text);
  assert.deepEqual(refreshedPageDetail.content?.source_refs, initial.content.source_refs);
  await expect(page.locator('[data-screen-id="UI2-12"]')).toBeVisible();
  await expect(page.getByText('正在读取当前资料', { exact: true })).toHaveCount(0);
  await expect(page.getByText('暂时无法取得资料', { exact: true })).toHaveCount(0);
  await expect(page.getByText('正在保存', { exact: true })).toHaveCount(0);
  const refreshedVisibleReadAt = await page.locator('.pair').filter({ hasText: '本人阅读时间' }).locator('strong').textContent();
  assert.equal(refreshedVisibleReadAt, visibleReadAt);
  assert.notEqual(refreshedVisibleReadAt?.trim(), '尚未记录');
  assert.equal(refreshedPageDetail.read_at, readOnce.read_at);
  await expect(page.getByText(initial.content.approved_text, { exact: true })).toBeVisible();
  if (contact.state === 'available') {
    await expect(page.getByText(contact.contact_name, { exact: true })).toBeVisible();
    await expect(page.getByText(contact.channel_value, { exact: true })).toBeVisible();
  } else {
    await expect(page.getByText('当前没有有效联系信息。', { exact: true })).toBeVisible();
  }
  await assertSourceVersionLinks(sourceVersionIds);
  const readAfterRefresh = await getJson(`/api/alerts/${alertId}`);
  assert.equal(readAfterRefresh.read_at, readOnce.read_at);
  const noticeAfterRefresh = await getJson(`/api/v2/alerts/${alertId}/notification`);
  recordCheck('UI2-12 正文、来源、联系、阅读保存与刷新重读', 'PASS');
  return { alert_id: alertId, initial, notification_before: notice.record, read_after_page: readOnce,
    read_after_refresh: readAfterRefresh, notification_after_refresh: noticeAfterRefresh.record,
    source_version_ids: sourceVersionIds, contact };
}

async function checkSupportRecord(alertResult) {
  if (!alertResult?.notification_before) {
    recordCheck('UI2-39 受控通知详情', 'NOT_RUN：没有当前提示接收记录可关联');
    return null;
  }
  const record = alertResult.notification_before;
  assert.equal(record.recipient_id, notificationReference.recipient_id);
  assert.equal(record.alert_id, notificationReference.alert_id);
  await page.goto(new URL('/support/login', origin).toString());
  await page.getByLabel('账号', { exact: true }).fill('simulation-support');
  await page.getByLabel('密码', { exact: true }).fill(process.env.SJIA_SIMULATION_PASSWORD);
  await page.getByRole('button', { name: '登录账号', exact: true }).click();
  await expect(page).toHaveURL(/\/support\/data-use$/);
  await page.goto(new URL('/support/deliveries', origin).toString());
  await expect(page.locator('[data-screen-id="UI2-39"]')).toBeVisible();
  const capability = await getJson('/api/v2/support/notification-scopes');
  if (capability.capability_state !== 'available' || !capability.items.some(item => item.patient_id === fixture.patient_id)) {
    recordCheck('UI2-39 受控通知详情', 'NOT_RUN：支持岗位没有该患儿的通知授权范围');
    return { capability };
  }
  await page.getByRole('button', { name: '选择获准资料范围', exact: true }).click();
  await page.getByRole('button', { name: new RegExp(fixture.patient_id) }).click();
  await expect(page.getByText('实际发送与回执', { exact: true })).toBeVisible();
  const history = await getJson(`/api/v2/support/patients/${fixture.patient_id}/notification-records`);
  const matches = history.items.filter(item => item.alert_id === alertResult.alert_id && item.recipient_id === record.recipient_id);
  if (matches.length !== 1) {
    recordCheck('UI2-39 受控通知详情', 'NOT_RUN：受控通知记录无法唯一关联');
    return { capability, history, matching_records: matches };
  }
  const targetIndex = history.items.findIndex(item => item.recipient_id === record.recipient_id);
  const historySection = page.locator('section.card.form-card').filter({ has: page.getByRole('heading', { name: '实际发送与回执', exact: true }) });
  const exactResponseStart = pageApiResponses.length;
  const exactResponsePromise = waitForPageApiResponse(`/api/v2/support/notification-records/${record.recipient_id}`, 'GET', exactResponseStart);
  await historySection.getByRole('button', { name: /已受理|已送达|尚未发送|发送失败|受理状态未知/ }).nth(targetIndex).click();
  const exactResponse = await exactResponsePromise;
  assert.equal(exactResponse.status(), 200);
  const exact = await exactResponse.json();
  assert.equal(exact.recipient_id, record.recipient_id);
  assert.equal(exact.alert_id, alertResult.alert_id);
  assert.equal(exact.delivery_status, record.delivery_status);
  await expect(page.getByRole('heading', { name: '准确通知记录', exact: true })).toBeVisible();
  await expect(page.getByText(record.recipient_id, { exact: true })).toBeVisible();
  await expect(page.getByText(record.read_at ?? '尚未阅读', { exact: true })).toBeVisible();
  const recordUrl = page.url();
  const supportRefreshStart = pageApiResponses.length;
  const supportRefreshPromise = waitForPageApiResponse(`/api/v2/support/notification-records/${record.recipient_id}`, 'GET', supportRefreshStart);
  await page.reload();
  const supportRefresh = await supportRefreshPromise;
  assert.equal(supportRefresh.status(), 200);
  assert.deepEqual(await supportRefresh.json(), exact);
  await expect(page).toHaveURL(recordUrl);
  await expect(page.getByRole('heading', { name: '准确通知记录', exact: true })).toBeVisible();
  await expect(page.getByText(record.recipient_id, { exact: true })).toBeVisible();
  const refreshed = await getJson(`/api/v2/support/notification-records/${record.recipient_id}`);
  assert.deepEqual(refreshed, exact);
  recordCheck('UI2-39 授权范围、接收人、回执与刷新重读', 'PASS');
  return { capability, history, record: exact, record_after_refresh: refreshed, route: recordUrl };
}

async function checkSummaryFlow() {
  await page.goto(new URL('/account', origin).toString());
  const logoutResponseStart = pageApiResponses.length;
  const logoutResponsePromise = waitForPageApiResponse('/api/session/logout', 'POST', logoutResponseStart);
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  const logoutResponse = await logoutResponsePromise;
  assert.equal(logoutResponse.status(), 200);
  await expect(page).toHaveURL(/\/home$/);
  await loginFamily();
  const records = await searchAllRecords();
  if (records.length === 0) {
    recordCheck('UI2-13/14 摘要生成、来源和刷新重读', 'NOT_RUN：指定患儿当前没有可读记录');
    return { records: [], status: 'NOT_RUN' };
  }
  const dateRange = summaryDates(records);
  const originalRequests = await getJson(`/api/patients/${fixture.patient_id}/requests?operation_id=summary_create`);
  const pendingRequests = originalRequests.requests.filter(item => item.state !== 'committed');
  assert.ok(pendingRequests.length <= 1, '需要逐项核查多个原摘要请求');
  const pendingRequest = pendingRequests[0] ?? null;
  if (pendingRequest) {
    assert.equal(pendingRequest.may_retry, true);
    assert.equal(pendingRequest.summary_input.from_date, dateRange.from);
    assert.equal(pendingRequest.summary_input.to_date, dateRange.to);
    assert.equal(pendingRequest.summary_input.scope_timezone, 'Asia/Shanghai');
    assert.equal(pendingRequest.summary_input.include_unknown_time, true);
  }
  const summaryPageStart = pageApiResponses.length;
  const summaryDirectoryResponsePromise = waitForPageApiResponse(`/api/patients/${fixture.patient_id}/requests`, 'GET', summaryPageStart);
  await page.goto(new URL('/summaries', origin).toString());
  await expect(page.locator('[data-screen-id="UI2-13"]')).toBeVisible();
  const summaryDirectoryResponse = await summaryDirectoryResponsePromise;
  assert.equal(summaryDirectoryResponse.status(), 200);
  await summaryDirectoryResponse.body();
  let button;
  if (pendingRequest) {
    const pendingSection = page.locator('details section.summary-section').filter({
      has: page.getByRole('heading', { name: '尚待核验结果', exact: true }),
    });
    await expect(pendingSection).toHaveCount(1);
    button = pendingSection.getByRole('button', { name: '查询原请求并继续', exact: false });
  } else {
    await page.getByLabel('开始日期', { exact: false }).fill(dateRange.from);
    await page.getByLabel('结束日期', { exact: false }).fill(dateRange.to);
    await page.getByText('时间与资料范围说明', { exact: true }).click();
    const unknownTime = page.getByLabel('包含医学时间尚未确定的资料', { exact: false });
    if (dateRange.includesUnknownTime && !(await unknownTime.isChecked())) await unknownTime.check();
    button = page.getByRole('button', { name: '生成就医摘要', exact: true });
  }
  await expect(button).toBeEnabled();
  const summaryResponseStart = pageApiResponses.length;
  const summaryTaskResponsePromise = waitForPageApiResponse(`/api/patients/${fixture.patient_id}/summaries`, 'POST', summaryResponseStart);
  await button.click();
  const summaryTaskResponse = await summaryTaskResponsePromise;
  const summaryTaskBody = await summaryTaskResponse.text();
  assert.equal(summaryTaskResponse.status(), 202, `摘要提交失败：${summaryTaskResponse.status()} ${summaryTaskBody}`);
  const submittedTask = JSON.parse(summaryTaskBody);
  assert.equal(submittedTask.status, 'completed');
  assert.equal(submittedTask.result_type, 'summary');
  assert.ok(submittedTask.result_ref);
  assert.ok(submittedTask.task_id);
  const summaryId = submittedTask.result_ref;
  const taskId = submittedTask.task_id;
  const savedSummaryResponse = await waitForPageApiResponse(`/api/summaries/${summaryId}`, 'GET', summaryResponseStart);
  const savedSummaryBody = await savedSummaryResponse.text();
  assert.equal(savedSummaryResponse.status(), 200, `保存的摘要读取失败：${savedSummaryResponse.status()} ${savedSummaryBody}`);
  const detail = JSON.parse(savedSummaryBody);
  assert.equal(detail.summary_id, summaryId);
  assert.equal(detail.task_id, taskId);
  const savedTaskResponse = await waitForPageApiResponse(`/api/tasks/${taskId}`, 'GET', summaryResponseStart);
  const savedTaskBody = await savedTaskResponse.text();
  assert.equal(savedTaskResponse.status(), 200, `摘要任务读取失败：${savedTaskResponse.status()} ${savedTaskBody}`);
  const task = JSON.parse(savedTaskBody);
  assert.equal(task.status, 'completed');
  assert.equal(task.result_ref, summaryId);
  if (pendingRequest) assert.equal(task.request_id, pendingRequest.request_id);
  await expect(page).toHaveURL(/\/summaries\/[0-9a-f-]+$/);
  await expect(page.locator('[data-screen-id="UI2-14"]')).toBeVisible();
  const requestList = await getJson(`/api/patients/${fixture.patient_id}/requests?operation_id=summary_create`);
  const request = requestList.requests.find(item => item.receipt?.resource_id === summaryId);
  assert.ok(request, '摘要页面没有可读取的准确保存请求');
  const exactRequest = await getJson(`/api/requests/${request.intent_id}`);
  if (pendingRequest) assert.equal(exactRequest.intent_id, pendingRequest.intent_id);
  assert.equal(task.request_id, exactRequest.request_id);
  assert.equal(task.result_ref, summaryId);
  const sourceVersionIds = detail.items.map(item => item.record_version_id);
  await page.getByText('查询生成依据', { exact: true }).click();
  await expect(page.getByText(summaryId, { exact: true })).toBeVisible();
  await assertSourceVersionLinks(sourceVersionIds);
  const summaryRefreshStart = pageApiResponses.length;
  const summaryRefreshPromise = waitForPageApiResponse(`/api/summaries/${summaryId}`, 'GET', summaryRefreshStart);
  await page.reload();
  const summaryRefreshResponse = await summaryRefreshPromise;
  const summaryRefreshBody = await summaryRefreshResponse.text();
  assert.equal(summaryRefreshResponse.status(), 200, `摘要刷新读取失败：${summaryRefreshResponse.status()} ${summaryRefreshBody}`);
  const reread = JSON.parse(summaryRefreshBody);
  assert.deepEqual(reread, detail);
  await expect(page.locator('[data-screen-id="UI2-14"]')).toBeVisible();
  await expect(page.getByText('正在读取当前资料', { exact: true })).toHaveCount(0);
  await expect(page.getByText('暂时无法取得资料', { exact: true })).toHaveCount(0);
  await page.getByText('查询生成依据', { exact: true }).click();
  await expect(page.getByText(summaryId, { exact: true })).toBeVisible();
  await assertSourceVersionLinks(sourceVersionIds);
  const rereadTask = await getJson(`/api/tasks/${taskId}`);
  assert.deepEqual(rereadTask, task);
  recordCheck('UI2-13/14 摘要保存、Task、来源版本与刷新重读', 'PASS');
  return { status: 'PASS', original_pending_request: pendingRequest, date_range: dateRange, source_records: records.map(item => ({ record_id: item.record_id,
    record_version_id: item.detail.record_version_id, version: item.detail.version })), summary: detail,
    request: exactRequest, task, summary_after_refresh: reread, task_after_refresh: rereadTask,
    source_version_ids: sourceVersionIds, database_crosscheck: 'PENDING_MAIN_WINDOW_READBACK' };
}

let result;
try {
  await loginFamily();
  const alert = await checkAlertFlow();
  const support = await checkSupportRecord(alert);
  const summary = await checkSummaryFlow();
  assert.equal(apiMutations.filter(item => item.method === 'POST'
    && item.path === `/api/v2/notification-records/${notificationReference.recipient_id}/delivered`).length, 0);
  recordCheck('已送达通知重新读取保持原回执且没有重复确认', 'PASS');
  assert.deepEqual(pageErrors, []);
  result = { mode: 'SIMULATION_ONLY', status: checks.every(item => item.result === 'PASS') ? 'PASS' : 'PARTIAL',
    task_id: 'T56_UI2_NOTIFICATION_SUMMARY_SUPPLEMENT', checked_at: new Date().toISOString(),
    origin, account: fixture.family_username, patient_id: fixture.patient_id, notification_reference: notificationReference,
    alert, support, summary,
    checks, api_reads: apiReads, api_mutations: apiMutations,
    page_api_responses: pageApiResponses.map(({ method, path, status, body }) => ({ method, path, status,
      ...(body ? { query: body } : {}) })), page_errors: pageErrors,
    visual: 'NOT_RUN', actual_device: 'NOT_RUN', family_observation: 'NOT_RUN', wechat: 'NOT_RUN',
    database_crosscheck: 'PENDING_MAIN_WINDOW_READBACK' };
} finally {
  await context.close();
  await browser.close();
}

const reportPath = resolve(evidenceDirectory, `t56-notification-summary-ui-${new Date().toISOString().replaceAll(':', '').replaceAll('.', '-')}.json`);
await writeFile(reportPath, JSON.stringify(result, null, 2));
process.stdout.write(`${JSON.stringify({ status: result.status, report: reportPath, checks: result.checks,
  summary_id: result.summary?.summary?.summary_id ?? null, task_id: result.summary?.task?.task_id ?? null,
  request_id: result.summary?.request?.request_id ?? null }, null, 2)}\n`);
