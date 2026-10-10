import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import schema from '../../../backend/api/family-read.schema.json';
import { validateApiValue } from './schema';

const validator = new Ajv2020({ strict: true, allowUnionTypes: true,
  coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(schema, 'family-read.schema.json');

export function validateFamilyRead<T>(name: 'HomeView' | 'AlertList' | 'AlertDetailView' | 'AlertReadReceipt' | 'DraftView' | 'DraftList', value: unknown): T {
  if (name === 'HomeView') return validateApiValue<T>('protocol.schema.json#/$defs/HomeView', value);
  const check = validator.getSchema(`family-read.schema.json#/$defs/${name}`);
  if (!check) throw new Error('家庭读取 Schema 尚未登记。');
  if (!check(value)) throw new Error('家庭资料未通过 Schema 校验，本次内容未被采用。');
  return value as T;
}
