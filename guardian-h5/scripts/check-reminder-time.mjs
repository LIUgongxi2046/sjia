import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { Temporal } from '@js-temporal/polyfill';
import { reminderClock, reminderInstant, reminderLocal } from '../src/api/reminderTime.ts';

const cases = [];
function check(name, operation) { operation(); cases.push({ name, result: 'PASS' }); }
check('准确时区转换及重新读取', () => {
  const future = Temporal.Now.instant().add({ hours: 48 });
  const local = reminderLocal(future.toString(), 'Asia/Shanghai');
  assert.equal(reminderInstant(local, 'Asia/Shanghai', 'reject'), future.toString());
});
check('夏令时重复时间要求本人选择', () => {
  assert.throws(() => reminderInstant('2027-11-07T01:30', 'America/New_York', 'reject'), RangeError);
  assert.equal(reminderInstant('2027-11-07T01:30', 'America/New_York', 'earlier'), '2027-11-07T05:30:00Z');
  assert.equal(reminderInstant('2027-11-07T01:30', 'America/New_York', 'later'), '2027-11-07T06:30:00Z');
});
check('夏令时不存在时间拒绝保存', () => {
  assert.throws(() => reminderInstant('2027-03-14T02:30', 'America/New_York', 'reject'), RangeError);
  for (const choice of ['earlier', 'later']) assert.throws(() => reminderInstant('2027-03-14T02:30', 'America/New_York', choice), /没有这个当地时间/);
});
check('无效日期和时区就地拒绝', () => {
  assert.throws(() => reminderInstant('2027-02-30T12:00', 'Asia/Shanghai', 'reject'), RangeError);
  assert.throws(() => reminderInstant('2027-12-01T12:00', 'Invalid/Timezone', 'reject'), RangeError);
});
check('过去时间与缺少时区拒绝保存', () => {
  assert.throws(() => reminderInstant('2020-01-01T12:00', 'Asia/Shanghai', 'reject'), /晚于当前时间/);
  assert.throws(() => reminderInstant('2027-12-01T12:00', '', 'reject'), /选择本人所在时区/);
});
check('安静时段保持秒数和空值', () => {
  assert.equal(reminderClock(''), null);
  assert.equal(reminderClock('22:30'), '22:30:00');
  assert.equal(reminderClock('07:00:15'), '07:00:15');
  assert.throws(() => reminderClock('25:00'), RangeError);
  assert.throws(() => reminderClock('07:00:00.001'), /准确秒数/);
});
const report = { status: 'PASS', checks: cases, engineering_time_only: true, credentials_submitted: false,
  clinical_data_created: false, reminder_business_validation: 'NOT_RUN' };
await writeFile('../.planning-work/t54-reminder-time.json', JSON.stringify(report, null, 2) + '\n');
process.stdout.write(JSON.stringify(report) + '\n');
