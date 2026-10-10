import type { IntentView, MedicalTime, SourceReference } from './types';
import type { RelationInput, RelationChangeInput, RelationType, RelationView, WriteReceipt } from './v2-types';

export type RelationOperation = 'archive_relation_create' | 'archive_relation_update' | 'archive_relation_verify';
export interface RelationVerificationInput { expected_version: string; verification_id: string; evidence_ref: string; reason: string }
export interface RelationIdentity {
  operation_id: RelationOperation; target_key: string; request_id: string; schema_version: '2.0.0';
  payload: RelationInput | RelationChangeInput | RelationVerificationInput;
}
export interface RelationSource {
  kind: 'file' | 'fact'; id: string; label: string; record_id: string | null; record_version_id: string | null;
  field_code: string | null; report_identifier: string | null; medical_time: MedicalTime | null;
  source: SourceReference | null; object_version: string | null; checksum: string | null;
}
export interface RelationCandidate {
  source_kind: 'file' | 'fact'; left_id: string; right_id: string; suggested_types: RelationType[]; reason_codes: string[]; status: 'proposed';
}
export interface RelationDirectory { patient_id: string; sources: RelationSource[]; candidates: RelationCandidate[];
  relations: RelationView[]; snapshot_version: string; next_cursor: string | null }
export interface RelationRequestView extends Omit<IntentView, 'status'> {
  operation_id: RelationOperation; target_key: string; prepared_at: string; state: 'pending' | 'committed' | 'failed' | 'unknown';
  may_retry: boolean; receipt: WriteReceipt | null; error_code: string | null; relation_input: RelationIdentity['payload'];
}
