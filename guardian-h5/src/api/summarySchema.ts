import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import schema from '../../../backend/summaries/read.schema.json';
import recordSchema from '../../../backend/records/search.schema.json';
import relationSchema from '../../../backend/relations/read.schema.json';
import type { SummarySchema } from './summaries';

const validator = new Ajv2020({ strict: true, allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(recordSchema, 'record-search.schema.json');
validator.addSchema(relationSchema, 'relation-read.schema.json');
validator.addSchema(schema, 'summary-read.schema.json');

export function validateSummary<T>(name: SummarySchema | 'IntentView', value: unknown): T {
  const check = validator.getSchema(`${name === 'IntentView' ? 'relation' : 'summary'}-read.schema.json#/$defs/${name}`);
  if (!check) throw new Error('摘要 Schema 尚未登记。');
  if (!check(value)) throw new Error('摘要内容未通过 Schema 校验，本次内容未被采用。');
  return value as T;
}
