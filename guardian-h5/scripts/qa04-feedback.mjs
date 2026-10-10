import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { chromium } from 'playwright';
import { fileURLToPath } from 'node:url';

const baseURL = 'http://127.0.0.1:5174';
const output = new URL('../../.qa-work/QA04/', import.meta.url);
const startedAt = new Date().toISOString();
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1044, height: 1090 } });
const page = await context.newPage();
page.setDefaultTimeout(15000);
const errors = [];
const results = [];
const responses = [];
page.on('pageerror', error => errors.push(error.message));
page.on('dialog', dialog => dialog.accept());
page.on('response', response => {
  if (/recognition\/(report|therapy)-ocr|voice-transcription/.test(response.url())) responses.push({ url: response.url(), status: response.status() });
});
async function passed(id, detail = {}) {
  results.push({ id, status: 'PASS', ...detail });
  await writeFile(new URL('feedback.json', output), JSON.stringify({ startedAt, updatedAt: new Date().toISOString(), baseURL, results, responses, errors }, null, 2));
  console.log(id);
}
await page.goto(`${baseURL}/login`);
await page.getByPlaceholder('账号', { exact: true }).fill('acceptance-family');
await page.locator('input[type=password]').fill('Local-accept-2026');
await page.getByRole('button', { name: '登录', exact: true }).click();
await page.waitForURL(`${baseURL}/home`);
const csrf = (await (await context.request.get(`${baseURL}/api/auth/bootstrap`)).json()).csrf_token;
const relations = (await (await context.request.get(`${baseURL}/api/relations`)).json()).relations;
const patient = relations.find(item => item.display_id === '模拟患儿A').patient_id;
await page.getByLabel('当前患儿').selectOption(patient);

for (const width of [320, 390, 580, 1044]) {
  await page.setViewportSize({ width, height: 1090 });
  const dimensions = await page.locator('.child-image').evaluate(image => ({ height: image.getBoundingClientRect().height, fit: getComputedStyle(image).objectFit, loaded: image.complete && image.naturalHeight > 0 }));
  assert.ok(dimensions.height >= 164 && dimensions.height <= 230, JSON.stringify(dimensions));
  assert.equal(dimensions.fit, 'contain'); assert.ok(dimensions.loaded);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}
await passed('QA04-04-home-four-widths-aspect-ratio');

await page.goto(`${baseURL}/register/observations`);
await page.getByRole('button', { name: '文字输入', exact: true }).click();
for (const width of [320, 390, 580, 1044]) {
  await page.setViewportSize({ width, height: 1090 });
  const bounds = await page.locator('.voice-review').evaluate(element => {
    const textarea = element.querySelector('textarea').getBoundingClientRect();
    const label = element.querySelector('label > span').getBoundingClientRect();
    const button = element.querySelector('.action').getBoundingClientRect();
    return { width: element.getBoundingClientRect().width, textareaWidth: textarea.width, labelBottom: label.bottom, textareaTop: textarea.top, buttonHeight: button.height, buttonTop: button.top, textareaBottom: textarea.bottom };
  });
  assert.ok(bounds.textareaWidth >= bounds.width - 2);
  assert.ok(bounds.textareaTop >= bounds.labelBottom && bounds.buttonTop > bounds.textareaBottom && bounds.buttonHeight >= 44);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
}
await page.setViewportSize({ width: 320, height: 1090 });
const larger = await page.addStyleTag({ content: 'html { font-size: 200% !important; }' });
assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
await larger.evaluate(element => element.remove());
await passed('QA04-03-voice-layout-four-widths-large-text');

