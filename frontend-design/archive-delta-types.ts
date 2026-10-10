import type { MedicalTime, IntentView } from './types';
import type { WriteReceipt } from './v2-types';

export interface DeltaInput { baseline_batch_id: string | null; baseline_document_version_ids: string[] }
export interface DeltaSource {
  document_id: string; document_version_id: string; page_id: string; file_page_id: string; file_id: string;
  object_version: string; checksum: string; record_version_id: string | null; field_id: string | null; fact_version_id: string | null; anchor_id: string | null;
}
export interface DeltaFact {
  fact_version_id: string; record_version_id: string; field_id: string; field_code: string; concept_code: string | null; original_name: string | null;
  value_type: 'decimal' | 'text' | 'time' | 'categorical'; value_state: 'known' | 'unknown' | 'invalid';
  raw_text: string | null; raw_numeric: string | null; raw_unit: string | null; qualifier: string | null; method: string | null; categorical_value: string | null;
  medical_time: MedicalTime | null; context_type: string; assertion_state: string; verification_status: 'unverified' | 'pending' | 'verified' | 'needs_correction' | 'revoked';
}
export interface DeltaItem {
  item_id: string; kind: 'document' | 'fact' | 'duplicate' | 'conflict' | 'unknown_time' | 'unverified' | 'change'; title: string;
  before: DeltaFact | null; after: DeltaFact | null; source_refs: DeltaSource[]; relation_version_ids: string[]; reason_codes: string[];
}
export interface DeltaView extends DeltaInput {
  delta_id: string; patient_id: string; batch_id: string; current_document_version_ids: string[]; task_id: string;
  status: 'accepted' | 'running' | 'completed' | 'failed' | 'superseded'; validity: 'valid' | 'invalid'; reason_codes: string[]; items: DeltaItem[]; created_at: string;
}
export interface DeltaIdentity { operation_id: 'archive_delta_create'; target_key: string; request_id: string; schema_version: '2.0.0'; payload: DeltaInput }
export interface DeltaRequest extends Omit<IntentView, 'status'> {
  operation_id: 'archive_delta_create'; target_key: string; state: 'pending' | 'committed' | 'failed' | 'unknown';
  may_retry: boolean; receipt: WriteReceipt | null; error_code: string | null; delta_input: DeltaInput;
}
export interface DeltaTask {
  task_id: string; patient_id: string; status: 'completed' | 'superseded'; status_version: string;
  result_type: 'archive_delta'; result_ref: string; error_code: string | null;
}
