import Ajv2020 from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import schema from '../../../backend/records/search.schema.json' with { type: 'json' };
import type { RecordSearchPage, RecordView } from './records';
import { validateRecordFactReferences } from './records.ts';
import type { AnalyteTrend, RecordHistoryView } from './endpoints';

const validator = new Ajv2020({ strict: true, allowUnionTypes: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(schema, 'record-search.schema.json');
const input = validator.getSchema('record-search.schema.json#/$defs/RecordSearchInput');
const page = validator.getSchema('record-search.schema.json#/$defs/RecordSearchPage');
if (!input || !page) throw new Error('记录查询 Schema 尚未登记。');

export function validateRecordSearchInput(value: unknown): void {
  if (!input!(value)) throw new Error('查询条件未通过 Schema 校验。');
}

export function validateRecordSearchPage(value: unknown): RecordSearchPage {
  if (!page!(value)) throw new Error('记录资料未通过 Schema 校验，本次内容未被采用。');
  const result = value as RecordSearchPage;
  result.records.forEach(record => validateRecordFactReferences(record.detail));
  return result;
}

export function validateRecordRead<T extends RecordView | RecordHistoryView>(name: 'RecordView' | 'RecordHistoryView', value: unknown): T {
  const check = validator.getSchema(`record-search.schema.json#/$defs/${name}`);
  if (!check) throw new Error('记录读取 Schema 尚未登记。');
  if (!check(value)) throw new Error('记录读取结果未通过 Schema 校验，本次内容未被采用。');
  if (name === 'RecordView') validateRecordFactReferences(value as RecordView);
  return value as T;
}

export function validateAnalyteRead(value: unknown): AnalyteTrend {
  const check = validator.getSchema('record-search.schema.json#/$defs/AnalyteTrendView');
  if (!check) throw new Error('历次检验 Schema 尚未登记。');
  if (!check(value)) throw new Error('历次检验读取结果未通过 Schema 校验，本次内容未被采用。');
  const result = value as AnalyteTrend;
  if (new Set(result.trend.map(item => item.field_id)).size !== result.trend.length) throw new Error('历次检验包含重复字段，本次内容未被采用。');
  if (result.trend.some((item, index) => item.direction !== null && (index === 0 || item.comparison_reasons.length > 0))) throw new Error('历次检验的变化方向与比较依据不一致，本次内容未被采用。');
  return result;
}

export function validateCorrectionValue<T>(name: 'RecordCorrectionInput' | 'RecordCorrectionField' | 'RecordCorrectionReceipt' | 'CorrectionDraft', value: unknown): T {
  const check = validator.getSchema(`record-search.schema.json#/$defs/${name}`);
  if (!check) throw new Error('更正 Schema 尚未登记。');
  if (!check(value)) throw new Error('更正内容未通过 Schema 校验，本次内容未被采用。');
  return value as T;
}
