import type { ApprovalInput, ConfigInput, ContactInput, GateAction, IntentInput, IntentView, ReleaseInput,
  RequestView, StateAction, ValidationInput, WriteReceipt } from '../../../frontend-design/types';
import { fetchJson } from './client';
import { compileApiSchemas, validateApiValue } from './schema';

export type PublicationAction = 'manage_configs' | 'approve_config' | 'register_validation' | 'publish_config' | 'manage_contacts' | 'manage_config';
export type PublicationCollection = 'release' | 'config' | 'contact' | 'source';
export interface PublicationItem { id: string; title: string; version: string | null; status: string;
  kind: string | null; schema_version: string | null; checksum: string | null }
export interface PublicationDirectory { environment: string; gate_version: string; publication_epoch: string;
  active_release_id: string | null; collection: PublicationCollection; schemas: { kind: string; schema_version: string }[];
  items: PublicationItem[]; next_cursor: string | null; snapshot_version: string; capability_state: 'available' | 'unavailable'; capability_reason: string | null }
export interface ConfigDetail { resource: { id: string; version: string; checksum: string;
  status: 'draft' | 'pending' | 'approved' | 'enabled' | 'disabled'; config: ConfigInput };
  artifact_sha256: string; approvals: { id: string; approved_at: string; revoked_at: string | null; scope: object }[] }
export interface ContactDetail { id: string; version: string; status: 'draft' | 'approved' | 'active' | 'disabled';
  owner_id: string; verified_at: string | null; updated_at: string; contact: ContactInput }
export interface ReleaseDetail { resource: { release_id: string; version: string; status: 'draft' | 'approved' | 'enabled' | 'disabled';
  publication_epoch: string; release: ReleaseInput }; gate_version: string; publication_epoch: string; active_release_id: string | null;
  readiness: { key: 'verification' | 'rules' | 'validation' | 'content' | 'contact' | 'notification'; state: 'available' | 'blocked'; reason: string | null }[];
  qualification_reason: string | null; may_enable: boolean; approved_text: string | null;
  approved_contents?: { rule_id: string; content_id: string; approved_text: string | null }[]; recipient_permission_state: 'individual_check_required' }

export const publicationOperations = {
  source_register: ['manage_config', 'SourceInput', 'source_registry', '登记来源'],
  source_revision_register: ['manage_config', 'SourceRevisionInput', 'source_revision', '登记来源版本'],
  source_revision_approve: ['manage_config', 'StateAction', 'source_revision', '批准来源版本'],
  source_revision_revoke: ['manage_config', 'StateAction', 'source_revision', '撤销来源版本'],
  retention_policy_create: ['manage_config', 'RetentionInput', 'retention_policy', '保存保留政策'],
  retention_policy_activate: ['manage_config', 'StateAction', 'retention_policy', '批准并启用保留政策'],
  config_create: ['manage_configs', 'ConfigInput', 'config', '保存配置'],
  config_submit: ['manage_configs', 'StateAction', 'config', '提交配置'],
  config_approve: ['approve_config', 'ApprovalInput', 'config_approval', '批准配置'],
  config_validate: ['register_validation', 'ValidationInput', 'config_validation', '登记实际验证'],
  config_enable: ['publish_config', 'GateAction', 'config', '启用配置'],
  config_disable: ['publish_config', 'GateAction', 'config', '停用配置'],
  config_approval_revoke: ['approve_config', 'StateAction', 'config_approval', '撤销配置批准'],
  release_create: ['publish_config', 'ReleaseInput', 'release', '保存发布版本'],
  release_approve: ['publish_config', 'StateAction', 'release', '批准发布版本'],
  release_enable: ['publish_config', 'GateAction', 'release', '启用发布版本'],
  release_disable: ['publish_config', 'GateAction', 'release', '停用发布版本'],
  contact_create: ['manage_contacts', 'ContactInput', 'contact_version', '保存联系版本'],
  contact_activate: ['manage_contacts', 'GateAction', 'contact_version', '启用联系版本'],
  contact_disable: ['manage_contacts', 'StateAction', 'contact_version', '停用联系版本'],
} as const;
export type PublicationOperation = keyof typeof publicationOperations;
export type PublicationPayload = ConfigInput | ApprovalInput | ValidationInput | ContactInput | ReleaseInput | GateAction | StateAction | Record<string, unknown>;
export interface PublicationIdentity extends IntentInput { operation_id: PublicationOperation; payload: PublicationPayload }
const protocol = 'protocol.schema.json#/$defs/';
const schema = 'publication.schema.json#/$defs/';

