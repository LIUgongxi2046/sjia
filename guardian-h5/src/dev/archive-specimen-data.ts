import type { MedicalTimeView, RecordFieldView, RecordItem } from '../api/records';

// Static invented display fixtures only. Never imported by production routes.
const id = (value: number) => `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`;
const patient = id(1), at = '2026-09-27T11:40:00Z';
const time: MedicalTimeView = { precision: 'minute', raw_text: '09/27 19:40（合成时间）', local_date: null, local_datetime: '2026-09-27T19:40:00', instant: at, timezone_name: 'Asia/Shanghai', resolution_status: 'resolved', local_year: null, local_month: null, end_year: null, end_month: null, relative_text: null, utc_offset_minutes: 480 };
function field(index: number, code: string, values: Partial<RecordFieldView> = {}): RecordFieldView {
  return { field_id: id(index), field_code: code, concept_code: null, original_name: null, value_type: 'categorical', value_state: 'known', raw_text: null, raw_numeric: null, categorical_value: 'yes', qualifier: null, raw_unit: null, medical_time: time, medical_time_id: id(2), sample_time_origin: 'observation', method: null, reference_interval: null, report_flag: null, verification_states: [], confirmation_id: null, source_anchor: null, ...values };
}
function record(index: number, kind: string, fields: RecordFieldView[]): RecordItem {
  return { record_id: id(index), kind, status: 'active', current_version: 1, created_at: at, detail: {
    patient_id: patient, record_id: id(index), record_version_id: id(index + 100), kind, version: 1,
    origin_type: 'self_report', capture_method: 'manual', quality_status: 'pending', recorded_at: at,
    source_organization: '合成界面样本，非真实业务结果', corrected_at: null, correction_reason: null,
    fields, fact_refs: fields.map(value => ({ field_id: value.field_id, fact_id: id(Number(value.field_id.slice(-12)) + 1000), version_id: id(Number(value.field_id.slice(-12)) + 2000), version: '1' })), attachments: [], report: null,
  } };
}
export const archiveSpecimenRecords: RecordItem[] = [
  record(10, 'observation', [field(11, 'temp', { value_type: 'decimal', raw_numeric: '38.2', raw_unit: '°C', categorical_value: null }), field(12, 'rash'), field(13, 'joint_discomfort', { value_state: 'unknown', categorical_value: null })]),
  record(20, 'report', [field(21, 'synthetic_report_1', { original_name: '合成检验项目一' }), field(22, 'synthetic_report_2', { original_name: '合成检验项目二' }), field(23, 'synthetic_report_3', { original_name: '合成检验项目三' })]),
  record(30, 'therapy', [field(31, 'medication_name', { value_type: 'text', raw_text: '合成治疗文字，非药物建议', categorical_value: null })]),
];
