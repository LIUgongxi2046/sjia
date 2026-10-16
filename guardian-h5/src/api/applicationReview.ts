import { fetchJson } from './client';
import { validateApiValue } from './schema';
import type { ApplicationDeclarationRead } from '../../../frontend-design/types';
import { sameApplicationDeclaration } from './application';

export interface SupportApplicationReview extends ApplicationDeclarationRead {
  application_id: string; applicant_id: string; patient_id: string | null;
  status: 'pending' | 'approved' | 'rejected' | 'withdrawn'; version: string;
  evidence_ref: string; note: string | null; requested_access: 'read' | 'read_write' | 'review_decides' | null;
  created_at: string; checked_at: string | null; reason: string | null;
}
export interface SupportApplicationDecisionInput { expected_version: string; decision: 'approved' | 'rejected'; reason: string; patient_id?: string }
export interface SupportApplicationDecisionReceipt { relation_id: string | null; status: 'approved' | 'rejected'; version: string }
const schema = 'protocol.schema.json#/$defs/';
export function checkApplicationId(value: unknown): string { return validateApiValue<string>(schema + 'Uuid', value); }
export async function readApplicationReview(id: string, signal: AbortSignal) {
  checkApplicationId(id);
  const value = validateApiValue<SupportApplicationReview>(schema + 'SupportApplicationReview', await fetchJson(`/api/support/relation-applications/${id}`, { signal }));
  if (value.application_id !== id) throw new Error('申请资料与准确申请标识不一致。');
  return value;
}
export function checkApplicationDecision(payload: SupportApplicationDecisionInput) {
  validateApiValue(schema + 'SupportApplicationDecisionInput', payload);
  if (BigInt(payload.expected_version) >= 9223372036854775807n) throw new Error('申请版本超出范围。');
}
export async function decideApplicationReview(id: string, payload: SupportApplicationDecisionInput, signal: AbortSignal) {
  checkApplicationId(id);
  checkApplicationDecision(payload);
  const value = validateApiValue<SupportApplicationDecisionReceipt>(schema + 'SupportApplicationDecisionReceipt', await fetchJson(`/api/support/relation-applications/${id}/decide`, { method: 'POST', body: payload, signal }));
  if (value.status !== payload.decision || BigInt(value.version) !== BigInt(payload.expected_version) + 1n
    || (value.status === 'approved') !== (value.relation_id !== null)) throw new Error('处理回执与当前申请结论或版本不一致。');
  return value;
}
export function applicationDecisionMatches(original: SupportApplicationReview, payload: SupportApplicationDecisionInput, value: SupportApplicationReview) {
  return value.application_id === original.application_id && value.applicant_id === original.applicant_id
    && sameApplicationDeclaration(original.applicant_declaration, value.applicant_declaration)
    && value.status === payload.decision && BigInt(value.version) === BigInt(payload.expected_version) + 1n
    && value.reason === payload.reason && value.patient_id === (payload.decision === 'approved' ? payload.patient_id : original.patient_id);
}
