import Ajv2020 from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import schema from '../../../backend-design/content-disposition-plan-view.schema.json';
import { fetchJson } from './client';
export interface ContentTarget { kind: 'database' | 'local_object' | 's3_object'; adapter?: string; object_id?: string; version?: string; file_id?: string; object_version?: string }
export interface ContentStorageReference {
  schema_version?: 's3-object-reference-1'; binding_sha256: string; object_key: string;
  version_id: string; etag: string; checksum: string; size_bytes: number; encryption?: 'AES256';
}
export interface ContentObjectProof extends ContentTarget {
  object_key?: string; checksum?: string; size_bytes?: number; operation_id?: string;
  storage_reference?: ContentStorageReference;
}
export interface ContentPlan {
  schema_version: 'content-disposition-plan-view-1'; plan_id: string; patient_id: string;
  status: 'preview' | 'approved' | 'retained' | 'access_revoked' | 'erasing' | 'logical_erasure_complete';
  manifest_sha256: string; runtime_execution_enabled: boolean;
  manifest: { schema_version: 'content-disposition-manifest-1' | 'content-disposition-manifest-2';
    storage_binding_sha256?: string; mode: 'retain' | 'revoke_access' | 'erase_selected_content'; patient_id: string;
    family_request_id?: string | null; policy_id: string; policy_version: number; policy_sha256: string; preservation_sha256: string;
    roots: ContentTarget[]; items: ContentObjectProof[]; obligations: string[] };
  approved_at: string | null; completed_at: string | null;
  receipt: { logical_payload_erased: boolean; all_copies_erased: false; retained_until?: string;
    recheck_at?: string; access_revoked?: boolean; unverified_copy_obligations?: string[];
    storage_binding_sha256?: string; proof_scope?: string } | null;
}
const ajv = new Ajv2020({ strict: false, allowUnionTypes: true }); addFormats(ajv);
const check = ajv.compile<ContentPlan>(schema);
export function validateContentPlan(value: unknown): ContentPlan {
  if (!check(value)) throw new Error('内容处置响应未通过版本化契约校验。');
  if (value.patient_id !== value.manifest.patient_id) throw new Error('处置清单的患儿范围不一致。');
  if (value.manifest.schema_version === 'content-disposition-manifest-2' && value.manifest.items.some(item =>
    item.kind === 's3_object' && (!item.storage_reference || item.storage_reference.binding_sha256 !== value.manifest.storage_binding_sha256 ||
      item.storage_reference.object_key !== item.object_key || item.storage_reference.checksum !== item.checksum ||
      item.storage_reference.size_bytes !== item.size_bytes))) throw new Error('处置清单的对象版本引用不一致。');
  if (value.receipt?.logical_payload_erased && value.manifest.schema_version === 'content-disposition-manifest-2' &&
    value.receipt.storage_binding_sha256 !== value.manifest.storage_binding_sha256) throw new Error('处置凭证的存储范围不一致。');
  return value;
}
const base = '/api/v2/support/content-disposition/plans';
export async function previewContentPlan(patientId: string, policyId: string, request: unknown, signal: AbortSignal, familyRequestId: string | null = null) {
  const value = validateContentPlan(await fetchJson(`/api/v2/support/patients/${patientId}/content-disposition/plans`,
    { method: 'POST', body: { policy_id: policyId, request, family_request_id: familyRequestId }, signal }));
  if (value.patient_id !== patientId || value.manifest.policy_id !== policyId || (value.manifest.family_request_id ?? null) !== familyRequestId) throw new Error('处置清单与本次申请不一致。'); return value;
}
export async function readContentPlan(id: string, signal: AbortSignal) {
  const value = validateContentPlan(await fetchJson(`${base}/${id}`, { signal }));
  if (value.plan_id !== id) throw new Error('处置清单标识不一致。'); return value;
}
export async function approveContentPlan(plan: ContentPlan, sourceRevisionId: string, signal: AbortSignal) {
  const value = validateContentPlan(await fetchJson(`${base}/${plan.plan_id}/approve`, { method: 'POST',
    body: { expected_manifest_sha256: plan.manifest_sha256, source_revision_id: sourceRevisionId }, signal }));
  if (value.plan_id !== plan.plan_id || value.manifest_sha256 !== plan.manifest_sha256) throw new Error('批准范围发生变化。'); return value;
}
export async function executeContentPlan(plan: ContentPlan, signal: AbortSignal) {
  const value = validateContentPlan(await fetchJson(`${base}/${plan.plan_id}/execute`, { method: 'POST',
    body: { expected_manifest_sha256: plan.manifest_sha256 }, signal }));
  if (value.plan_id !== plan.plan_id || value.manifest_sha256 !== plan.manifest_sha256) throw new Error('执行范围发生变化。'); return value;
}

