// Runtime schemas for the deployed legacy wire shapes. The older design
// Bootstrap/Session models differ from these actual backend responses.
import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import protocol from '../../../backend-design/protocol.schema.json';
import type { AuthBootstrap, FieldConfigView, SessionView } from './endpoints';

const validator = new Ajv2020({ strict: true, strictTypes: false, strictRequired: false,
  allowUnionTypes: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
validator.addSchema(protocol, 'protocol.schema.json');
const uuid = { type: 'string', format: 'uuid' };
const text = { type: 'string', minLength: 1 };
const timestamp = { type: 'string', format: 'date-time' };
const version = { type: 'string', pattern: '^[1-9][0-9]*$', maxLength: 19 };
const nullableUuid = { anyOf: [uuid, { type: 'null' }] };
const strings = { type: 'array', items: text, uniqueItems: true };
const object = (properties: Record<string, unknown>, required = Object.keys(properties)) => ({
  type: 'object', additionalProperties: false, required, properties,
});
const creationReceipt = object({ patient_id: uuid, record_id: uuid, record_version_id: uuid, version: { const: 1, type: 'integer' } });
const correctionReceipt = object({ patient_id: uuid, record_id: uuid, record_version_id: uuid, version: { type: 'integer', minimum: 2 } });
const recordRequest = object({ patient_id: uuid, intent_id: uuid, request_id: uuid,
  operation_id: { enum: ['record_create', 'record_correct'] }, target_key: text,
  status: { enum: ['prepared', 'committed', 'failed'] }, generation: version,
  prepared_at: timestamp, expires_at: timestamp, resource_id: nullableUuid,
  receipt: { anyOf: [creationReceipt, correctionReceipt, { type: 'null' }] },
  may_retry: { type: 'boolean' }, record_input: { type: 'object', required: ['fields'],
    properties: { fields: { type: 'array', minItems: 1, items: { type: 'object' } } } },
});
const definitions = {
  AuthBootstrap: object({ csrf_token: text, return_paths: { type: 'array', uniqueItems: true,
    items: { type: 'string', pattern: '^/(?!/)' } }, schema_version: { const: '1.0.0' } }),
  SessionView: object({ principal_id: uuid, account_status: { const: 'active' }, expires_at: timestamp,
    audience: { enum: ['family', 'support'] }, capabilities: strings, csrf_token: text, selected_patient_id: nullableUuid,
    session_binding: { anyOf: [{ type: 'string', pattern: '^[0-9a-f]{64}$' }, { type: 'null' }], readOnly: true } },
    ['principal_id', 'account_status', 'expires_at', 'audience', 'capabilities', 'csrf_token', 'selected_patient_id']),
  PatientSelection: object({ selected_patient_id: uuid }),
  Empty: object({}),
  RecordCreationReceipt: creationReceipt,
  RecordRequest: recordRequest,
  RecordRequestList: object({ patient_id: uuid, requests: { type: 'array', maxItems: 50, items: recordRequest } }),
};
for (const [name, schema] of Object.entries(definitions)) validator.addSchema(schema, `legacy:${name}`);

export function validateLegacyResponse<T>(name: keyof typeof definitions | 'FieldConfigView' | 'IntentView', value: unknown): T {
  const ref = name === 'FieldConfigView' || name === 'IntentView' ? `protocol.schema.json#/$defs/${name}` : `legacy:${name}`;
  const check = validator.getSchema(ref);
  if (!check || !check(value)) throw new Error('接口资料未通过 Schema 校验，本次内容未被采用。');
  return value as T;
}

export function validateBootstrap(value: unknown): AuthBootstrap {
  return validateLegacyResponse('AuthBootstrap', value);
}

export function validateSession(value: unknown, audience?: 'family'): SessionView {
  const result = validateLegacyResponse<SessionView>('SessionView', value);
  if (audience && result.audience !== audience) throw new Error('登录回执与本次身份范围不一致。');
  if (result.audience === 'support' && result.selected_patient_id !== null) throw new Error('支持身份不应携带家庭患儿选择。');
  return result;
}

export function validateFieldConfig(value: unknown, patientId: string, kind: string, versionId?: string): FieldConfigView {
  const result = validateLegacyResponse<FieldConfigView>('FieldConfigView', value);
  if (result.patient_id !== patientId || result.kind !== kind || versionId !== undefined && result.config_version !== versionId)
    throw new Error('字段配置与当前患儿、表单或配置版本不一致。');
  const codes = new Set(result.fields.map(field => field.field_code));
  if (codes.size !== result.fields.length || result.watch_field_codes.some(code => !codes.has(code))
      || result.state !== 'available' && result.fields.some(field => field.writable))
    throw new Error('字段配置包含重复、越界或不可用的写入字段。');
  return result;
}

export interface RecordCreationReceipt { patient_id: string; record_id: string; record_version_id: string; version: 1 }
export function validateRecordCreationReceipt(value: unknown, patientId: string): RecordCreationReceipt {
  const result = validateLegacyResponse<RecordCreationReceipt>('RecordCreationReceipt', value);
  if (result.patient_id !== patientId) throw new Error('保存回执与本次患儿不一致。');
  return result;
}

export interface RecordIntentView { intent_id: string; request_id: string; generation: string; status: string; expires_at: string; payload_sha256: string }
export function validateRecordIntent(value: unknown, requestId: string, intentId?: string, generation?: string): RecordIntentView {
  const result = validateLegacyResponse<RecordIntentView>('IntentView', value);
  if (result.request_id !== requestId || intentId !== undefined && result.intent_id !== intentId
      || generation !== undefined && result.generation !== generation) throw new Error('提交意图与本次请求标识或版本不一致。');
  return result;
}

export interface RecordRequestView {
  patient_id: string; intent_id: string; request_id: string; operation_id: 'record_create' | 'record_correct'; target_key: string;
  status: 'prepared' | 'committed' | 'failed'; generation: string; prepared_at: string; expires_at: string;
  resource_id: string | null; receipt: Record<string, unknown> | null; may_retry: boolean; record_input: Record<string, unknown>;
}
export interface RecordRequestScope { patientId: string; operation: 'record_create' | 'record_correct'; target: string; requestId?: string; intentId?: string; kind?: string }
export function validateRecordRequest(value: unknown, scope: RecordRequestScope): RecordRequestView {
  const result = validateLegacyResponse<RecordRequestView>('RecordRequest', value);
  if (result.patient_id !== scope.patientId || result.operation_id !== scope.operation || result.target_key !== scope.target
      || scope.requestId !== undefined && result.request_id !== scope.requestId
      || scope.intentId !== undefined && result.intent_id !== scope.intentId
      || scope.kind !== undefined && result.record_input.kind !== scope.kind)
    throw new Error('原提交记录与当前患儿、表单或提交身份不一致。');
  if (result.operation_id === 'record_create' && !['observation', 'report', 'therapy'].includes(String(result.record_input.kind)))
    throw new Error('原提交表单类别无效。');
  if (result.operation_id === 'record_correct' && (!Number.isSafeInteger(result.record_input.expected_version) || Number(result.record_input.expected_version) < 1))
    throw new Error('原更正版本无效。');
  if (result.status === 'committed') {
    if (!result.receipt || result.may_retry || result.resource_id === null || result.receipt.patient_id !== scope.patientId)
      throw new Error('原提交已保存状态与回执不一致。');
    if (result.operation_id === 'record_create') {
      const receipt = validateRecordCreationReceipt(result.receipt, scope.patientId);
      if (receipt.record_id !== result.resource_id) throw new Error('原提交资源与保存回执不一致。');
    } else if (result.receipt.record_version_id !== result.resource_id
        || result.receipt.record_id !== scope.target.split('/')[3]
        || result.receipt.version !== Number(result.record_input.expected_version) + 1)
      throw new Error('原更正回执与准确记录或版本不一致。');
  } else if (result.receipt !== null || result.resource_id !== null) throw new Error('原提交状态与回执不一致。');
  return result;
}

export function validateRecordRequests(value: unknown, scope: RecordRequestScope): { patient_id: string; requests: RecordRequestView[] } {
  const result = validateLegacyResponse<{ patient_id: string; requests: unknown[] }>('RecordRequestList', value);
  if (result.patient_id !== scope.patientId) throw new Error('原提交列表与当前患儿不一致。');
  const requests = result.requests.map(row => validateRecordRequest(row, scope));
  if (new Set(requests.map(row => row.intent_id)).size !== requests.length || new Set(requests.map(row => row.request_id)).size !== requests.length)
    throw new Error('原提交列表包含重复身份。');
  return { patient_id: result.patient_id, requests };
}

/** Only the few deployed legacy read routes may use the generic resource hook. */
export async function validateLegacyResource<T>(path: string, value: unknown): Promise<T> {
  const url = new URL(path, 'https://same-origin.invalid');
  const config = /^\/api\/patients\/([^/]+)\/field-config$/.exec(url.pathname);
  if (config) {
    if (url.searchParams.getAll('kind').length !== 1 || url.searchParams.getAll('version').length > 1
        || [...url.searchParams.keys()].some(key => !['kind', 'version'].includes(key))) throw new Error('字段配置查询无效。');
    return validateFieldConfig(value, config[1], url.searchParams.get('kind')!, url.searchParams.get('version') ?? undefined) as T;
  }
  const alert = /^\/api\/alerts\/([^/]+)$/.exec(url.pathname);
  if (alert && !url.search) {
    const { validateFamilyRead } = await import('./familyReadSchema');
    const result = validateFamilyRead<{ alert_id: string }>('AlertDetailView', value);
    if (result.alert_id !== alert[1]) throw new Error('提示资料与本次对象不一致。');
    return result as T;
  }
  throw new Error('当前读取接口尚未登记响应校验。');
}