const audioResponse = page.waitForResponse(response => response.url().endsWith('/voice-transcription'), { timeout: 150000 });
await page.getByLabel('选择语音录音', { exact: true }).setInputFiles(fileURLToPath(new URL('observation.wav', output)));
const transcribed = await audioResponse;
assert.equal(transcribed.status(), 200);
await page.waitForFunction(() => document.querySelector('.voice-review textarea')?.value.length > 0);
const transcript = await page.getByLabel('语音转写与文字描述').inputValue();
assert.match(transcript, /38[.．]6|三十八[点點]六/);
assert.match(transcript, /没有|沒有/);
await page.waitForFunction(() => document.querySelector('.voice-review textarea')?.value.length > 0);
assert.equal(await page.getByLabel('语音转写与文字描述').inputValue(), transcript);
const structuredResponse = page.waitForResponse(response => response.url().endsWith('/voice-structure'), { timeout: 150000 });
await page.getByRole('button', { name: '提取候选并核对', exact: true }).click();
const structured = await structuredResponse;
assert.equal(structured.status(), 200, await structured.text());
assert.equal((await structured.json()).candidates.temperature, 38.6);
await page.getByLabel('采用体温与单位', { exact: true }).check();
await page.getByLabel('已逐项核对候选与原文', { exact: true }).check();
await page.getByRole('button', { name: '确认采用所选候选', exact: true }).click();
assert.equal(await page.locator('#obs-temp').inputValue(), '38.6');
const observation = page.waitForResponse(response => /\/patients\/[^/]+\/records$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST');
await page.getByRole('button', { name: '保存本次记录', exact: true }).click();
const savedObservation = await observation;
assert.equal(savedObservation.status(), 201, await savedObservation.text());
const observationId = (await savedObservation.json()).record_id;
await page.waitForURL(`${baseURL}/records`);
const observationRead = await (await context.request.get(`${baseURL}/api/records/${observationId}`)).json();
assert.equal(observationRead.capture_method, 'voice_confirmed');
assert.equal(observationRead.fields.find(field => field.field_code === 'temp').raw_numeric, '38.6');
await passed('QA04-01-audio-local-transcription-Ark-confirm-save-read', { transcript, recordId: observationId });

async function upload(name, endpoint) {
  const recognized = page.waitForResponse(response => response.url().endsWith(endpoint), { timeout: 150000 });
  await page.getByLabel('选择原始照片', { exact: true }).setInputFiles(fileURLToPath(new URL(`${name}.png`, output)));
  const response = await recognized;
  assert.equal(response.status(), 200, await response.text());
  await page.getByRole('button', { name: '相册多选', exact: true }).waitFor({ state: 'visible' });
  await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent.includes('相册多选')).disabled);
  const thumbnail = page.getByRole('region', { name: '照片 1', exact: true }).locator('img');
  assert.equal(await thumbnail.isVisible(), true);
  await thumbnail.evaluate(image => image.decode());
  assert.ok(await thumbnail.evaluate(image => image.naturalWidth > 0 && image.getBoundingClientRect().height >= 140));
  return await response.json();
}
async function save(button = '确认并保存') {
  const pending = page.waitForResponse(response => /\/patients\/[^/]+\/records$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST');
  await page.getByRole('button', { name: button, exact: true }).click();
  const response = await pending;
  assert.equal(response.status(), 201, await response.text());
  const created = await response.json();
  await page.waitForURL(`${baseURL}/records`);
  return await (await context.request.get(`${baseURL}/api/records/${created.record_id}`)).json();
}

await page.goto(`${baseURL}/register/reports`);
const report = await upload('report', '/report-ocr');
assert.equal(report.candidates.items.length, 4);
const ferritin = report.candidates.items.find(item => item.name.includes('铁蛋白'));
assert.equal(Number(ferritin.value), 150.5); assert.equal(ferritin.unit, 'ng/mL');
const crp = report.candidates.items.find(item => item.name.includes('反应蛋白'));
assert.equal(Number(crp.value), 5); assert.equal(crp.comparator, '<');
await page.getByText('本页已识别 4 项候选，请对照原图核对。', { exact: true }).waitFor();
await page.getByRole('link', { name: '核对识别内容', exact: true }).click();
await page.getByRole('button', { name: /全屏查看原图/ }).first().click();
await page.getByRole('dialog', { name: '原图查看器', exact: true }).waitFor();
await page.getByRole('button', { name: '关闭原图', exact: true }).click();
const retried = page.waitForResponse(response => response.url().endsWith('/report-ocr'), { timeout: 150000 });
await page.getByRole('button', { name: '重新识别本页', exact: true }).click();
assert.equal((await retried).status(), 200);
await page.waitForFunction(() => ![...document.querySelectorAll('button')].find(button => button.textContent === '重新识别本页').disabled);
assert.equal(await page.getByLabel('原项目名称', { exact: true }).count(), 4);
await page.getByLabel('已对照原图逐项核对', { exact: true }).check();
const savedReport = await save();
assert.equal(savedReport.fields.length, 4);
assert.ok(savedReport.fields.every(field => field.source_anchor?.file_id));
assert.equal(savedReport.capture_method, 'ocr_confirmed');
await passed('QA04-05-Chinese-report-visible-thumbnail-retry-source-save', { report });

await page.goto(`${baseURL}/register/reports`);
assert.equal((await upload('blank', '/report-ocr')).candidates.items.length, 0);
await page.getByText('本页未识别到可填写的项目。请检查照片是否清晰、内容是否完整，也可以手动填写。', { exact: true }).waitFor();
assert.equal(await page.getByRole('button', { name: '重新识别本页', exact: true }).isEnabled(), true);
await page.getByRole('button', { name: '手动添加检验项目', exact: true }).click();
await page.getByLabel('原项目名称', { exact: true }).fill('QA04手动项目');
await page.getByLabel('原始数值', { exact: true }).fill('0');
await page.getByLabel('已对照原图逐项核对', { exact: true }).check();
const manualReport = await save();
assert.equal(manualReport.capture_method, 'manual');
assert.equal(manualReport.fields[0].raw_numeric, '0');
await passed('QA04-06-empty-recognition-visible-manual-save-zero');

await page.goto(`${baseURL}/register/therapies`);
const therapy = await upload('therapy', '/therapy-ocr');
assert.equal(therapy.candidates.items.length, 2);
assert.ok(therapy.candidates.items.some(item => item.name === '甲氨蝶呤片'));
assert.ok(therapy.candidates.items.some(item => item.name === '叶酸片'));
assert.equal(await page.locator('#therapy-name').inputValue(), '');
const chosen = page.getByRole('region', { name: '治疗候选 1', exact: true });
await chosen.getByRole('button', { name: '采用此药物原文', exact: true }).click();
assert.equal(await page.getByLabel('实际使用状态', { exact: true }).inputValue(), '无法确定');
await page.getByLabel('已核对患儿归属、原始资料与登记来源').check();
await page.locator('#therapy-note').fill(`${await page.locator('#therapy-note').inputValue()}\n本人核对原文。`);
assert.equal(await page.getByLabel('已核对患儿归属、原始资料与登记来源').isChecked(), false);
const drafted = page.waitForResponse(response => /\/drafts(?:\/[^/]+)?$/.test(new URL(response.url()).pathname) && ['POST', 'PUT'].includes(response.request().method()));
await page.getByRole('button', { name: '保存草稿', exact: true }).click();
assert.equal((await drafted).ok(), true);
await page.reload();
await page.getByRole('heading', { name: '治疗识别候选', exact: true }).waitFor();
assert.equal(await page.getByRole('region', { name: '照片 1', exact: true }).locator('img').isVisible(), true);
assert.equal(await page.getByRole('button', { name: '采用此药物原文', exact: true }).count(), 2);
await page.getByLabel('已核对患儿归属、原始资料与登记来源').check();
const savedTherapy = await save();
assert.equal(savedTherapy.capture_method, 'ocr_confirmed');
assert.equal(savedTherapy.fields.find(field => field.field_code === 'medication_name').raw_text, therapy.candidates.items[0].name);
assert.ok(savedTherapy.fields.find(field => field.field_code === 'medication_name').source_anchor.file_id);
assert.equal(savedTherapy.fields.find(field => field.field_code === 'therapy_status').value_state, 'unknown');
await passed('QA04-07-08-therapy-candidates-confirm-draft-source-save', { therapy });

await page.goto(`${baseURL}/register/observations`);
await page.getByRole('button', { name: '文字输入', exact: true }).click();
await page.getByLabel('语音转写与文字描述').fill('需要保留的本人描述');
const invalid = page.waitForResponse(response => response.url().endsWith('/voice-transcription'));
await page.getByLabel('选择语音录音', { exact: true }).setInputFiles({ name: 'invalid.wav', mimeType: 'audio/wav', buffer: Buffer.from('invalid audio') });
assert.equal((await invalid).status(), 422);
await page.getByText('录音文件无法解码，请重新录音或选择完整音频。', { exact: true }).waitFor();
assert.equal(await page.getByLabel('语音转写与文字描述').inputValue(), '需要保留的本人描述');
assert.equal(await page.getByRole('button', { name: '重新转写本次录音', exact: true }).isEnabled(), true);
const noCsrf = await context.request.post(`${baseURL}/api/patients/${patient}/voice-transcription`, { data: await readFile(new URL('observation.wav', output)), headers: { 'Content-Type': 'audio/wav' } });
assert.equal(noCsrf.status(), 403);
const invalidType = await context.request.post(`${baseURL}/api/patients/${patient}/voice-transcription`, { data: 'text', headers: { 'Content-Type': 'text/plain', 'X-CSRFToken': csrf } });
assert.equal(invalidType.status(), 415);
await passed('QA04-02-invalid-audio-preserves-text-CSRF-type');
assert.deepEqual(errors, []);
await browser.close();
console.log(JSON.stringify({ passed: results.length, errors }));