export interface ContentLifecycle {
 schema_version: 'content-disposition-lifecycle-1'; plan_id: string; retention_basis_source_revision_id: string;
 next_recheck_at: string; review_due: boolean; review_evidence_status: 'not_reviewed' | 'valid' | 'invalid_or_revoked'; review_version: number; receipt_keep_until: string; metadata_keep_until: string;
 receipt_expiry_review_due: boolean; metadata_expiry_review_due: boolean;
 expiry_disposal_status: 'blocked_pending_backup_and_retry_window_proof';
 unresolved_copy_obligations: { kind: string; reason_code: string; review_due_at: string; all_copies_erased: false }[];
 last_review: ContentReview | null;
}
export interface ContentReview {
 schema_version: 'content-disposition-review-1'; plan_id: string; manifest_sha256: string; policy_sha256: string;
 review_version: number; reviewed_by: string; reviewed_at: string; next_recheck_at: string;
 reason_code: 'approved_retention_continues' | 'copy_obligations_unresolved' | 'hold_requires_review' | 'metadata_retry_window_unproven';
}
export interface ContentPatients { schema_version: 'content-disposition-patients-1'; patients: { patient_id: string; display_id: string }[] }
export interface ContentOptions {
 schema_version: 'content-disposition-options-1'; patient_id: string; display_id: string; default_policy: null;
 policies: { policy_id: string; version: number; mode: ContentPlan['manifest']['mode']; execution_enabled: boolean;
 retention_seconds: number; clock: string; access_purpose: string; recheck_at: string }[];
 hold: { hold_id: string; version: number; state: 'clear' | 'held' | 'unresolved'; valid_until: string; eligible: boolean; reason_code: string | null } | null;
 targets: { target: ContentTarget; label: string; record_id: string | null }[];
}
export interface ContentHistory {
 schema_version: 'content-disposition-history-1'; patient_id: string;
 plans: { plan_id: string; status: ContentPlan['status']; created_at: string; mode: ContentPlan['manifest']['mode']; item_count: number; lifecycle: ContentLifecycle }[];
}
import lifecycleSchema from '../../../backend-design/content-disposition-lifecycle.schema.json';
import patientsSchema from '../../../backend-design/content-disposition-patients.schema.json';
import optionsSchema from '../../../backend-design/content-disposition-options.schema.json';
import historySchema from '../../../backend-design/content-disposition-history.schema.json';
import reviewSchema from '../../../backend-design/content-disposition-review.schema.json';
const checkLifecycle=ajv.compile<ContentLifecycle>(lifecycleSchema), checkPatients=ajv.compile<ContentPatients>(patientsSchema);
const checkOptions=ajv.compile<ContentOptions>(optionsSchema), checkHistory=ajv.compile<ContentHistory>(historySchema);
const checkReview=ajv.compile<ContentReview>(reviewSchema);
export async function listContentPatients(signal: AbortSignal) {
 const value=await fetchJson('/api/v2/support/content-disposition/patients',{signal});
 if (!checkPatients(value)) throw new Error('可处置对象身份列表契约无效。'); return value;
}
export async function readContentOptions(patientId: string,signal: AbortSignal) {
 const value=await fetchJson(`/api/v2/support/patients/${patientId}/content-disposition/options`,{signal});
 if (!checkOptions(value) || value.patient_id!==patientId) throw new Error('处置选择范围不一致。'); return value;
}
export async function listContentHistory(patientId: string,signal: AbortSignal) {
 const value=await fetchJson(`/api/v2/support/patients/${patientId}/content-disposition/history`,{signal});
 if (!checkHistory(value) || value.patient_id!==patientId) throw new Error('处置历史范围不一致。'); return value;
}
export async function readContentLifecycle(planId: string,signal: AbortSignal) {
 const value=await fetchJson(`${base}/${planId}/review`,{signal});
 if (!checkLifecycle(value) || value.plan_id!==planId) throw new Error('处置复查范围不一致。'); return value;
}
export async function reviewContentPlan(plan: ContentPlan,attestation: unknown,sourceRevisionId: string,signal: AbortSignal) {
 if (!checkReview(attestation) || attestation.plan_id!==plan.plan_id || attestation.manifest_sha256!==plan.manifest_sha256 || attestation.policy_sha256!==plan.manifest.policy_sha256) throw new Error('复查证明与准确申请不一致。');
 const value=await fetchJson(`${base}/${plan.plan_id}/review`,{method:'POST',body:{attestation,source_revision_id:sourceRevisionId},signal});
 if (!checkLifecycle(value) || value.plan_id!==plan.plan_id) throw new Error('处置复查回执范围不一致。'); return value;
}


import familyRequestsSchema from '../../../backend-design/content-disposition-family-requests.schema.json';
export interface ContentFamilyApplication {
 schema_version: 'content-disposition-family-view-1'; request_id: string; client_request_id: string; patient_id: string;
 policy_id: string; created_at: string; targets: { record_version_id: string; version: string }[];
 status: ContentPlan['status'] | 'requested'; linked_plan_id: string | null; logical_payload_erased: boolean; all_copies_erased: false;
}
interface ContentFamilyApplications { schema_version: 'content-disposition-family-requests-1';patient_id: string;requests: ContentFamilyApplication[] }
const checkFamilyRequests=ajv.compile<ContentFamilyApplications>(familyRequestsSchema);
export async function listContentFamilyRequests(patientId: string, signal: AbortSignal) {
 const value=await fetchJson(`/api/v2/support/patients/${patientId}/content-disposition/requests`,{signal});
 if (!checkFamilyRequests(value) || value.patient_id!==patientId || value.requests.some(r=>r.patient_id!==patientId)) throw new Error('本人申请列表范围不一致。'); return value;
}
