import { Temporal } from '@js-temporal/polyfill';
import type { MedicalTimeView, RecordFieldView, RecordItem } from '../../api/records';

export interface ObservationReference {
  patientId: string;
  recordId: string;
  recordVersionId: string;
  field: RecordFieldView;
}
export type MedicalSelectionState = 'empty' | 'single' | 'latest' | 'same_time' | 'indeterminate';

/** Comparison bounds are internal only. Never fill missing components in the
 * stored/displayed time, use save time, or coerce raw medical values to numbers. */
function timeBounds(time: MedicalTimeView | null): [bigint, bigint] | null {
  if (!time || !['resolved', 'local_only'].includes(time.resolution_status)) return null;
  try {
    if (['second', 'minute'].includes(time.precision)) {
      if (time.resolution_status !== 'resolved' || !time.instant) return null;
      const start = Temporal.Instant.from(time.instant).epochNanoseconds;
      return [start, start + (time.precision === 'minute' ? 60_000_000_000n : 1_000_000_000n)];
    }
    const offset = time.utc_offset_minutes;
    const offsetZone = typeof offset === 'number' && Number.isInteger(offset) && Math.abs(offset) < 24 * 60
      ? `${offset < 0 ? '-' : '+'}${String(Math.floor(Math.abs(offset) / 60)).padStart(2, '0')}:${String(Math.abs(offset) % 60).padStart(2, '0')}` : null;
    const zone = time.timezone_name || offsetZone;
    if (!zone) return null;
    let start: Temporal.PlainDate, end: Temporal.PlainDate;
    if (time.precision === 'date' && time.local_date) {
      start = Temporal.PlainDate.from(time.local_date); end = start.add({ days: 1 });
    } else if (time.precision === 'year' && time.local_year !== null) {
      start = Temporal.PlainDate.from({ year: time.local_year, month: 1, day: 1 }); end = start.add({ years: 1 });
    } else if (['month', 'month_range'].includes(time.precision) && time.local_year !== null && time.local_month !== null) {
      start = Temporal.PlainDate.from({ year: time.local_year, month: time.local_month, day: 1 });
      if (time.precision === 'month_range') {
        if (time.end_year === null || time.end_month === null) return null;
        end = Temporal.PlainDate.from({ year: time.end_year, month: time.end_month, day: 1 }).add({ months: 1 });
      } else end = start.add({ months: 1 });
    } else return null;
    const bounds: [bigint, bigint] = [start.toZonedDateTime(zone).epochNanoseconds, end.toZonedDateTime(zone).epochNanoseconds];
    return bounds[0] < bounds[1] ? bounds : null;
  } catch { return null; }
}

/** Keep every possible latest field when unknown/overlapping times or ties
 * prevent choosing one. Input order, record save time and ordinal never break ties. */
export function selectLatestMedicalEntries<T extends { field: RecordFieldView }>(entries: T[]): { state: MedicalSelectionState; entries: T[] } {
  if (!entries.length) return { state: 'empty', entries: [] };
  if (entries.length === 1) return { state: 'single', entries: [...entries] };
  const bounded = entries.map(entry => ({ entry, bounds: timeBounds(entry.field.medical_time) }));
  const latestStart = bounded.reduce<bigint | null>((value, item) => item.bounds && (value === null || item.bounds[0] > value) ? item.bounds[0] : value, null);
  const candidates = bounded.filter(item => !item.bounds || latestStart === null || item.bounds[1] > latestStart);
  if (candidates.length === 1) return { state: 'latest', entries: [candidates[0].entry] };
  const first = candidates[0].bounds;
  const same = !!first && candidates.every(item => item.bounds && item.bounds[0] === first[0] && item.bounds[1] === first[1]);
  return { state: same ? 'same_time' : 'indeterminate', entries: candidates.map(item => item.entry) };
}

/** Home-only display projection. An undated record still prevents a global
 * latest claim; a separate qualified-time subset can give the family a useful
 * dated value without changing chronology, assessment gates or source data. */
export function projectHomeObservationEntries<T extends { field: RecordFieldView }>(entries: T[]) {
  const all = selectLatestMedicalEntries(entries);
  const timed = entries.filter(entry => timeBounds(entry.field.medical_time) !== null);
  const pendingTimeCount = entries.length - timed.length;
  const scopedToTimed = pendingTimeCount > 0 && timed.length > 0;
  return { all, display: scopedToTimed ? selectLatestMedicalEntries(timed) : all, scopedToTimed, pendingTimeCount };
}

export function observationEntries(records: Pick<RecordItem, 'record_id' | 'detail'>[], code: string): ObservationReference[] {
  const entries = records.flatMap(record => record.detail.fields.filter(field => field.field_code === code).map(field => ({
    patientId: record.detail.patient_id, recordId: record.record_id, recordVersionId: record.detail.record_version_id, field,
  })));
  return [...new Map(entries.map(entry => [entry.field.field_id, entry])).values()];
}

/** Current read permissions are retained. The detail page must verify these
 * immutable IDs before rendering; a newer version is never silently substituted. */
export function recordSnapshotPath({ recordId, recordVersionId, fieldId }: { recordId: string; recordVersionId: string; fieldId?: string }): string {
  const query = new URLSearchParams({ version: recordVersionId });
  if (fieldId) query.set('field', fieldId);
  return `/records/${encodeURIComponent(recordId)}?${query}`;
}
