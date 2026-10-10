import { Temporal } from '@js-temporal/polyfill';

export type LocalTimeChoice = 'reject' | 'earlier' | 'later';
export function reminderInstant(local: string, timezone: string, choice: LocalTimeChoice): string {
  if (!local || !timezone) throw new Error('请填写提醒时间并选择本人所在时区。');
  const plain = Temporal.PlainDateTime.from(local, { overflow: 'reject' });
  const zoned = plain.toZonedDateTime(timezone, { disambiguation: choice });
  if (!zoned.toPlainDateTime().equals(plain)) throw new Error('该时区没有这个当地时间，请重新选择提醒时间。');
  if (Temporal.Instant.compare(zoned.toInstant(), Temporal.Now.instant()) <= 0) throw new Error('提醒时间需要晚于当前时间。');
  return zoned.toInstant().toString();
}
export function reminderLocal(instant: string | null, timezone: string): string {
  return instant === null ? '' : Temporal.Instant.from(instant).toZonedDateTimeISO(timezone).toPlainDateTime().toString();
}
export function reminderClock(value: string): string | null {
  if (!value) return null;
  const time = Temporal.PlainTime.from(value, { overflow: 'reject' });
  if (time.millisecond || time.microsecond || time.nanosecond) throw new Error('安静时段需要使用准确秒数。');
  return time.toString({ smallestUnit: 'second' });
}
