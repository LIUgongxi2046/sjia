import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import directorySchema from '../../../backend-design/alert-preset-view.schema.json';
import equal from 'fast-deep-equal';
import { ApiError, checkRequestGuard, fetchJson, type RequestGuard } from './client';
import { validateRecordIntent, type RecordIntentView } from './legacyResponseSchema';

export interface AlertPresetInput { assignment_id: string; assignment_checksum: string; expected_version: string; schedule_config_id: string }
export interface AlertPresetOption { schedule_config_id: string; label: string; description: string; preset_key: string; config_version: string }
export interface AlertPresetsView {
  schema_version: 'patient-alert-presets-view-1'; patient_id: string; assignment_id: string; assignment_checksum: string;
  assignment_version: string; display_name: string; purpose: 'family_observation'; selection_version: string;
  selected_schedule_config_id: string | null; selection_state: 'selected' | 'professional_default' | 'requires_reselection'; options: AlertPresetOption[];
}
export interface AlertPresetReceipt { resource_type: 'alert_preset_choice'; resource_id: string; version: string; saved_at: string; request_id: string }
export interface AlertPresetRequest {
  patient_id: string; intent_id: string; request_id: string; operation_id: 'alert_preset_select'; target_key: string;
  prepared_at: string; state: 'pending' | 'failed' | 'committed' | 'unknown'; may_retry: boolean; receipt: AlertPresetReceipt | null;
  error_code: string | null; generation: string; expires_at: string; payload_sha256: string; preset_input: AlertPresetInput;
}
const operation = 'alert_preset_select';
const uuid = { type: 'string', format: 'uuid' }, text = { type: 'string', minLength: 1 }, timestamp = { type: 'string', format: 'date-time' };
const version = { type: 'string', pattern: '^[1-9][0-9]*$', maxLength: 19 };
const zeroVersion = { type: 'string', pattern: '^(0|[1-9][0-9]*)$', maxLength: 19 };
const digest = { type: 'string', pattern: '^[0-9a-f]{64}$' };
const object = (properties: Record<string, unknown>) => ({ type: 'object', additionalProperties: false, required: Object.keys(properties), properties });
const input = object({ assignment_id: uuid, assignment_checksum: digest, expected_version: zeroVersion, schedule_config_id: uuid });
const receipt = object({ resource_type: { const: 'alert_preset_choice' }, resource_id: uuid, version, saved_at: timestamp, request_id: uuid });
const validator = new Ajv2020({ strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
addFormats(validator);
const schemas = {
  uuid: validator.compile(uuid), input: validator.compile(input), receipt: validator.compile(receipt),
  view: validator.compile(directorySchema),
  request: validator.compile(object({ patient_id: uuid, intent_id: uuid, request_id: uuid, operation_id: { const: operation }, target_key: text,
    prepared_at: timestamp, state: { enum: ['pending', 'failed', 'committed', 'unknown'] }, may_retry: { type: 'boolean' },
    receipt: { anyOf: [receipt, { type: 'null' }] }, error_code: { anyOf: [text, { type: 'null' }] }, generation: version,
    expires_at: timestamp, payload_sha256: digest, preset_input: input })),
};
function checked<T>(name: keyof typeof schemas, value: unknown): T {
  if (!schemas[name](value)) throw new Error('预警方案资料未通过格式核验，本次内容未被采用。');
  return value as T;
}
export function alertPresetTarget(patientId: string) { checked('uuid', patientId); return `/api/patients/${patientId}/alert-preset-selection`; }
export function validateAlertPresets(value: unknown, patientId: string): AlertPresetsView {
  const result = checked<AlertPresetsView>('view', value);
  if ([result.assignment_version, result.selection_version, ...result.options.map(option => option.config_version)]
    .some(value => BigInt(value) > 9223372036854775807n)) throw new Error('预警方案版本超出可核验范围。');
  if (result.patient_id !== patientId || new Set(result.options.map(option => option.schedule_config_id)).size !== result.options.length
      || result.selected_schedule_config_id !== null && !result.options.some(option => option.schedule_config_id === result.selected_schedule_config_id)
      || result.selection_state === 'requires_reselection' && result.selected_schedule_config_id !== null
      || result.selection_state !== 'requires_reselection' && result.options.length > 0 && result.selected_schedule_config_id === null
      || result.selection_state === 'professional_default' && result.selection_version !== '0'
      || result.selection_state !== 'professional_default' && result.selection_version === '0') throw new Error('预警方案与当前患儿、选择或版本不一致。');
  return result;
}
export async function readAlertPresets(patientId: string, guard: RequestGuard = {}) {
  alertPresetTarget(patientId);
  return validateAlertPresets(await fetchJson(`/api/patients/${patientId}/alert-presets`, guard), patientId);
}
export function validateAlertPresetReceipt(value: unknown, requestId: string, payload: AlertPresetInput): AlertPresetReceipt {
  const result = checked<AlertPresetReceipt>('receipt', value);
  if (result.request_id !== requestId || BigInt(result.version) !== BigInt(payload.expected_version) + 1n) throw new Error('方案保存回执与原请求或版本不一致。');
  return result;
}
function validateAlertPresetRequest(value: unknown, patientId: string, requestId: string) {
  checked('uuid', requestId);
  const target = alertPresetTarget(patientId);
  const result = checked<AlertPresetRequest>('request', value);
  if (result.patient_id !== patientId || result.request_id !== requestId || result.target_key !== target
      || (result.state === 'committed') !== (result.receipt !== null)
      || ['committed', 'unknown'].includes(result.state) && result.may_retry
      || result.state === 'failed' && !result.may_retry && result.error_code !== 'PRESET_SELECTION_SUPERSEDED') throw new Error('方案原请求的患儿、身份或结果状态不一致。');
  if (result.receipt) validateAlertPresetReceipt(result.receipt, requestId, result.preset_input);
  return result;
}
export async function readAlertPresetRequest(patientId: string, requestId: string, guard: RequestGuard = {}) {
  checked('uuid', requestId);
  const target = alertPresetTarget(patientId);
  const query = new URLSearchParams({ lookup: 'request_id', operation_id: operation, target_key: target });
  return validateAlertPresetRequest(await fetchJson(`/api/requests/${requestId}?${query}`, guard), patientId, requestId);
}

/** A frozen wire payload and request ID; even a lost prepare response retains both. */
export function createAlertPresetSubmission(patientId: string, payload: AlertPresetInput, guard: RequestGuard, original?: AlertPresetRequest, originalRequestId?: string) {
  checked('input', payload);
  const target = alertPresetTarget(patientId), body = JSON.parse(JSON.stringify(payload)) as AlertPresetInput;
  const requestId = original?.request_id ?? originalRequestId ?? crypto.randomUUID();
  checked('uuid', requestId);
  if (original) validateAlertPresetRequest(original, patientId, requestId);
  if (original && (original.patient_id !== patientId || original.target_key !== target || original.operation_id !== operation || !equal(original.preset_input, body))) throw new Error('原方案请求与当前患儿或正文不一致。');
  let intent: RecordIntentView | null = original ? requestIntent(original) : null;
  let outcome: AlertPresetReceipt | null = original?.receipt ?? null;
  let attempted = Boolean(original || originalRequestId), uncertain = Boolean(original || originalRequestId), pending: Promise<AlertPresetReceipt> | null = null;
  function compare(current: AlertPresetRequest) {
    validateAlertPresetRequest(current, patientId, requestId);
    if (!equal(current.preset_input, body) || intent && (current.intent_id !== intent.intent_id || current.payload_sha256 !== intent.payload_sha256
      || BigInt(current.generation) < BigInt(intent.generation))) throw new Error('方案原请求正文、摘要或代次不一致。');
    return current;
  }
  async function query() {
    const current = compare(await readAlertPresetRequest(patientId, requestId, guard));
    checkRequestGuard(guard); intent = requestIntent(current); outcome = current.receipt;
    return current;
  }
  async function prepare(csrfToken?: string) {
    const value = validateRecordIntent(await fetchJson('/api/write-intents', { ...guard, method: 'POST', csrfToken,
      body: { operation_id: operation, target_key: target, request_id: requestId, schema_version: '1.0.0', payload: body } }), requestId, intent?.intent_id);
    checkRequestGuard(guard);
    if (intent && intent.payload_sha256 !== value.payload_sha256) throw new Error('方案原请求摘要发生变化。');
    intent = value;
  }
  return {
    requestId, assertRequest: compare, get hasIntent() { return intent !== null; }, get uncertain() { return uncertain; }, get payload() { return JSON.parse(JSON.stringify(body)) as AlertPresetInput; }, query,
    submit(csrfToken?: string): Promise<AlertPresetReceipt> {
      try { checkRequestGuard(guard); } catch (failure) { return Promise.reject(failure); }
      if (outcome) return Promise.resolve(outcome);
      if (pending) return pending;
      const recovering = attempted; attempted = true;
      pending = (async () => {
        try {
          if (recovering) {
            let current: AlertPresetRequest | null = null;
            try { current = await query(); }
            catch (failure) {
              // A missing prepare response can only replay this exact ID/body.
              if (!(failure instanceof ApiError && failure.status === 404 && failure.code === 'RESOURCE_NOT_FOUND' && !intent)) throw failure;
            }
            checkRequestGuard(guard);
            if (current?.receipt) return current.receipt;
            if (current?.state === 'unknown') throw new Error('原请求结果仍未明确，请稍后继续核对，勿重新选择。');
            if (current?.may_retry) {
              const renewed = validateRecordIntent(await fetchJson(`/api/write-intents/${current.intent_id}/retry`, {
                ...guard, method: 'POST', csrfToken, body: { expected_generation: current.generation },
              }), requestId, current.intent_id, String(BigInt(current.generation) + 1n));
              checkRequestGuard(guard);
              if (renewed.payload_sha256 !== current.payload_sha256) throw new Error('方案重试摘要与原请求不一致。');
              intent = renewed;
            } else if (current && (current.state !== 'pending' || Date.parse(current.expires_at) <= Date.now())) {
              throw new Error('原请求已过期或暂不可继续，请重新核对本次请求。');
            }
          }
          if (!intent) await prepare(csrfToken);
          checkRequestGuard(guard);
          const prepared = intent!;
          // Once submission starts, every failure retains the original request.
          uncertain = true;
          const saved = validateAlertPresetReceipt(await fetchJson(target, { ...guard, method: 'POST', csrfToken, body,
            writeIntent: prepared.intent_id, idempotencyKey: requestId, intentGeneration: prepared.generation }), requestId, body);
          checkRequestGuard(guard); outcome = saved; uncertain = false; return saved;
        } catch (failure) {
          if (recovering || !(failure instanceof ApiError) || failure.status >= 500 || failure.status === 408) uncertain = true;
          throw failure;
        } finally { pending = null; }
      })();
      return pending;
    },
  };
}
function requestIntent(value: AlertPresetRequest): RecordIntentView {
  return { intent_id: value.intent_id, request_id: value.request_id, generation: value.generation,
    status: value.state === 'committed' ? 'committed' : value.state === 'failed' ? 'failed' : 'prepared',
    expires_at: value.expires_at, payload_sha256: value.payload_sha256 };
}
