import Decimal from 'decimal.js';
import type { RecordFieldView } from '../../api/records.ts';

export function correctionFields(fields: readonly RecordFieldView[]): Array<Record<string, unknown>> {
  if (!fields.length || new Set(fields.map(field => field.field_id)).size !== fields.length) throw new Error('更正字段不完整或包含重复对象。');
  return fields.map(field => ({
    field_code: field.field_code, concept_code: field.concept_code, original_name: field.original_name,
    value_type: field.value_type, value_state: field.value_state, raw_text: field.raw_text,
    raw_numeric: field.raw_numeric, categorical_value: field.categorical_value, qualifier: field.qualifier,
    raw_unit: field.raw_unit, method: field.method, reference_interval: field.reference_interval,
    report_flag: field.report_flag, sample_time_origin: field.sample_time_origin,
    medical_time: field.medical_time === null ? null : {
      precision: field.medical_time.precision, raw_text: field.medical_time.raw_text,
      local_date: field.medical_time.local_date, local_datetime: field.medical_time.local_datetime,
      instant: field.medical_time.instant, timezone_name: field.medical_time.timezone_name,
      resolution_status: field.medical_time.resolution_status, local_year: field.medical_time.local_year,
      local_month: field.medical_time.local_month, end_year: field.medical_time.end_year,
      end_month: field.medical_time.end_month, relative_text: field.medical_time.relative_text,
      utc_offset_minutes: field.medical_time.utc_offset_minutes,
    },
  }));
}

export function correctionContentMatches(expected: readonly Record<string, unknown>[], actual: readonly RecordFieldView[]): boolean {
  const stored = correctionFields(actual);
  if (expected.length !== stored.length) return false;
  return expected.every((field, index) => Object.keys(field).every(key => {
    if (key === 'raw_numeric') {
      const before = field[key];
      const after = stored[index][key];
      if (before === null || after === null) return before === after;
      if (typeof before !== 'string' || typeof after !== 'string') return false;
      return new Decimal(before).equals(after);
    }
    if (key !== 'medical_time') return JSON.stringify(field[key]) === JSON.stringify(stored[index][key]);
    const before = field.medical_time as Record<string, unknown> | null;
    const after = stored[index].medical_time as Record<string, unknown> | null;
    if (before === null || after === null) return before === after;
    return Object.keys(before).every(timeKey => {
      if (timeKey !== 'local_datetime' || before[timeKey] === after[timeKey]) return before[timeKey] === after[timeKey];
      if (before.precision !== 'minute' || typeof before[timeKey] !== 'string' || typeof after[timeKey] !== 'string') return false;
      const expectedDate = Date.parse(before[timeKey]);
      const actualDate = Date.parse(after[timeKey]);
      return Number.isFinite(expectedDate) && Number.isFinite(actualDate) && expectedDate === actualDate;
    });
  }));
}
