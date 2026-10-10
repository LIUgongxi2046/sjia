export interface MedicalTimeView {
  precision: string;
  raw_text: string | null;
  local_date: string | null;
  local_datetime: string | null;
  instant: string | null;
  timezone_name: string | null;
  resolution_status: string;
  local_year: number | null;
  local_month: number | null;
  end_year: number | null;
  end_month: number | null;
  relative_text: string | null;
  utc_offset_minutes: number | null;
}

export interface RecordFieldView {
  medical_time_id: string | null;
  sample_time_origin: string;
  confirmation_id: string | null;
  field_id: string;
  field_code: string;
  concept_code: string | null;
  dictionary_entry_id?: string | null;
  original_name: string | null;
  value_type: string;
  value_state: string;
  raw_text: string | null;
  raw_numeric: string | null;
  categorical_value: string | null;
  qualifier: string | null;
  raw_unit: string | null;
  medical_time: MedicalTimeView | null;
  method: string | null;
  reference_interval: string | null;
  report_flag: string | null;
  verification_states: Array<{ purpose: string; status: string; version: string }>;
  source_anchor: { anchor_kind: string; page_id: string; page_ordinal: number; file_id: string; original_text: string } | null;
}

export interface RecordFactReference { field_id: string; fact_id: string; version_id: string; version: string }

export interface TherapyEventItem {
  medication_event_item_id: string; source_field_id: string; ordinal: number; medication_name: string;
  dose?: string | null; dose_unit?: string | null; concentration_text?: string | null;
  route_text?: string | null; frequency_text?: string | null; interval_value?: string | null;
  interval_unit?: 'hour' | 'day' | 'week' | 'month' | null;
}
export interface TherapyEventView {
  medication_event_id: string; source_field_id: string; previous_event_id: string | null;
  status: { value_state: 'known' | 'unknown' | 'invalid'; value: 'ongoing' | 'ended' | 'not_started' | null } | null;
  action: string | null; medical_time: MedicalTimeView | null; note?: string | null; items: TherapyEventItem[];
}

export interface RecordView {
  family_confirmation?: { confirmed_by_self: boolean; confirmed_at: string;
    scope: Array<'source' | 'use_status' | 'start_time' | 'end_time'> } | null;
  therapy_events?: TherapyEventView[];
  fact_refs: RecordFactReference[];
  patient_id: string;
  allowed_actions?: string[];
  record_id: string;
  record_version_id: string;
  kind: string;
  version: number;
  origin_type: string;
  capture_method: string;
  quality_status: string;
  recorded_at: string;
  source_organization: string | null;
  corrected_at: string | null;
  correction_reason: string | null;
  fields: RecordFieldView[];
  attachments: Array<{ file_id: string; ordinal: number }>;
  report: { external_ref: string | null; report_status: string; sample_time: MedicalTimeView | null; result_time: MedicalTimeView | null } | null;
}

export interface RecordItem {
  record_id: string;
  kind: string;
  status: string;
  current_version: number;
  created_at: string;
  detail: RecordView;
}

export interface RecordSearchPage { records: RecordItem[]; next_cursor: string | null }

export function validateRecordFactReferences(record: RecordView): void {
  const fields = new Set(record.fields.map(field => field.field_id));
  const references = new Set(record.fact_refs.map(reference => reference.field_id));
  if (fields.size !== record.fields.length || references.size !== record.fact_refs.length || fields.size !== references.size
    || record.fact_refs.some(reference => !fields.has(reference.field_id))
    || new Set(record.fact_refs.map(reference => reference.fact_id)).size !== references.size
    || new Set(record.fact_refs.map(reference => reference.version_id)).size !== references.size) {
    throw new Error('记录字段与事实版本引用不一致，本次内容未被采用。');
  }
  const events = record.therapy_events ?? [];
  if (record.family_confirmation && record.kind !== 'therapy') throw new Error('本人治疗确认与当前记录类型不一致。');
  if (events.length && record.kind !== 'therapy'
    || new Set(events.map(event => event.medication_event_id)).size !== events.length
    || events.some(event => !fields.has(event.source_field_id)
      || new Set(event.items.map(item => item.medication_event_item_id)).size !== event.items.length
      || event.items.some(item => !fields.has(item.source_field_id)))) {
    throw new Error('治疗事件与获准字段引用不一致，本次内容未被采用。');
  }
}

const labels: Record<string, string> = { temp: '体温', rash: '皮疹观察', joint_discomfort: '关节不适观察', observation_note: '观察说明', medication_name: '原药名', therapy_status: '使用状态', therapy_note: '治疗说明', therapy_start: '开始时间', therapy_end: '结束时间', therapy_source: '来源说明' };
export const originLabels: Record<string, string> = { self_report: '监护人自报', report_transcription: '报告转录', authorized_clinical: '获准临床资料' };
export function fieldLabel(field: RecordFieldView) { return field.original_name || labels[field.field_code] || field.field_code; }
export function fieldValue(field: RecordFieldView): string {
  if (field.value_state === 'unknown') return '无法确定';
  if (field.raw_numeric !== null) return `${({ eq: '=', lt: '<', le: '≤', gt: '>', ge: '≥' } as Record<string, string>)[field.qualifier ?? ''] ?? ''}${field.raw_numeric}${field.raw_unit ? ` ${field.raw_unit}` : '（单位待补充）'}`;
  if (field.categorical_value !== null) return ({ yes: '观察到', no: '未观察到', ongoing: '持续使用', ended: '已结束', not_started: '尚未使用', unknown: '无法确定' } as Record<string, string>)[field.categorical_value] ?? field.categorical_value;
  if (field.value_type === 'time') return timeLabel(field.medical_time);
  return field.raw_text ?? '未记录';
}
export function verificationLabel(field: RecordFieldView) {
  if (!field.verification_states?.length) return '未核对';
  const labels: Record<string, string> = { unverified: '未核对', pending: '待核对', verified: '已核对', needs_correction: '需补正', revoked: '已撤销' };
  return field.verification_states.map(state => `${state.purpose}：${labels[state.status] ?? state.status}`).join('；');
}
export function timeLabel(time: MedicalTimeView | null) {
  if (!time || time.precision === 'unknown') return '时间待补充';
  if (time.raw_text) return time.raw_text;
  if (time.precision === 'year' && time.local_year !== null) return `${time.local_year}年`;
  if (time.precision === 'month' && time.local_year !== null && time.local_month !== null) return `${time.local_year}年${time.local_month}月`;
  if (time.precision === 'month_range' && time.local_year !== null && time.local_month !== null && time.end_year !== null && time.end_month !== null) {
    return `${time.local_year}年${time.local_month}月至${time.end_year}年${time.end_month}月`;
  }
  if (time.precision === 'relative' && time.relative_text) return time.relative_text;
  return time.raw_text || time.local_datetime || time.local_date || time.instant || '时间待补充';
}
