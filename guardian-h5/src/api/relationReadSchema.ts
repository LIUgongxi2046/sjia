import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import schema from '../../../backend/relations/read.schema.json';

const validator = new Ajv2020({ strict: true, allowUnionTypes: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(schema, 'relation-read.schema.json');

export type RelationReadSchema = 'RelationList' | 'RelationView' | 'ApplicationList' | 'ApplicationView'
  | 'ApplicationInput' | 'ApplicationAction' | 'WriteReceipt' | 'IntentView' | 'ApplicationRequestView' | 'ApplicationRequestList';

export function validateRelationRead<T>(name: RelationReadSchema, value: unknown): T {
  const check = validator.getSchema(`relation-read.schema.json#/$defs/${name}`);
  if (!check) throw new Error('关联读取 Schema 尚未登记。');
  if (!check(value)) throw new Error('关联资料未通过 Schema 校验，本次内容未被采用。');
  return value as T;
}
