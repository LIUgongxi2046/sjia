import Ajv2020 from 'ajv/dist/2020.js';
import reportSchema from '../../../../backend/recognition/report.schema.json' with { type: 'json' };
import { emptyTime, timePayload, type TimeInput } from '../../components/medicalTimeValue.ts';

export interface ReportItemInput {
  name: string;
  value: string | number | null;
  comparator: string | null;
  unit: string | null;
  reference: string | null;
  method?: string | null;
  photo_id?: string;
  source_text?: string;
  sampleTime?: TimeInput;
  dictionaryEntry?: { id: string; concept_code: string; version: string } | null;
}

const validateDecimal = new Ajv2020({ strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false }).compile(reportSchema.$defs.decimal);

export function reportFieldValue(item: ReportItemInput) {
  const raw = item.value === null || String(item.value).trim() === '' ? null : String(item.value);
  const numeric = raw !== null && validateDecimal(raw);
  const qualifiers: Record<string, string> = { '=': 'eq', '<': 'lt', '≤': 'le', '<=': 'le', '>': 'gt', '≥': 'ge', '>=': 'ge' };
  if (item.comparator && !Object.hasOwn(qualifiers, item.comparator)) throw new Error('原报告比较符不受支持');
  if (item.photo_id && !item.source_text?.trim()) throw new Error('本项目缺少对应原文');
  const medicalTime = timePayload(item.sampleTime ?? emptyTime);
  return {
    field_code: 'report_item', original_name: item.name,
    concept_code: item.dictionaryEntry?.concept_code ?? null, dictionary_entry_id: item.dictionaryEntry?.id ?? null,
    value_type: 'decimal', value_state: raw === null ? 'unknown' : numeric ? 'known' : 'invalid',
    raw_text: raw, raw_numeric: numeric ? raw : null, qualifier: item.comparator ? qualifiers[item.comparator] : null,
    raw_unit: item.unit, reference_interval: item.reference || null, method: item.method || null,
    medical_time: medicalTime, sample_time_origin: medicalTime ? 'item' : 'unknown',
    source_file_id: item.photo_id ?? null, source_text: item.source_text ?? null,
  };
}
