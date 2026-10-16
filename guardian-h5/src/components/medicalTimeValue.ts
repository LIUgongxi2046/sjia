import type { MedicalTime, MedicalTimeInput } from '../../../frontend-design/types';

export interface TimeInput {
  precision: MedicalTimeInput['precision'];
  value: string;
  timezone: string;
  localYear?: number;
  localMonth?: number;
  utcOffsetMinutes?: number | null;
  preserved?: Required<MedicalTimeInput>;
}

export const emptyTime: TimeInput = { precision: 'unknown', value: '', timezone: '' };

export function isTimeInputValid(time: TimeInput): boolean {
  try { timePayload(time); return true; } catch { return false; }
}

export function editableMedicalTime(value: MedicalTime | MedicalTimeInput | null): TimeInput {
  if (!value) return { ...emptyTime };
  const { precision, raw_text, local_year, local_month, end_year, end_month, relative_text,
    local_date, local_datetime, timezone_name, utc_offset_minutes, resolution_status } = value;
  const month = (year: number | null | undefined, month: number | null | undefined) => `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}`;
  const text = precision === 'year' ? String(local_year) : precision === 'month' ? month(local_year, local_month)
    : precision === 'month_range' ? `${month(local_year, local_month)}/${month(end_year, end_month)}`
    : precision === 'relative' ? relative_text ?? '' : precision === 'date' ? local_date ?? ''
    : precision === 'minute' || precision === 'second' ? local_datetime ?? '' : raw_text ?? '';
  return { precision, value: text, timezone: timezone_name ?? '', localYear: local_year ?? undefined,
    localMonth: local_month ?? undefined, utcOffsetMinutes: utc_offset_minutes,
    preserved: { precision, raw_text, local_year: local_year ?? null, local_month: local_month ?? null,
      end_year: end_year ?? null, end_month: end_month ?? null, relative_text: relative_text ?? null,
      local_date, local_datetime, timezone_name, utc_offset_minutes, instant: null,
      resolution_status: resolution_status === 'resolved' ? 'local_only' : resolution_status } };
}

export function timeInputValue(time: TimeInput, input: Pick<HTMLInputElement, 'value' | 'valueAsDate'>, dateFromUnknown = false): TimeInput {
  const precision = dateFromUnknown && time.precision === 'unknown' && input.value ? 'date' : time.precision;
  const date = precision === 'month' || precision === 'date' ? input.valueAsDate : null;
  if (input.value && (precision === 'month' || precision === 'date') && !date) throw new Error('浏览器未确认当前输入的日期或月份');
  return { ...time, preserved: undefined, precision, value: input.value, localYear: precision === 'month' ? date?.getUTCFullYear() : undefined,
    localMonth: precision === 'month' && date ? date.getUTCMonth() + 1 : undefined };
}

export function timePayload(time: TimeInput): Required<MedicalTimeInput> | null {
  if (time.preserved) return structuredClone(time.preserved);
  if (!['unknown', 'year', 'month', 'month_range', 'relative', 'date', 'minute', 'second'].includes(time.precision)) throw new Error('当前时间控件不支持此精度');
  if (time.precision === 'unknown') return null;
  if (!time.value) throw new Error('请填写已选择精度的时间');
  const base: Required<MedicalTimeInput> = { precision: time.precision, raw_text: time.value,
    local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null,
    local_date: null, local_datetime: null, timezone_name: null, utc_offset_minutes: null,
    instant: null, resolution_status: 'local_only' };
  if (time.precision === 'year') {
    if (!/^\d{4}$/.test(time.value) || Number(time.value) < 1) throw new Error('请填写有效的四位年份');
    return { ...base, local_year: Number(time.value) };
  }
  if (time.precision === 'month_range') {
    const range = /^(\d{4})-(\d{2})\/(\d{4})-(\d{2})$/.exec(time.value);
    if (!range) throw new Error('请填写月份范围，例如 2026-01/2026-03');
    const [, firstYear, firstMonth, lastYear, lastMonth] = range;
    if (![firstYear, lastYear].every(value => Number(value) >= 1 && Number(value) <= 9999)
      || ![firstMonth, lastMonth].every(value => Number(value) >= 1 && Number(value) <= 12)
      || `${firstYear}-${firstMonth}` > `${lastYear}-${lastMonth}`) throw new Error('月份范围无效');
    return { ...base, local_year: Number(firstYear), local_month: Number(firstMonth), end_year: Number(lastYear), end_month: Number(lastMonth) };
  }
  if (time.precision === 'relative') {
    if (!time.value.trim()) throw new Error('请填写原文中的相对时间');
    return { ...base, relative_text: time.value };
  }
  if (time.precision === 'month') {
    if (!Number.isInteger(time.localYear) || !Number.isInteger(time.localMonth)
      || time.localYear! < 1 || time.localYear! > 9999 || time.localMonth! < 1 || time.localMonth! > 12
      || time.value !== `${String(time.localYear).padStart(4, '0')}-${String(time.localMonth).padStart(2, '0')}`) {
      throw new Error('月份与浏览器确认的年份、月份不一致');
    }
    return { precision: 'month', raw_text: time.value, local_year: time.localYear!, local_month: time.localMonth!,
      end_year: null, end_month: null, relative_text: null, local_date: null, local_datetime: null,
      timezone_name: null, utc_offset_minutes: null, instant: null, resolution_status: 'local_only' };
  }
  return { ...base, local_date: time.precision === 'date' ? time.value : null,
    local_datetime: time.precision === 'minute' || time.precision === 'second' ? time.value : null,
    timezone_name: time.timezone || null, utc_offset_minutes: time.utcOffsetMinutes ?? null };
}