export function checkPublicationSchemas() {
  return compileApiSchemas(['Directory', 'ConfigDetail', 'ContactDetail', 'ReleaseDetail', 'RequestList', 'IntentCancelInput', 'PublicationQuery', 'PublicationReadQuery'].map(name => schema + name)
    .concat(['IntentInput', 'IntentView', 'RequestView', 'WriteReceipt', ...new Set(Object.values(publicationOperations).map(item => item[1]))].map(name => protocol + name)));
}
export async function readPublicationDirectory(collection: PublicationCollection, action: PublicationAction, cursor?: string, signal?: AbortSignal) {
  validateApiValue(schema + 'Action', action); validateApiValue(schema + 'Collection', collection);
  const query: Record<string, string> = { collection, action }; if (cursor) query.cursor = cursor;
  validateApiValue(schema + 'PublicationQuery', query);
  const value = validateApiValue<PublicationDirectory>(schema + 'Directory', await fetchJson<unknown>(
    `/api/v2/support/publication-directory?${new URLSearchParams(query)}`, { signal }));
  if (value.collection !== collection || new Set(value.items.map(item => item.id)).size !== value.items.length)
    throw new Error('发布目录与当前查询范围不一致。');
  return value;
}
export async function readPublicationConfig(id: string, action: PublicationAction, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', id); validateApiValue(schema + 'Action', action);
  validateApiValue(schema + 'PublicationReadQuery', { action });
  const value = validateApiValue<ConfigDetail>(schema + 'ConfigDetail', await fetchJson<unknown>(
    `/api/v2/support/publication-configs/${encodeURIComponent(id)}?${new URLSearchParams({ action })}`, { signal }));
  if (value.resource.id !== id || new Set(value.approvals.map(item => item.id)).size !== value.approvals.length)
    throw new Error('配置读取与指定版本不一致。');
  return value;
}
export async function readPublicationApprovalConfig(id: string, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', id);
  const value = validateApiValue<ConfigDetail>(schema + 'ConfigDetail', await fetchJson<unknown>(
    `/api/v2/support/publication-approvals/${encodeURIComponent(id)}`, { signal }));
  if (!value.approvals.some(row => row.id === id)) throw new Error('批准读取与准确配置不一致。');
  return value;
}
export async function readPublicationContact(id: string, action: PublicationAction, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', id); validateApiValue(schema + 'Action', action);
  validateApiValue(schema + 'PublicationReadQuery', { action });
  const value = validateApiValue<ContactDetail>(schema + 'ContactDetail', await fetchJson<unknown>(
    `/api/v2/support/publication-contacts/${encodeURIComponent(id)}?${new URLSearchParams({ action })}`, { signal }));
  if (value.id !== id) throw new Error('联系读取与指定版本不一致。');
  return value;
}
export async function readPublicationRelease(id: string, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', id);
  const value = validateApiValue<ReleaseDetail>(schema + 'ReleaseDetail', await fetchJson<unknown>(
    `/api/v2/support/publication-releases/${encodeURIComponent(id)}`, { signal }));
  if (value.resource.release_id !== id || new Set(value.readiness.map(item => item.key)).size !== 6
      || value.may_enable && (value.resource.status !== 'approved' || value.qualification_reason !== null || value.active_release_id !== null))
    throw new Error('发布读取与指定版本或启用资格不一致。');
  return value;
}
export function publicationTarget(operation: PublicationOperation, id?: string) {
  if (!(operation in publicationOperations)) throw new Error('当前发布操作没有登记。');
  if (operation === 'source_register') return '/api/support/sources';
  if (operation === 'retention_policy_create') return '/api/support/retention-policies';
  if (operation.startsWith('source_revision_') || operation === 'retention_policy_activate') {
    validateApiValue(protocol + 'Uuid', id);
    if (operation === 'source_revision_register') return `/api/support/sources/${id}/revisions`;
    if (operation === 'retention_policy_activate') return `/api/support/retention-policies/${id}/activate`;
    return `/api/support/source-revisions/${id}/${operation === 'source_revision_approve' ? 'approve' : 'revoke'}`;
  }
  if (operation.endsWith('_create')) return `/api/support/${operation === 'config_create' ? 'configs' : operation === 'release_create' ? 'releases' : 'contacts'}`;
  validateApiValue(protocol + 'Uuid', id);
  if (operation === 'config_approval_revoke') return `/api/support/config-approvals/${id}/revoke`;
  const [kind, verb] = operation.split('_');
  const action = operation === 'config_approve' ? 'approvals' : operation === 'config_validate' ? 'validations' : verb;
  return `/api/support/${kind === 'config' ? 'configs' : kind === 'release' ? 'releases' : 'contacts'}/${id}/${action}`;
}
function checkIdentity(identity: PublicationIdentity) {
  validateApiValue(protocol + 'IntentInput', identity);
  const operation = publicationOperations[identity.operation_id];
  if (!operation) throw new Error('当前发布操作没有登记。');
  validateApiValue(protocol + operation[1], identity.payload);
  const id = identity.operation_id.endsWith('_create') ? undefined : identity.target_key.split('/')[4];
  if (publicationTarget(identity.operation_id, id) !== identity.target_key) throw new Error('提交目标与原操作不一致。');
}
function checkedReceipt(identity: PublicationIdentity, raw: unknown) {
  const value = validateApiValue<WriteReceipt>(protocol + 'WriteReceipt', raw);
  if (value.request_id !== identity.request_id || value.resource_type !== publicationOperations[identity.operation_id][2])
    throw new Error('发布保存凭据与原请求不一致。');
  if (!identity.operation_id.endsWith('_create') && !['config_approve', 'config_validate', 'source_register', 'source_revision_register'].includes(identity.operation_id)
      && identity.target_key.split('/')[4] !== value.resource_id) throw new Error('发布保存凭据与指定资源不一致。');
  return value;
}
export async function preparePublication(identity: PublicationIdentity, signal?: AbortSignal) {
  checkIdentity(identity);
  const intent = validateApiValue<IntentView>(protocol + 'IntentView', await fetchJson<unknown>('/api/write-intents', { method: 'POST', body: identity, signal }));
  if (intent.request_id !== identity.request_id) throw new Error('发布意图与原请求不一致。');
  return intent;
}
export async function submitPublication(identity: PublicationIdentity, intent: IntentView, signal?: AbortSignal) {
  checkIdentity(identity); validateApiValue(protocol + 'IntentView', intent);
  if (intent.request_id !== identity.request_id) throw new Error('发布提交与原意图不一致。');
  return checkedReceipt(identity, await fetchJson<unknown>(identity.target_key, { method: 'POST', body: identity.payload,
    signal, idempotencyKey: identity.request_id, writeIntent: intent.intent_id, intentGeneration: intent.generation }));
}
export function publicationRequestIdentity(request: RequestView): PublicationIdentity {
  validateApiValue(protocol + 'RequestView', request);
  if (!(request.operation_id in publicationOperations) || !request.controlled_input || !request.generation || !request.expires_at || !request.payload_sha256)
    throw new Error('原发布请求正文或身份不可读取。');
  const identity: PublicationIdentity = { operation_id: request.operation_id as PublicationOperation, request_id: request.request_id,
    target_key: request.target_key, schema_version: '1.0.0', payload: request.controlled_input };
  checkIdentity(identity);
  if ((request.state === 'committed') !== (request.receipt !== null)) throw new Error('发布请求状态与保存凭据不一致。');
  if (request.receipt) checkedReceipt(identity, request.receipt);
  return identity;
}
export async function readPublicationRequest(id: string, lookup?: Pick<PublicationIdentity, 'operation_id' | 'target_key'>, signal?: AbortSignal) {
  validateApiValue(protocol + 'Uuid', id);
  const query = lookup ? `?${new URLSearchParams({ lookup: 'request_id', ...lookup })}` : '';
  const value = await fetchJson<RequestView>(`/api/requests/${encodeURIComponent(id)}${query}`, { signal });
  const identity = publicationRequestIdentity(value);
  if (lookup ? identity.request_id !== id || identity.operation_id !== lookup.operation_id || identity.target_key !== lookup.target_key : value.intent_id !== id)
    throw new Error('发布恢复与原请求范围不一致。');
  return value;
}
export async function readPublicationRequests(cursor?: string, signal?: AbortSignal) {
  const value = validateApiValue<{ items: RequestView[]; next_cursor: string | null; snapshot_version: string }>(schema + 'RequestList',
    await fetchJson<unknown>(`/api/requests${cursor ? `?${new URLSearchParams({ cursor })}` : ''}`, { signal }));
  value.items.forEach(publicationRequestIdentity);
  return value;
}
export async function retryPublication(request: RequestView, signal?: AbortSignal) {
  publicationRequestIdentity(request);
  if (!request.may_retry || !request.generation) throw new Error('当前发布请求不具备重试条件。');
  const value = validateApiValue<IntentView>(protocol + 'IntentView', await fetchJson<unknown>(
    `/api/write-intents/${request.intent_id}/retry`, { method: 'POST', body: { expected_generation: request.generation }, signal }));
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || BigInt(value.generation) !== BigInt(request.generation) + 1n)
    throw new Error('发布重试与原意图不一致。');
  return value;
}
export async function cancelPublication(request: RequestView, reason: string, signal?: AbortSignal) {
  publicationRequestIdentity(request);
  if (request.state !== 'pending' || !request.generation || !reason.trim()) throw new Error('原提交意图尚未满足更正条件。');
  const body = { expected_generation: request.generation, reason };
  validateApiValue(schema + 'IntentCancelInput', body);
  const value = await fetchJson<RequestView>(`/api/write-intents/${encodeURIComponent(request.intent_id)}/cancel`, {
    method: 'POST', body, signal });
  publicationRequestIdentity(value);
  if (value.intent_id !== request.intent_id || value.request_id !== request.request_id || value.generation !== request.generation
      || value.state !== 'failed' || value.error_code !== 'CONTROLLED_INTENT_CANCELLED' || value.may_retry || value.receipt)
    throw new Error('提交意图更正状态与原请求不一致。');
  return value;
}
