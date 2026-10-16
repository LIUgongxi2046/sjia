import type { RecordFieldView, RecordItem } from '../../api/records';
import { selectLatestMedicalEntries } from './observationSelection';
export interface ArchiveEntry { record: RecordItem; field: RecordFieldView }
export type ArchiveCategory = 'temperature' | 'symptoms' | 'reports' | 'therapies';
export function archiveEntries(records: RecordItem[], category?: ArchiveCategory): ArchiveEntry[] {
  return records.flatMap(record => record.detail.fields.filter(field => !category
    ? ['observation', 'report', 'therapy'].includes(record.kind)
    : category === 'temperature' ? record.kind === 'observation' && field.field_code === 'temp'
    : category === 'symptoms' ? record.kind === 'observation' && field.field_code !== 'temp'
    : record.kind === (category === 'reports' ? 'report' : 'therapy')).map(field => ({ record, field })));
}
export function selectedArchiveEntries(entries: ArchiveEntry[], time: string) {
  if (time === 'latest') return selectLatestMedicalEntries(entries).entries;
  if (time === 'unknown') return entries.filter(({ field }) => !field.medical_time || field.medical_time.precision === 'unknown');
  return entries.filter(({ field }) => field.medical_time_id === time);
}
export function archiveRecordCount(entries: ArchiveEntry[]) { return new Set(entries.map(({ record }) => record.record_id)).size; }
