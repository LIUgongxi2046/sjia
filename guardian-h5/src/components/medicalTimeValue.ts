export interface TimeInput {
  precision: 'unknown' | 'month' | 'date' | 'minute';
  value: string;
  timezone: string;
  localYear?: number;
  localMonth?: number;
}

export const emptyTime: TimeInput = { precision: 'unknown', value: '', timezone: '' };

export function timeInputValue(time: TimeInput, input: Pick<HTMLInputElement, 'value' | 'valueAsDate'>, dateFromUnknown = false): TimeInput {
  const precision = dateFromUnknown && time.precision === 'unknown' && input.value ? 'date' : time.precision;
  const date = precision === 'month' || precision === 'date' ? input.valueAsDate : null;
  if (input.value && (precision === 'month' || precision === 'date') && !date) throw new Error('浏览器未确认当前输入的日期或月份');
  return { ...time, precision, value: input.value, localYear: precision === 'month' ? date?.getUTCFullYear() : undefined,
    localMonth: precision === 'month' && date ? date.getUTCMonth() + 1 : undefined };
}

export function timePayload(time: TimeInput) {
  if (!['unknown', 'month', 'date', 'minute'].includes(time.precision)) throw new Error('当前时间控件不支持此精度');
  if (time.precision === 'unknown') return null;
  if (!time.value) throw new Error('请填写已选择精度的时间');
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
  return { precision: time.precision, raw_text: time.value, local_year: null, local_month: null, end_year: null, end_month: null,
    relative_text: null, local_date: time.precision === 'date' ? time.value : null, local_datetime: time.precision === 'minute' ? time.value : null,
    timezone_name: time.timezone || null, utc_offset_minutes: null, instant: null, resolution_status: 'local_only' };
}
