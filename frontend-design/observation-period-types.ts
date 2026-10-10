import type { Uuid, Version, IntentView, MedicalTime, MedicalTimeInput, SourceReference } from './types';
import type { DeltaFact } from './archive-delta-types';
import type { WriteReceipt } from './v2-types';

export interface ObservationPeriodInput {
  expected_version: Version | null; start: MedicalTimeInput; end: MedicalTimeInput;
  label: string | null; episode_id: Uuid | null; reason: string;
}
export interface PeriodSampling {
  fact: DeltaFact; source: SourceReference; document_id: Uuid | null; document_version_id: Uuid | null;
  page_id: Uuid | null; available_at: string | null;
  sampling_group_id: Uuid; relation_version_ids: Uuid[];
  range_state: 'inside' | 'outside' | 'overlap' | 'unknown' | 'not_selected'; reason_codes: string[];
}
export interface SamplingInterval {
  before_fact_version_id: Uuid; after_fact_version_id: Uuid; actual_hours: string | null;
  minimum_hours: string | null; maximum_hours: string | null;
  comparability: 'comparable' | 'incomparable' | 'unknown'; reason_codes: string[];
  relation_version_ids: Uuid[];
}
export interface ObservationPeriodView {
  patient_id: Uuid; period_id: Uuid | null; version_id: Uuid | null; version: Version | null;
  owner_id: Uuid | null; label: string | null; label_source: 'self_report' | 'authorized_clinical' | null;
  episode_id: Uuid | null; episode_label: string | null; created_at: string | null;
  is_current: boolean; start: MedicalTime | null; end: MedicalTime | null; timezone: string | null;
  target_interval_hours: string | null; interval_basis_ref: string | null;
  interval_policy_id: Uuid | null; samplings: PeriodSampling[]; intervals: SamplingInterval[];
  unknown_time_fact_ids: Uuid[]; reason_codes: string[]; snapshot_version: string;
  episode_options: { episode_id: Uuid; label: string; organization: string | null }[];
  allowed_actions: string[];
}
export interface PeriodIdentity {
  operation_id: 'observation_period_change'; target_key: string; request_id: Uuid;
  schema_version: '2.0.0'; payload: ObservationPeriodInput;
}
export interface PeriodRequest {
  intent_id: Uuid; request_id: Uuid; operation_id: 'observation_period_change'; target_key: string;
  state: 'pending' | 'committed' | 'failed' | 'unknown'; may_retry: boolean;
  generation: Version; expires_at: string; payload_sha256: string;
  receipt: WriteReceipt | null; error_code: string | null; period_input: ObservationPeriodInput;
}
export type PeriodIntent = IntentView;
