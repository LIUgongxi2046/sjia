import { emptyTime, type TimeInput } from '../../components/medicalTimeValue';

/** The absence of symptomTimes identifies a legacy shared-time draft. */
export interface ObservationDraft extends Record<string, unknown> {
  temp: string;
  tempUnit: string;
  description: string;
  measuredTime: TimeInput;
  observedTime: TimeInput;
  method: string;
  symptoms?: Record<string, string>;
  voiceApplied?: boolean;
  symptomTimes?: Record<string, TimeInput>;
  inheritedSymptomTimes?: string[];
}

export function emptyObservationDraft(): ObservationDraft {
  return { temp: '', tempUnit: '尚未选择', description: '', measuredTime: { ...emptyTime },
    observedTime: { ...emptyTime }, method: '', symptoms: {}, voiceApplied: false, symptomTimes: {} };
}

const precisions = ['unknown', 'year', 'month', 'month_range', 'relative', 'date', 'minute', 'second'];
const isObject = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const has = (value: object, key: string) => Object.hasOwn(value, key);
const onlyKeys = (value: object, keys: string[]) => Object.keys(value).every(key => keys.includes(key));
const fieldCode = (key: string) => key.length > 0 && !['__proto__', 'constructor', 'prototype'].includes(key);

/** Structural validation only: unfinished time values are valid draft content. */
function isDraftTime(value: unknown): value is TimeInput {
  if (!isObject(value) || (typeof value.precision !== 'string' || !precisions.includes(value.precision)) || typeof value.value !== 'string' || typeof value.timezone !== 'string'
    || !onlyKeys(value, ['precision', 'value', 'timezone', 'localYear', 'localMonth', 'utcOffsetMinutes', 'preserved'])) return false;
  for (const key of ['localYear', 'localMonth', 'utcOffsetMinutes']) {
    if (has(value, key) && value[key] !== undefined && !(key === 'utcOffsetMinutes' && value[key] === null) && !Number.isInteger(value[key])) return false;
  }
  if (value.preserved === undefined) return true;
  const original = value.preserved;
  const strings = ['raw_text', 'relative_text', 'local_date', 'local_datetime', 'timezone_name'];
  const integers = ['local_year', 'local_month', 'end_year', 'end_month', 'utc_offset_minutes'];
  return isObject(original) && typeof original.precision === 'string' && precisions.includes(original.precision)
    && onlyKeys(original, ['precision', ...strings, ...integers, 'instant', 'resolution_status'])
    && ['raw_text', 'local_date', 'local_datetime', 'timezone_name', 'utc_offset_minutes', 'instant', 'resolution_status'].every(key => has(original, key))
    && strings.every(key => !has(original, key) || original[key] === null || typeof original[key] === 'string')
    && integers.every(key => !has(original, key) || original[key] === null || Number.isInteger(original[key]))
    && original.instant === null && typeof original.resolution_status === 'string' && ['unknown', 'local_only', 'ambiguous', 'invalid'].includes(original.resolution_status);
}

export function restoreObservationDraft(value: unknown): ObservationDraft {
  const invalid = () => { throw new Error('观察草稿格式不受支持，未恢复或更改原草稿。请返回草稿列表核对。'); };
  if (!isObject(value) || !onlyKeys(value, ['temp', 'tempUnit', 'description', 'measuredTime', 'observedTime', 'method', 'symptoms', 'voiceApplied', 'symptomTimes', 'inheritedSymptomTimes'])
    || !['temp', 'tempUnit', 'description', 'method'].every(key => typeof value[key] === 'string')
    || !isDraftTime(value.measuredTime) || !isDraftTime(value.observedTime)) return invalid();
  if (has(value, 'voiceApplied') && typeof value.voiceApplied !== 'boolean') return invalid();
  if (has(value, 'symptoms') && (!isObject(value.symptoms) || !Object.entries(value.symptoms).every(([code, state]) => fieldCode(code) && typeof state === 'string'))) return invalid();
  if (has(value, 'symptomTimes') && (!isObject(value.symptomTimes) || !Object.entries(value.symptomTimes).every(([code, time]) => fieldCode(code) && isDraftTime(time)))) return invalid();
  if (has(value, 'inheritedSymptomTimes') && (!isObject(value.symptomTimes) || !Array.isArray(value.inheritedSymptomTimes)
    || new Set(value.inheritedSymptomTimes).size !== value.inheritedSymptomTimes.length
    || !value.inheritedSymptomTimes.every(code => typeof code === 'string' && has(value.symptomTimes as object, code) && isObject(value.symptoms) && has(value.symptoms, code)))) return invalid();
  // Do not normalize or add optional keys: useFormDraft compares this exact shape.
  return value as ObservationDraft;
}

/** Only this explicit action imports the old time; new/unselected symptoms stay unknown. */
export function separateObservationTimes(value: ObservationDraft): ObservationDraft {
  if (value.symptomTimes) return value;
  const codes = Object.keys(value.symptoms ?? {});
  return { ...value, symptomTimes: Object.fromEntries(codes.map(code => [code, structuredClone(value.observedTime)])), inheritedSymptomTimes: codes };
}
