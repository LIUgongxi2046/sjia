import { timeLabel, type MedicalTimeView } from '../api/records';

/** Presentation only: preserve the supplied event instant/offset, never use the
 * browser clock zone or reinterpret a clinical source's local time. */
export function eventTimeLabel(value: string | null | undefined): string {
  if (!value) return '未提供';
  const readable = value.replace('T', ' ');
  if (/Z$/.test(value)) return `${readable.slice(0, -1)} UTC`;
  if (/[+-]\d{2}:\d{2}$/.test(value)) return `${readable.slice(0, -6)} UTC${readable.slice(-6)}`;
  return `${readable}（时区未明确）`;
}
export function medicalSourceZoneLabel(time: MedicalTimeView | null): string {
  if (!time) return '时区未明确';
  const offset = time.utc_offset_minutes;
  const offsetLabel = typeof offset === 'number' && Number.isFinite(offset)
    ? `UTC${offset < 0 ? '-' : '+'}${String(Math.floor(Math.abs(offset) / 60)).padStart(2, '0')}:${String(Math.abs(offset) % 60).padStart(2, '0')}` : null;
  return time.timezone_name ? `${time.timezone_name}${offsetLabel ? `（${offsetLabel}）` : ''}` : offsetLabel ?? '时区未明确';
}
export function medicalSourceTimeLabel(time: MedicalTimeView | null): string {
  return time ? `${timeLabel(time)} · ${medicalSourceZoneLabel(time)}` : timeLabel(time);
}
