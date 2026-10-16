import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import optionsSchema from '../../../backend-design/content-disposition-family-options.schema.json';
import requestSchema from '../../../backend-design/content-disposition-family-request.schema.json';
import requestsSchema from '../../../backend-design/content-disposition-family-requests.schema.json';
import viewSchema from '../../../backend-design/content-disposition-family-view.schema.json';
import { checkRequestGuard, fetchJson } from './client';
import type { RequestGuard } from './client';

export interface FamilyContentTarget { record_version_id: string; version: string }
export interface FamilyContentPolicy {
  policy_id: string; version: number; mode: 'retain' | 'revoke_access' | 'erase_selected_content';
  execution_enabled: boolean; retention_seconds: number; clock: string; access_purpose: string; recheck_at: string;
}
export interface FamilyContentOptions {
  schema_version: 'content-disposition-family-options-1'; patient_id: string; default_policy: null;
  policies: FamilyContentPolicy[]; targets: Array<FamilyContentTarget & { record_id: string; recorded_at: string }>;
}
export interface FamilyContentRequestInput {
  schema_version: 'content-disposition-family-request-1'; request_id: string; policy_id: string; targets: FamilyContentTarget[];
}
export interface FamilyContentRequest {
  schema_version: 'content-disposition-family-view-1'; request_id: string; client_request_id: string;
  patient_id: string; policy_id: string; created_at: string; targets: FamilyContentTarget[];
  status: 'requested' | 'preview' | 'approved' | 'retained' | 'access_revoked' | 'erasing' | 'logical_erasure_complete';
  linked_plan_id: string | null; logical_payload_erased: boolean; all_copies_erased: false;
}
export interface FamilyContentRequests {
  schema_version: 'content-disposition-family-requests-1'; patient_id: string; requests: FamilyContentRequest[];
}

// This family bridge deliberately does not import the engine manifest or its
// support-side approval/execution API. All four public contracts are standalone.
const ajv = new Ajv2020({ strict: true, allErrors: true });
addFormats(ajv);
const checkOptions = ajv.compile<FamilyContentOptions>(optionsSchema);
const checkInput = ajv.compile<FamilyContentRequestInput>(requestSchema);
const checkRequests = ajv.compile<FamilyContentRequests>(requestsSchema);
const checkView = ajv.compile<FamilyContentRequest>(viewSchema);
export const isFamilyContentId = (value: string | null): value is string =>
  typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export const familyContentTargetKey = (target: FamilyContentTarget) => JSON.stringify([target.record_version_id, target.version]);
const unique = (values: string[]) => new Set(values).size === values.length;
function checkTargets(targets: FamilyContentTarget[]) {
  if (!targets.length || targets.length > 100 || !unique(targets.map(target => target.record_version_id)))
    throw new Error('内容处置申请的准确版本范围无效或重复。');
}
function patientPath(patientId: string) {
  if (!isFamilyContentId(patientId)) throw new Error('当前患儿标识无效。');
  return `/api/v2/patients/${encodeURIComponent(patientId)}/content-disposition`;
}
export function validateFamilyContentRequest(value: unknown, patientId: string, requestId?: string, clientId?: string): FamilyContentRequest {
  if (!checkView(value)) throw new Error('内容处置申请响应未通过版本化契约校验。');
  if (value.patient_id !== patientId || requestId !== undefined && value.request_id !== requestId
    || clientId !== undefined && value.client_request_id !== clientId) throw new Error('内容处置申请与当前患儿或原请求标识不一致。');
  checkTargets(value.targets);
  if (value.logical_payload_erased !== (value.status === 'logical_erasure_complete')
    || (value.status === 'requested') !== (value.linked_plan_id === null)) throw new Error('内容处置申请的状态与限定回执不一致。');
  return value;
}
export async function readFamilyContentOptions(patientId: string, guard: RequestGuard = {}): Promise<FamilyContentOptions> {
  const value = await fetchJson<unknown>(`${patientPath(patientId)}/options`, guard);
  if (!checkOptions(value)) throw new Error('内容处置选项未通过版本化契约校验。');
  if (value.patient_id !== patientId || !unique(value.policies.map(policy => policy.policy_id))
    || !unique(value.targets.map(target => target.record_version_id))) throw new Error('内容处置选项的患儿或准确版本范围不一致。');
  return value;
}
export async function listFamilyContentRequests(patientId: string, guard: RequestGuard = {}): Promise<FamilyContentRequests> {
  const value = await fetchJson<unknown>(`${patientPath(patientId)}/requests`, guard);
  if (!checkRequests(value)) throw new Error('内容处置申请列表未通过版本化契约校验。');
  if (value.patient_id !== patientId || !unique(value.requests.map(request => request.request_id))
    || !unique(value.requests.map(request => request.client_request_id))) throw new Error('内容处置申请列表的患儿或请求标识不一致。');
  value.requests.forEach(request => validateFamilyContentRequest(request, patientId));
  return value;
}
export async function readFamilyContentRequest(patientId: string, requestId: string, clientId: string, guard: RequestGuard = {}) {
  if (!isFamilyContentId(requestId) || !isFamilyContentId(clientId)) throw new Error('原内容处置申请标识无效。');
  return validateFamilyContentRequest(await fetchJson(`/api/v2/content-disposition/requests/${encodeURIComponent(requestId)}`, guard),
    patientId, requestId, clientId);
}
export function assertFamilyContentSelection(value: FamilyContentRequest, input: FamilyContentRequestInput) {
  const expected = input.targets.map(familyContentTargetKey).sort();
  if (value.client_request_id !== input.request_id || value.policy_id !== input.policy_id
    || JSON.stringify(value.targets.map(familyContentTargetKey).sort()) !== JSON.stringify(expected))
    throw new Error('内容处置保存结果与原申请的政策或准确版本范围不一致。');
}
export async function submitFamilyContentRequest(patientId: string, input: FamilyContentRequestInput, guard: RequestGuard = {}) {
  if (!checkInput(input)) throw new Error('内容处置申请未通过版本化契约校验。');
  checkTargets(input.targets);
  // Freeze the exact request across the asynchronous transport boundary. This
  // method performs one POST only; uncertain responses must be recovered by GET.
  const body: FamilyContentRequestInput = { ...input, targets: input.targets.map(target => ({ ...target })) };
  checkRequestGuard(guard);
  const value = validateFamilyContentRequest(await fetchJson(`${patientPath(patientId)}/requests`,
    { ...guard, method: 'POST', body }), patientId, undefined, body.request_id);
  assertFamilyContentSelection(value, body);
  return value;
}
