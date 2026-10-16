import type { Uuid, Version, Timestamp, MedicalTimeInput, MedicalTime, FieldInput, FamilyField, SourceReference, TaskView as LegacyTaskView, Page, WriteReceipt as LegacyWriteReceipt, Capability } from './types';

export type Context = 'history' | 'current' | 'discharge' | 'planned' | 'actual' | 'unknown';
export type Assertion = 'present' | 'absent' | 'unknown';
export type TimeOrigin = 'item' | 'explicit_common' | 'observation' | 'therapy' | 'unknown' | 'legacy';
export type DocumentType = 'laboratory' | 'inpatient' | 'discharge' | 'outpatient' | 'therapy' | 'imaging_report' | 'imaging_asset' | 'unknown';
export type RelationType = 'same_bytes' | 'document_copy' | 'same_sampling' | 'independent_sampling' | 'formal_correction';
export type RelationStatus = 'proposed' | 'confirmed' | 'disputed' | 'released';
export type Purpose = 'external_recognition' | 'format_evaluation' | 'workflow_evaluation' | 'clinical_evaluation' | 'competition_display' | 'export';
export type ReminderTarget = 'draft' | 'batch' | 'record_task';
export type ReminderStatus = 'disabled' | 'enabled' | 'paused' | 'completed' | 'cancelled';
export type DeliveryStatus = 'authorized' | 'started' | 'accepted' | 'delivered' | 'failed' | 'uncertain' | 'cancelled';
export type PageState = 'default' | 'loading' | 'empty' | 'error' | 'offline' | 'permission' | 'partial' | 'stale' | 'pending' | 'success' | 'attention' | 'warning' | 'urgent';
export type WriteReceipt = LegacyWriteReceipt & { schema_version: '2.0.0'; task_id: Uuid | null };
export interface SupportLoginInput { username: string; password: string }
export interface SupportSessionView {
  principal_id: Uuid; account_status: 'active'; expires_at: Timestamp; audience: 'support';
  capabilities: Capability[]; csrf_token: string;
}
export type TaskView = Omit<LegacyTaskView, 'task_type' | 'result_type'> & {
  task_type: LegacyTaskView['task_type'] | 'purpose_copy';
  result_type: LegacyTaskView['result_type'] | 'purpose_copy' | 'evaluation_run';
};
export interface DispositionApprovalInput { policy_version: Version; expected_scope_sha256: string; evidence_ref: string }
export interface DispositionInput { targets: DispositionTarget[]; policy_id: Uuid; evidence_ref: string }
export interface DispositionOptions { patient_id: Uuid; policies: Array<{ policy_id: Uuid; policy_key: string; version: Version; evidence_ref: string }>;
  targets: Array<DispositionTarget & { policy_id: Uuid; label: string }> }
export interface DispositionExecuteInput { policy_version: Version; expected_scope_sha256: string; expected_target_version: string }
export interface DispositionRetainInput extends DispositionExecuteInput { retention_evidence_ref: string; recheck_at: Timestamp }
export interface DispositionRetryInput { expected_status_version: Version }
export interface DispositionTarget { target_type: string; target_id: string; target_version: string }
export interface DispositionItemView extends DispositionTarget {
  item_id: Uuid; status: 'pending' | 'blocked' | 'retained' | 'deleted' | 'failed' | 'awaiting_provider';
  reason: string | null; receipt_sha256: string | null; executed_at: Timestamp | null; recheck_at: Timestamp | null;
}
export interface DispositionView {
  disposition_id: Uuid; patient_id: Uuid; status: 'pending' | 'approved' | 'running' | 'blocked' | 'completed' | 'rejected';
  policy_id: Uuid; policy_version: Version; scope_sha256: string; targets: DispositionTarget[];
  approved_by: Uuid | null; approved_at: Timestamp | null; completed_at: Timestamp | null;
  task_ids: Uuid[]; items: DispositionItemView[];
}
export interface RequestView {
  intent_id: Uuid; request_id: Uuid; operation_id: string; target_key: string; prepared_at: Timestamp;
  state: 'pending' | 'processing' | 'committed' | 'failed' | 'unknown' | 'not_found';
  may_retry: boolean; receipt: WriteReceipt | null; error_code: string | null;
  generation?: Version; expires_at?: Timestamp; payload_sha256?: string;
  document_input?: DocumentInput | GroupingInput;
  reminder_input?: ReminderInput | ReminderChangeInput;
  batch_input?: BatchInput;
  disposition_input?: DispositionInput;
  batch_action_input?: BatchProcessInput | PageActionInput;
  fact_input?: FactConfirmationInput | FactCorrectionInput;
  dataset_input?: DatasetInput | PartitionInput | LabelInput | DatasetFreezeInput;
  evaluation_run_input?: EvaluationRunInput;
  assessment_input?: AssessmentInput;
  dynamic_input?: DynamicInput;
  verification_input?: VerificationRequestInput | VerificationInput | VerificationRevokeInput;
}
export interface EvaluationRunIntentInput {
  operation_id: 'dataset_run_create'; target_key: string; request_id: Uuid; schema_version: '2.0.0'; payload: EvaluationRunInput;
}
export interface EvaluationRunRequestLookup {
  lookup: 'request_id'; operation_id: 'dataset_run_create'; target_key: string;
}
export interface EvaluationIntentInput {
  operation_id: 'dataset_create' | 'dataset_partition' | 'dataset_label' | 'dataset_freeze';
  target_key: string; request_id: Uuid; schema_version: '2.0.0';
  payload: DatasetInput | PartitionInput | LabelInput | DatasetFreezeInput;
}
export interface EvaluationRetryInput { expected_generation: Version }
export interface EvaluationRequestLookup {
  lookup: 'request_id'; operation_id: EvaluationIntentInput['operation_id']; target_key: string;
}
export interface BatchIntentInput {
  operation_id: 'archive_batch_create'; target_key: string;
  request_id: Uuid; schema_version: '2.0.0'; payload: BatchInput;
}
export interface BatchRetryInput { expected_generation: Version }
export interface BatchActionIntentInput {
  operation_id: 'archive_batch_process' | 'archive_page_retry' | 'archive_page_cancel'; target_key: string;
  request_id: Uuid; schema_version: '2.0.0'; payload: BatchProcessInput | PageActionInput;
}
export interface BatchInputsView {
  patient_id: Uuid;
  items: { file_group_id: Uuid; version: Version; page_count: number; file_ids: Uuid[]; created_at: Timestamp }[];
}
export interface BatchRequestLookup {
  lookup: 'request_id'; operation_id: 'archive_batch_create'; target_key: string;
}
export interface DocumentIntentInput {
  operation_id: 'archive_document_create' | 'archive_document_group'; target_key: string;
  request_id: Uuid; schema_version: '2.0.0'; payload: DocumentInput | GroupingInput;
}
export interface DocumentRequestLookup {
  lookup: 'request_id'; operation_id: DocumentIntentInput['operation_id']; target_key: string;
}
export interface ReminderIntentInput {
  operation_id: 'material_reminder_create' | 'material_reminder_change'; target_key: string;
  request_id: Uuid; schema_version: '2.0.0'; payload: ReminderInput | ReminderChangeInput;
}
export interface ReminderRetryInput { expected_generation: Version }
export interface ReminderRequestLookup {
  lookup: 'request_id'; operation_id: ReminderIntentInput['operation_id']; target_key: string;
}
export interface PageQuery { cursor?: string; limit?: number }
export interface RangeQuery { range?: string }
export interface ReceiptAck { received: true; provider_event_id: string }

export interface ArchiveQuery extends PageQuery {
  organization?: string; document_type?: DocumentType; episode_id?: Uuid;
  from_time?: MedicalTimeInput; to_time?: MedicalTimeInput;
  confirmed_text?: string; include_unknown_time?: boolean;
}
export interface DocumentFields {
  batch_id: Uuid; page_ids: Uuid[]; document_type: DocumentType;
  organization: string | null; documented_time: MedicalTimeInput | null; episode_ids: Uuid[];
  confirmed_text?: string | null; proposed_metadata?: ProposedDocumentMetadata | null;
}
export interface ProposedEpisode {
  kind: 'inpatient' | 'outpatient' | 'unknown'; start_time: MedicalTimeInput | null;
  end_time: MedicalTimeInput | null; description: string | null;
}
export interface ProposedDocumentMetadata { episodes: ProposedEpisode[]; grouping_note: string | null }
export type DocumentInput = DocumentFields;
export interface DocumentEpisodeOption {
  episode_id: Uuid; source_page_id: Uuid; source_field_id: Uuid; record_version_id: Uuid;
  episode_type: 'onset' | 'hospitalization' | 'icu' | 'consultation' | 'transfer' | 'relapse' | 'outpatient' | 'other';
  label: string; start_time: MedicalTime | null; end_time: MedicalTime | null;
}
export interface DocumentInputsView { patient_id: Uuid; batch_id: Uuid; episodes: DocumentEpisodeOption[] }
export interface GroupingInput extends DocumentFields { expected_version: Version; reason: string }
export interface DocumentView {
  document_id: Uuid; patient_id: Uuid; version_id: Uuid; version: Version; batch_id: Uuid;
  document_type: DocumentType; organization: string | null; documented_time: MedicalTime | null;
  page_ids: Uuid[]; episode_ids: Uuid[]; grouping_status: 'pending' | 'confirmed' | 'needs_correction';
  confirmed_text: string | null; proposed_metadata: ProposedDocumentMetadata | null;
  available_at: Timestamp; allowed_actions: string[];
}
export type DocumentList = Page<DocumentView>;
export type ArchiveList = DocumentList;
export interface BatchInput { file_group_ids: Uuid[]; intake_mode?: 'ocr' | 'manual' }
export interface BatchProcessInput { expected_version: Version; page_ids: Uuid[] }
export interface Counts {
  originals: number; pages: number; declared: number; receiving: number; uploaded: number; queued: number;
  processing: number; awaiting_confirmation: number; confirmed: number; archived: number; failed: number;
  uncertain: number; cancel_requested: number; cancelled: number; superseded: number; facts: number;
}
export interface BatchView {
  batch_id: Uuid; patient_id: Uuid; version: Version;
  intake_mode: 'ocr' | 'manual';
  status: 'receiving' | 'processing' | 'awaiting_confirmation' | 'partial' | 'archived' | 'cancelled' | 'failed';
  counts: Counts; received_at: Timestamp; budget_status: 'reserved' | 'available' | 'exhausted' | 'unknown' | 'released';
  pages: PageView[]; allowed_actions: string[];
}
export type BatchList = Page<BatchView>;
export interface PageView {
  page_id: Uuid; batch_id: Uuid; file_page_id: Uuid; file_id: Uuid; version: Version; status_version: Version;
  file_object_version: string; checksum: string; file_status: 'quarantined' | 'available' | 'failed' | 'disposed';
  status: 'declared' | 'receiving' | 'uploaded' | 'queued' | 'processing' | 'awaiting_confirmation' | 'confirmed' | 'archived' | 'failed' | 'uncertain' | 'cancel_requested' | 'cancelled' | 'superseded';
  task: TaskView | null; candidate_set_id: Uuid | null; error_code: string | null;
  attempts: PageAttemptView[]; allowed_actions: string[];
}
export interface PageAttemptView {
  attempt_id: Uuid; page_version: Version; task_id: Uuid; task_attempt_id: Uuid;
  request_id: Uuid | null; attempt_number: number; input_sha256: string; model_version: string;
  candidate_set_id: Uuid | null; provider_request_id: string | null;
  started_at: Timestamp; finished_at: Timestamp | null; duration_ms: number | null;
  cost: string | null; currency: string | null; usage_evidence_sha256: string | null;
  pricing_scope?: 'simulation' | 'unknown';
  status: 'running' | 'completed' | 'failed' | 'uncertain' | 'cancelled' | 'superseded'; error_code: string | null;
}
export interface PageActionInput { expected_status_version: Version; reason: string }
export interface FactContextInput {
  context_type: Context; assertion_state: Assertion; episode_id: Uuid | null;
  medical_time: MedicalTimeInput | null; raw_time_text: string | null;
  time_origin: TimeOrigin; documented_time: MedicalTimeInput | null;
}
export interface FactContextView extends Omit<FactContextInput, 'medical_time' | 'documented_time'> {
  medical_time: MedicalTime | null; documented_time: MedicalTime | null;
}
export interface FactCandidate {
  candidate_id: Uuid; field: FieldInput; context: FactContextInput; document_version_id: Uuid | null;
  page_id: Uuid; source_anchor_id: Uuid; recognition_status: 'recognized' | 'partial' | 'unknown' | 'unreadable';
  source_quote: string; confirmation: FactDecision | null;
}
export interface ArchiveExtraction {
  schema_version: 'archive-fact-extraction-2'; document_type: DocumentType; organization: string | null;
  documented_time_text: string | null; page_text: string;
  items: Array<{ original_name: string; value_type: string; value_state: string; raw_text: string | null;
    raw_numeric: string | null; qualifier: string | null; raw_unit: string | null; method: string | null;
    reference_interval: string | null; context_type: Context; assertion_state: Assertion; raw_time_text: string | null;
    time_origin: TimeOrigin; episode_text: string | null; source_quote: string;
    locator: { anchor_kind: 'page' | 'bbox' | 'table_cell'; bbox: number[] | null; table_row: number | null; table_column: number | null };
    recognition_status: 'recognized' | 'partial' | 'unknown' | 'unreadable' }>;
  unreadable_regions: Array<{ locator: ArchiveExtraction['items'][number]['locator']; reason: string }>;
}
export interface FactCandidateSet {
  candidate_set_id: Uuid; page_id: Uuid; page_version: Version; model_version: string;
  status: 'awaiting_confirmation' | 'confirmed' | 'superseded' | 'cancelled'; items: FactCandidate[];
  extraction: ArchiveExtraction | null;
}
export interface FactDecision {
  candidate_id: Uuid; decision: 'accepted' | 'modified' | 'unknown' | 'excluded';
  field: FieldInput | null; context: FactContextInput | null; reason: string | null;
}
export interface FactConfirmationInput { candidate_set_id: Uuid; expected_page_version: Version; items: FactDecision[] }
export interface FactIntentInput {
  operation_id: 'archive_candidates_confirm' | 'archive_fact_correct'; target_key: string; request_id: Uuid;
  schema_version: '2.0.0'; payload: FactConfirmationInput | FactCorrectionInput;
}
export interface PageOriginalView {
  patient_id: Uuid; page: PageView; ordinal: number; created_at: Timestamp;
  file: { file_id: Uuid; status: string; checksum: string; size_bytes: number; media_type: string; content_path: string | null };
  use_authorizations: Array<{ authorization_id: Uuid; purpose: string; environment: string; evidence_ref: string; valid_until: Timestamp | null }>;
  purpose_copies: Array<{ copy_id: Uuid; version: Version; purpose: string; status: PurposeCopyView['status']; review_id: Uuid | null;
    reviewed_at: Timestamp | null; revoked_at: Timestamp | null; identity_result: string | null; clinical_result: string | null }>;
}
export interface FactCorrectionInput { expected_version: Version; field: FieldInput; context: FactContextInput; reason: string }
export interface FactFieldSnapshot extends FamilyField {
  quality_reasons_snapshot_state: 'recorded' | 'not_recorded';
}
export interface FactView {
  fact_id: Uuid; patient_id: Uuid; version_id: Uuid; version: Version; record_version_id: Uuid;
  field: FactFieldSnapshot; context: FactContextView; document_version_id: Uuid | null; page_id: Uuid | null;
  source_anchor_id: Uuid | null; available_at: Timestamp | null; availability_status: 'known' | 'unknown';
  source: SourceReference; allowed_actions: string[];
}
export type FactList = Page<FactView>;
export interface RelationFields {
  relation_type: RelationType; left_fact_version_id: Uuid | null; right_fact_version_id: Uuid | null;
  left_file_id: Uuid | null; right_file_id: Uuid | null; status: RelationStatus; evidence_ref: string; reason: string;
}
export type RelationInput = RelationFields;
export interface RelationChangeInput extends RelationFields { expected_version: Version }
export interface RelationView extends RelationFields {
  relation_id: Uuid; patient_id: Uuid; version_id: Uuid; version: Version; verification_id: Uuid | null; allowed_actions: string[];
}
export type RelationList = Page<RelationView>;
export type { DeltaInput, DeltaItem, DeltaView } from './archive-delta-types';
export type { ObservationPeriodInput, SamplingInterval, ObservationPeriodView } from './observation-period-types';
export interface PurposeCopyInput { purpose: Purpose; target: string; authorization_id: Uuid; processing_config_id: Uuid }
export interface UploadGroupReceipt {
  group_id: Uuid; version: Version; status: 'initiated' | 'uploaded' | 'validating' | 'available' | 'failed' | 'superseded' | 'disposed';
}
export interface ArchiveUploadOptions {
  patient_id: Uuid; runtime_config_id: Uuid; runtime_checksum: string; max_upload_bytes: number; max_pixels: number;
  max_pages: number; media_types: string[]; parser_version: string;
  authorizations: { authorization_id: Uuid; source_revision_id: Uuid; source_label: string; evidence_ref: string; approved_at: Timestamp; valid_until: Timestamp | null }[];
}
export interface PurposeCopyOptionsView {
  patient_id: Uuid; original_file_id: Uuid; original_version: string; original_checksum: string;
  purpose: Purpose; environment: string;
  authorizations: { authorization_id: Uuid; evidence_ref: string; approved_at: Timestamp; valid_until: Timestamp | null }[];
  processing_configs: { processing_config_id: Uuid; version: Version; checksum: string; target: string;
    file_page_id: Uuid; source_page_number: number;
    date_strategy: 'unchanged' | 'consistent_shift'; approval_evidence_refs: string[]; validation_evidence_refs: string[] }[];
}
export interface PurposeReviewInput {
  expected_version: Version; checksum: string;
  scope_results: { scope: 'image_text' | 'confirmed_text' | 'model_input' | 'log' | 'presentation'; result: 'passed' | 'failed' | 'not_applicable' }[];
  clinical_integrity: 'passed' | 'failed' | 'unknown'; date_shift_consistency: 'passed' | 'failed' | 'not_applicable' | 'unknown'; evidence_ref: string;
}
export interface PurposeRevokeInput { expected_version: Version; reason: string }
export interface PurposeProviderCopyInput { expected_version: Version; review_id: Uuid }
export interface PurposeCopyView {
  copy_id: Uuid; patient_id: Uuid; original_file_id: Uuid; original_version: string; version: Version;
  copy_file_id: Uuid; checksum: string; purpose: Purpose; target: string;
  status: 'pending_review' | 'approved' | 'rejected' | 'revoked' | 'disposed'; review_id: Uuid | null; allowed_actions: string[];
}
export interface DatasetQuery extends PageQuery { protocol_ref?: string; status?: 'draft' | 'frozen' | 'revoked' }
export interface PurposeCopyEvidenceView {
  copy_id: Uuid; version: Version; environment: string; use_authorization_id: Uuid; processing_version: string;
  processing_evidence: Record<string, unknown>; date_strategy: 'unchanged' | 'consistent_shift';
  clinical_integrity_status: 'pending' | 'verified' | 'failed'; created_by: Uuid; restriction_reasons: string[];
  created_at: string; disposition_ref: Uuid | null; reviewer_id: Uuid | null; reviewed_at: string | null;
  review_evidence_ref: string | null; date_review: 'passed' | 'failed' | 'not_applicable' | 'unknown' | null;
  simulated_provider_copies: { provider_copy_id: Uuid; provider_id: string; object_id: Uuid; object_version: string;
    checksum: string; size_bytes: number; status: string; deletion_request_id: Uuid | null;
    receipt_ref: string | null; receipt_sha256: string | null }[];
}
export interface DatasetInput {
  dataset_id: Uuid | null; expected_version: Version | null;
  protocol_ref: string; source_manifest_ref: string; purpose_copy_ids: Uuid[]; label_version_ref: string | null;
}
export interface DatasetFreezeInput { expected_version: Version; checksum: string; evidence_ref: string }
export interface PartitionInput { expected_version: Version; patient_id: Uuid; partition: 'development' | 'validation'; authorization_id: Uuid; evidence_ref: string }
export interface LabelInput {
  expected_version: Version; patient_id: Uuid; partition_id: Uuid; observation_period_id: Uuid;
  label_state: 'positive' | 'negative' | 'unknown'; reference_answer_ref: string | null;
  reference_recognition_at: Timestamp | null; limitations: string[];
}
export interface DatasetCounts {
  files: number | null; pages: number | null; documents: number | null; patients: number | null;
  encounters: number | null; sampling_events: number | null; mas_events: number | null;
  observation_periods: number | null; observation_person_hours: string | null;
  confirmed_labels: number | null; unknown_labels: number | null;
}
export interface DatasetArtifactProof {
  reference: string; revision_id: Uuid; source_id: Uuid; sha256: string;
  captured_at: Timestamp; retention_policy_id: Uuid;
}
export interface DatasetSourceRef {
  source_id: string; file_id: Uuid; authorization_id: Uuid; evidence: DatasetArtifactProof;
}
export interface DatasetPartitionView {
  partition_id: Uuid; patient_id: Uuid; partition: 'development' | 'validation';
  authorization_id: Uuid; source_refs: DatasetSourceRef[];
}
export interface DatasetLabelView {
  label_id: Uuid; patient_id: Uuid; partition_id: Uuid; observation_period_id: Uuid;
  label_state: 'positive' | 'negative' | 'unknown'; reference_answer_ref: string | null;
  reference_recognition_at: Timestamp | null; confirmed_by: Uuid | null;
  confirmed_at: Timestamp | null; limitations: string[];
}
export type EvaluationUnit = 'files' | 'pages' | 'documents' | 'patients' | 'encounters'
  | 'sampling_events' | 'mas_events' | 'observation_periods' | 'labels';
export interface DatasetUnitCoverage {
  unit: EvaluationUnit; state: 'complete' | 'partial' | 'unrecorded';
  registered_items: number | null; evidence_refs: string[]; limitations: string[];
}
export interface DatasetCoverage {
  evaluation_kind: 'format' | 'workflow' | 'clinical';
  purpose: 'format_evaluation' | 'workflow_evaluation' | 'clinical_evaluation';
  available_before: Timestamp; required_units: EvaluationUnit[]; units: DatasetUnitCoverage[];
  unrecorded_units: (keyof DatasetCounts)[]; clinical_qualification: 'not_evaluated';
}
export interface DatasetView extends Omit<DatasetInput, 'dataset_id' | 'expected_version'> {
  dataset_id: Uuid; version_id: Uuid; version: Version; checksum: string; frozen_at: Timestamp | null;
  status: 'draft' | 'frozen' | 'revoked'; counts: DatasetCounts; partitions: DatasetPartitionView[];
  labels: DatasetLabelView[]; coverage: DatasetCoverage; allowed_actions: string[];
}
export type DatasetList = Page<DatasetView>;
export interface EvaluationRunInput {
  expected_version: Version; partition: 'development' | 'validation'; config_ids: Uuid[];
  code_version: string; label_version_ref: string; mode: 'replay'; clinical_capture_ref?: string;
}
export interface EvaluationRunView {
  run_id: Uuid; dataset_id: Uuid; dataset_version: Version; task: TaskView; mode: 'replay';
  status: 'accepted' | 'running' | 'completed' | 'failed' | 'insufficient' | 'superseded'; result_ref: string | null;
  denominator_ref: string | null; label_version_ref: string;
}
export interface EvaluationDenominatorData {
  counts: DatasetCounts; patient_ids: Uuid[]; unit_ids: Record<EvaluationUnit, Uuid[]>;
  eligible_label_ids: Uuid[]; excluded_unknown_label_ids: Uuid[]; clinical_qualification: 'not_evaluated';
}
export interface EvaluationLimitation { unit: keyof DatasetCounts; reason: 'unrecorded' | 'clinical_metrics_not_run' }
export interface EvaluationUnitResult {
  schema_version: 'evaluation-unit-result-1'; scope: 'inventory'; unit: EvaluationUnit;
  state: 'complete' | 'partial' | 'unrecorded'; registered_items: number | null;
  unit_ids: Uuid[]; evidence_refs: string[]; limitations: string[];
  dataset_version_id: Uuid; dataset_checksum: string; protocol_ref: string;
  source_manifest_ref: string; input_sha256: string; result_ref: string;
}
export interface EvaluationRuleProof {
  id: Uuid; config_key: string; kind: string; version: Version; schema_version: string; sha256: string;
  approval_id: Uuid; approved_at: Timestamp; validation_id: Uuid; validated_at: Timestamp;
}
export interface EvaluationDenominatorsView {
  schema_version: 'evaluation-denominators-1'; run_id: Uuid; denominators: EvaluationDenominatorData;
  limitations: EvaluationLimitation[]; input_sha256: string;
}
export interface ClinicalMetricSummary {
  sensitivity: number | null; ppv: number | null; event_unassessable_fraction: number | null;
  detected_events: number; assessable_positive_events: number;
  missed_event_ids: string[]; unassessable_positive_event_ids: string[]; unknown_event_ids: string[];
  positive_prompts: number; known_outcome_prompts: number; unknown_outcome_prompts: number;
  prompt_counts: { total: number; first: number; repeat: number };
  assessable_observation_person_hours: string; all_observation_person_hours: string;
  negative_observation_person_hours: string;
  alerts_per_observation_person_hour: number | null; alerts_per_negative_observation_person_hour: number | null;
  first_alert_difference_seconds_by_event: Record<string, number>;
  confidence_interval: null; confidence_interval_status: 'NOT_MEASURED_REQUIRES_APPROVED_PATIENT_CORRELATED_METHOD';
}
export interface ClinicalMetricsReportV2 {
  schema_version: 'clinical-metrics-report-2'; data_mode: 'synthetic' | 'clinical';
  status: 'CALCULATED_FROM_VERIFIED_REPLAYS'; clinical_validation: false;
  evidence_qualification: 'QUALIFIED_SAVED_REPLAY_BINDINGS';
  binding: { dataset_version_id: Uuid; dataset_checksum: string; implementation_sha256: string;
    configuration_checksums: Record<string, string>; measurement_plan_ref: string };
  denominators: { patients: number; periods: number; labels: Record<string, number>; unknown_labels_excluded_from_negative_denominator: number };
  future_or_unverified_facts_excluded: number; static: ClinicalMetricSummary; dynamic: ClinicalMetricSummary;
  partitions: Record<'development' | 'validation', { patients: number; periods: number; static: ClinicalMetricSummary; dynamic: ClinicalMetricSummary }>;
  subgroups: Record<string, Record<string, { patients: number; static: ClinicalMetricSummary; dynamic: ClinicalMetricSummary }>>;
  confidence_interval_protocol_ref: string | null; limitations: string[];
  execution_steps: { period_id: string; step_id: string; mode: 'static' | 'dynamic'; task_id: Uuid;
    execution_input_sha256: string; result_sha256: string; corpus_sha256: string; status: 'completed' | 'insufficient' }[];
  sequence_policy: { assessability: 'all_scheduled_steps_completed'; occurrence_policy: 'first_positive_then_repeat';
    reset_policy: 'period_boundary_only'; static_criterion: 'classification_meets' | 'ms_threshold_true'; dynamic_criterion: 'decision_met' };
}
export type ClinicalIntervalMetricId = 'sensitivity' | 'missed_event_fraction' | 'ppv'
  | 'alerts_per_observation_person_hour' | 'alerts_per_negative_observation_person_hour'
  | 'event_unassessable_fraction' | 'first_alert_difference_mean_seconds' | 'first_alert_difference_median_seconds';
export interface ClinicalIntervalMetricSpec {
  spec_id: string; metric_id: ClinicalIntervalMetricId; mode: 'static' | 'dynamic';
  scope: { partition: 'development' | 'validation'; subgroup: { dimension: string; value: string } | null };
  aggregation: 'ratio_of_sums' | 'one_minus_ratio_of_sums' | 'pooled_detected_event_mean' | 'pooled_detected_event_median';
  unit: 'fraction' | 'prompts_per_person_hour' | 'seconds';
  denominator_policy: 'assessable_independently_positive_events' | 'known_outcome_prompts_including_first_and_repeat'
    | 'completed_period_hours_including_unknown_labels' | 'completed_negative_period_hours'
    | 'all_independently_positive_events' | 'detected_events_only';
  required: boolean;
}
export interface ClinicalIntervalProtocol {
  schema_version: 'clinical-interval-protocol-1'; protocol_ref: string; measurement_plan_ref: string;
  method: 'patient_cluster_percentile_v1'; algorithm_version: '1'; confidence_level: number;
  alternative: 'two_sided'; quantile_method: 'linear'; cluster_key: 'patient_id';
  sampling: 'whole_cluster_with_replacement'; cluster_frame: 'all_frozen_patients_in_selected_partition';
  // Integer choices are explicit safe JSON integers, bounded at 2^53 - 1.
  partition_policy: 'separate_only'; n_resamples: number; rng_algorithm: 'PCG64'; seed: number;
  patient_order: 'patient_id_lexicographic'; numpy_version: string; implementation_sha256: string;
  invalid_resample_policy: 'fail_interval'; degenerate_policy: 'not_computed'; minimum_clusters: number;
  minimum_contributing_clusters: number; coverage: 'pointwise'; multiplicity_adjustment: 'none';
  metric_specs: ClinicalIntervalMetricSpec[];
}
export type ClinicalIntervalStatus = 'COMPLETE' | 'INCOMPLETE' | 'PENDING' | 'NOT_CONFIGURED'
  | 'UNSUPPORTED_PROTOCOL' | 'UNSUPPORTED_METHOD' | 'RESOURCE_LIMIT';
export type ClinicalIntervalResultStatus = 'CALCULATED' | 'PENDING' | 'UNSUPPORTED_PROTOCOL' | 'RESOURCE_LIMIT'
  | 'ZERO_DENOMINATOR' | 'INSUFFICIENT_CLUSTERS' | 'INVALID_RESAMPLE' | 'DEGENERATE_DISTRIBUTION' | 'NUMERIC_FAILURE';
export interface ClinicalIntervalResult extends ClinicalIntervalMetricSpec {
  estimate: number | null; lower: number | null; upper: number | null;
  confidence_level: number; method: 'patient_cluster_percentile_v1'; status: ClinicalIntervalResultStatus;
  reason_codes: string[]; numerator: number | null; denominator: number | null;
  clusters: number; contributing_clusters: number; draws_requested: number; draws_valid: number; draws_invalid: number;
  draw_sequence_sha256: string | null;
  diagnostics: { detected_events: number; missed_events: number; unknown_events: number; unassessable_positive_events: number };
}
export interface ClinicalIntervalEngine {
  method: 'patient_cluster_percentile_v1'; algorithm_version: '1'; numpy_version: string;
  implementation_sha256: string; rng_algorithm: 'PCG64';
  resource_limits: { max_resamples: number; max_clusters: number; max_draw_cells: number; max_work: number; max_seconds: number };
}
export interface ClinicalIntervalSection {
  schema_version: 'clinical-interval-results-1'; status: ClinicalIntervalStatus; required_complete: boolean;
  reason_codes: string[]; protocol_ref: string | null; protocol_sha256: string | null; input_sha256: string;
  engine: ClinicalIntervalEngine;
  // Unsupported protocols retain their original JSON body; never infer statistical choices from it.
  protocol: unknown; unlisted_metrics_status: 'NOT_REQUESTED'; results: ClinicalIntervalResult[];
}
export type ClinicalMetricsReportV3 = Omit<ClinicalMetricsReportV2, 'schema_version' | 'execution_steps' | 'sequence_policy'> & {
  schema_version: 'clinical-metrics-report-3'; confidence_intervals: ClinicalIntervalSection;
} & ({ point_report_schema_version: 'clinical-metrics-report-1'; execution_steps?: never; sequence_policy?: never }
  | { point_report_schema_version: 'clinical-metrics-report-2'; execution_steps: ClinicalMetricsReportV2['execution_steps'];
      sequence_policy: ClinicalMetricsReportV2['sequence_policy'] });
export type ClinicalMetricsReport = ClinicalMetricsReportV2 | ClinicalMetricsReportV3;
// Offline computation is not an API-qualified replay report. The presentation can
// safely show this shape without implying that the independent evidence was checked.
type ClinicalMetricsOffline<T> = T extends unknown ? Omit<T, 'status' | 'evidence_qualification'> & {
  status: 'CALCULATED_FROM_DECLARED_CAPTURE'; evidence_qualification: 'NOT_VERIFIED_BY_PURE_EVALUATOR';
} : never;
export type ClinicalMetricsOfflineReport = ClinicalMetricsOffline<ClinicalMetricsReport
  | (Omit<ClinicalMetricsReportV2, 'schema_version' | 'execution_steps' | 'sequence_policy'> & {
      schema_version: 'clinical-metrics-report-1'; execution_steps?: never; sequence_policy?: never;
    })>;
export interface EvaluationResultsView {
  clinical_report?: ClinicalMetricsReport | null;
  schema_version: 'evaluation-results-1'; run_id: Uuid; status: 'completed' | 'insufficient'; results: EvaluationUnitResult[];
  rules: EvaluationRuleProof[]; limitations: EvaluationLimitation[]; implementation_sha256: string;
  input_sha256: string; finished_at: Timestamp;
}
export interface AssessmentInput {
  kind: 'classification' | 'ms' | 'hlh'; input_rule_id?: Uuid; assessment_at: Timestamp; known_at: Timestamp; mode: 'current' | 'replay';
  rule_id: Uuid; window_policy_id: Uuid; verification_policy_id: Uuid; observation_period_id: Uuid | null;
  clinical_selections?: AssessmentClinicalSelection[];
}
export interface AssessmentClinicalSelection {
  variable_code: string; fact_version_id: Uuid; reason: string; evidence_ref: string; prior_task_id: Uuid | null;
}
export interface AssessmentSelectionView extends AssessmentClinicalSelection { actor_id: Uuid; saved_at: Timestamp }
export interface VerificationRequestInput { record_version_id: Uuid; purpose: string; field_ids: Uuid[]; assigned_to: Uuid }
export interface VerificationInput {
  record_version_id: Uuid; purpose: string; policy_id: Uuid; evidence_source_id: Uuid; evidence_ref: string;
  fields: { field_id: Uuid; expected_state_version: Version; result: 'consistent' | 'needs_correction' | 'uncertain'; reason: string | null }[];
}
export interface VerificationRevokeInput { expected_verification_sha256: string; reason: string }
export interface VerificationIntentInput {
  operation_id: 'verification_v2_request_create' | 'verification_v2_submit' | 'verification_v2_revoke';
  target_key: string; request_id: Uuid; schema_version: '2.0.0';
  payload: VerificationRequestInput | VerificationInput | VerificationRevokeInput;
}
export interface VerificationCreationQuery { purpose: string }
export interface VerificationOriginalQuery { checksum: string; object_version: string }
export interface VerificationRequestLookup {
  lookup: 'request_id'; operation_id: VerificationIntentInput['operation_id']; target_key: string;
}
export interface VerificationFieldView {
  field_id: Uuid; field_code: string; raw_text: string | null; raw_numeric: string | null; raw_unit: string | null;
  original_name: string | null; value_type: 'decimal' | 'categorical' | 'text' | 'time'; categorical_value: string | null;
  qualifier: 'eq' | 'lt' | 'le' | 'gt' | 'ge' | null; method: string | null; reference_interval: string | null; report_flag: string | null;
  value_state: 'known' | 'unknown' | 'invalid'; medical_time_id: Uuid | null; source_anchor_id: Uuid | null;
  state_version: Version | null; status: 'unverified' | 'pending' | 'verified' | 'needs_correction' | 'revoked';
}
export interface VerificationResourceView {
  schema_version: 'verification-view-2'; resource_type: 'verification_request' | 'verification'; resource_id: Uuid;
  patient_id: Uuid; record_version_id: Uuid; record_id: Uuid; purpose: string; verification_epoch: Version;
  reference_sha256: string; fields: VerificationFieldView[];
  status?: 'pending' | 'in_review' | 'completed' | 'cancelled' | 'superseded'; assigned_to?: Uuid;
  created_at?: Timestamp; completed_at?: Timestamp | null; verifier_id?: Uuid; policy_id?: Uuid;
  evidence_source_id?: Uuid; evidence_ref?: string; verified_at?: Timestamp; revoked_at?: Timestamp | null;
  results?: { field_id: Uuid; result: 'consistent' | 'needs_correction' | 'uncertain'; reason: string | null; current_for_purpose: boolean }[];
}
export interface VerificationOptionsView {
  schema_version: 'verification-options-2'; verification_request_id: Uuid; patient_id: Uuid; record_version_id: Uuid; purpose: string;
  policies: { policy_id: Uuid; config_key: string; version: Version; evidence_purpose: string }[];
  evidence: { source_revision_id: Uuid; evidence_ref: string; allowed_policy_ids: Uuid[] }[];
}
export interface AssessmentIntentInput {
  operation_id: 'assessment_v2_create'; target_key: string; request_id: Uuid; schema_version: '2.0.0'; payload: AssessmentInput;
}
export interface VerificationCreationScopeView {
  schema_version: 'verification-creation-scope-2'; patient_id: Uuid; record_version_id: Uuid; record_id: Uuid; purpose: string;
  fields: VerificationFieldView[]; assignees: { principal_id: Uuid; field_ids: Uuid[] }[];
}
export interface AssessmentCreationQuery { known_at: Timestamp; mode: AssessmentInput['mode'] }
export interface ClinicalDirectoryQuery { action: ClinicalDirectoryView['action'] }
export interface ClinicalDirectoryRecord {
  record_id: Uuid; record_version_id: Uuid; version: Version; kind: 'observation' | 'report' | 'therapy' | 'professional';
  source_organization: string | null; recorded_at: Timestamp; purpose: string;
}
export interface ClinicalDirectoryView {
  schema_version: 'clinical-directory-2'; action: 'verify_field' | 'execute_assessment';
  patients: { patient_id: Uuid; display_id: string; purposes: string[]; records: ClinicalDirectoryRecord[] }[];
}
export interface AssessmentConfigurationOption { config_id: Uuid; config_key: string; version: Version; checksum: string }
/** Exact Schema time shape before the application strips storage identity. */
export interface AssessmentObservationTime extends Required<Omit<MedicalTime, 'id' | 'patient_id'>> { id?: Uuid; patient_id?: Uuid }
export interface AssessmentSelectionCandidate {
  fact_version_id: Uuid; field_id: Uuid; record_version_id: Uuid; fact_version: Version; original_name: string | null;
  raw_value: string | null; raw_unit: string | null; qualifier: 'eq' | 'lt' | 'le' | 'gt' | 'ge' | null; context_type: string;
  medical_time: Required<Omit<MedicalTime, 'id' | 'patient_id'>> | null; available_at: Timestamp | null;
}
export interface AssessmentCreationPlan {
  kind: 'classification' | 'ms' | 'hlh'; input_rule?: AssessmentConfigurationOption; purpose: string; rule: AssessmentConfigurationOption; window: AssessmentConfigurationOption; verification: AssessmentConfigurationOption;
  selection_variables: { variable_code: string; concept_code: string; candidates: AssessmentSelectionCandidate[] }[];
  selection_evidence: string[]; prior_selections: { task_id: Uuid; saved_at: Timestamp; choices: AssessmentClinicalSelection[] }[];
}
export interface AssessmentCreationOptions {
  schema_version: 'assessment-creation-options-2'; patient_id: Uuid; display_id: string; known_at: Timestamp; mode: 'current' | 'replay';
  plans: AssessmentCreationPlan[];
  observation_periods: { observation_period_id: Uuid; period_id: Uuid; version: Version; label: string | null;
    start_time: Required<Omit<MedicalTime, 'id' | 'patient_id'>> | null; end_time: Required<Omit<MedicalTime, 'id' | 'patient_id'>> | null }[];
}
export interface AssessmentSelectionCandidateWire extends Omit<AssessmentSelectionCandidate, 'medical_time'> {
  medical_time: AssessmentObservationTime | null;
}
export interface AssessmentCreationPlanWire extends Omit<AssessmentCreationPlan, 'selection_variables'> {
  selection_variables: Array<Omit<AssessmentCreationPlan['selection_variables'][number], 'candidates'> & { candidates: AssessmentSelectionCandidateWire[] }>;
}
export interface AssessmentObservationPeriodWire extends Omit<AssessmentCreationOptions['observation_periods'][number], 'start_time' | 'end_time'> {
  start_time: AssessmentObservationTime | null; end_time: AssessmentObservationTime | null;
}
export interface AssessmentCreationOptionsWire extends Omit<AssessmentCreationOptions, 'plans' | 'observation_periods'> {
  plans: AssessmentCreationPlanWire[]; observation_periods: AssessmentObservationPeriodWire[];
}
export interface AssessmentRequestLookup {
  lookup: 'request_id'; operation_id: 'assessment_v2_create'; target_key: string;
}
export interface ClassificationOutput { classification_result: 'meets' | 'not_meets' | 'insufficient' | 'not_applicable'; conditions: Record<string, 'met' | 'not_met' | 'insufficient'> }
export interface MsOutput {
  ms_total: string; ms_threshold_result: boolean;
  contributions: { cns_involvement: string; hemorrhagic_manifestations: string; active_arthritis: string; platelet_count: string; ldh: string; fibrinogen: string; ferritin: string };
}
export interface AssessmentReason { variable_code: string; reason_codes: string[] }
export interface AssessmentContextStates {
  population: 'met' | 'not_met' | 'unknown' | 'not_required_by_policy';
  therapy: 'met' | 'not_met' | 'unknown';
  clinical_finding: 'met' | 'not_met' | 'unknown' | 'not_required_by_policy';
  late_result: 'met' | 'not_met' | 'unknown' | 'not_required_by_policy';
}
export interface AssessmentStoredInput {
  variable_code: string; input_snapshot: { role: 'current' | 'context'; value: string; unit: string | null;
    reference: { [key: string]: unknown } };
}
export type HlhState = 'met' | 'not_met' | 'unknown';
export type HlhCriterionCode = 'fever' | 'splenomegaly' | 'cytopenias' | 'lipids_or_fibrinogen'
  | 'hemophagocytosis' | 'nk_activity' | 'ferritin' | 'soluble_cd25';
export interface HlhConditionResult { state: HlhState; reason_codes: string[]; fact_version_ids: Uuid[] }
export interface HlhCriterionResult extends HlhConditionResult {
  code: HlhCriterionCode; subconditions: Record<string, HlhConditionResult>;
}
export interface HlhReferenceResult {
  schema_version: 'hlh-2004-result-1'; kind: 'hlh_2004_reference';
  use_boundary: 'offline_reference_support_only'; clinical_use_authorized: false;
  policy_revision_id: Uuid; policy_checksum: string; input_checksum: string;
  source_table: 'histiocyte-society-2004-table-2' | 'henter-2007-table-I'; source_revision_id: Uuid;
  semantics_version: 'hlh-2004-bounds-1'; assessment_at: Timestamp; known_at: Timestamp;
  applicability: 'applicable' | 'not_applicable' | 'unknown';
  applicability_condition: HlhConditionResult; molecular_condition: HlhConditionResult;
  criteria: HlhCriterionResult[]; criteria_met_lower_bound: number; criteria_met_upper_bound: number;
  criteria_path: HlhState; molecular_or_criteria: HlhState;
  reference_result: 'criteria_supported' | 'criteria_not_supported' | 'insufficient' | 'not_applicable';
  supported_paths: ('molecular' | 'five_of_eight')[]; limitation_codes: string[];
}
export interface AssessmentOutput {
  classification_conditions: Record<string, string> | null;
  classification_result: 'meets' | 'not_meets' | 'insufficient' | 'not_applicable' | null;
  ms_contributions: Record<string, string> | null; ms_total: string | null; ms_threshold_result: boolean | null;
  hlh_result?: HlhReferenceResult | null;
}
export interface AssessmentView {
  schema_version: 'static-assessment-view-2'; assessment_id: Uuid; patient_id: Uuid; kind: 'classification' | 'ms' | 'hlh';
  assessment_at: Timestamp; known_at: Timestamp; mode: 'current' | 'replay'; task: TaskView;
  computation_status: 'not_computed' | 'configuration_pending' | 'insufficient' | 'completed' | 'failed';
  validity: 'valid' | 'invalid'; missing_items: AssessmentReason[]; rule_id: Uuid; window_policy_id: Uuid;
  input_sha256: string; implementation_sha256: string; context_states: AssessmentContextStates;
  limitations: AssessmentReason[]; applicability: 'applicable' | 'not_applicable' | 'insufficient'; computed_at: Timestamp;
  inputs: AssessmentStoredInput[]; exclusions: { field_id: Uuid; fact_version_id: Uuid; reason_codes: string[] }[];
  clinical_selections: AssessmentSelectionView[];
  output: AssessmentOutput | null; reason_code: string | null;
}
/** The Schema permits omission; readAssessment requires the saved array before use. */
export interface AssessmentViewWire extends Omit<AssessmentView, 'clinical_selections'> { clinical_selections?: AssessmentSelectionView[] }
export interface DynamicInput {
  mode: 'current' | 'replay'; assessment_at: Timestamp; known_at: Timestamp; policy_id: Uuid;
  input_rule_id: Uuid; verification_policy_id: Uuid; use_authorization_id: Uuid;
  window_policy_id: Uuid; observation_period_id: Uuid | null; clinical_selections?: DynamicClinicalSelection[];
}
export interface DynamicClinicalSelection extends AssessmentClinicalSelection { input_role: 'current' | 'baseline' }
export interface DynamicIntentInput {
  operation_id: 'alert_evaluation_v2_create'; target_key: string; request_id: Uuid; schema_version: '2.0.0'; payload: DynamicInput;
}
export interface DynamicRequestLookup {
  lookup: 'request_id'; operation_id: DynamicIntentInput['operation_id']; target_key: string;
}
export interface DynamicTriggerQuery { cursor?: string }
export interface DynamicCreationPlan {
  kind: 'dynamic'; purpose: string; rule: AssessmentConfigurationOption; input_rule: AssessmentConfigurationOption;
  window: AssessmentConfigurationOption; verification: AssessmentConfigurationOption; use_authorization_ids: Uuid[];
  selection_variables: { variable_code: string; input_role: 'current' | 'baseline'; concept_code: string; candidates: AssessmentSelectionCandidate[] }[];
  selection_evidence: string[]; prior_selections: { task_id: Uuid; saved_at: Timestamp; choices: DynamicClinicalSelection[] }[];
}
export interface DynamicCreationOptions {
  schema_version: 'dynamic-creation-options-2'; patient_id: Uuid; display_id: string; known_at: Timestamp; mode: 'current' | 'replay';
  plans: DynamicCreationPlan[]; observation_periods: AssessmentCreationOptions['observation_periods'];
}
export interface DynamicCreationPlanWire extends Omit<DynamicCreationPlan, 'selection_variables'> {
  selection_variables: Array<Omit<DynamicCreationPlan['selection_variables'][number], 'candidates'> & { candidates: AssessmentSelectionCandidateWire[] }>;
}
export interface DynamicCreationOptionsWire extends Omit<DynamicCreationOptions, 'plans' | 'observation_periods'> {
  plans: DynamicCreationPlanWire[]; observation_periods: AssessmentObservationPeriodWire[];
}
export type DynamicState = 'met' | 'not_met' | 'unknown';
export interface DynamicTimeBounds { precision: string; earliest: Timestamp; latest_exclusive: Timestamp | null; instant: Timestamp | null }
export interface DynamicPair {
  position: number; baseline_field_id: Uuid; current_field_id: Uuid; baseline_time: DynamicTimeBounds; current_time: DynamicTimeBounds;
  state: DynamicState; reason_codes: string[]; absolute_delta: string | null; relative_delta: string | null;
  elapsed_seconds: number | null; elapsed_min_seconds: number | null; elapsed_max_seconds: number | null;
  relative_reason?: string | null; relative_exact?: { numerator: string; denominator: string } | null;
}
export interface DynamicSignal {
  signal_id: string; schema_version: 'alert-signal-1'; algorithm_version: 'dynamic-2'; state: DynamicState;
  reason_codes: string[]; input_field_ids: Uuid[]; inputs: { position: number; variable_code: string;
    input_role: 'current' | 'baseline' | 'series' | 'context'; reference: Record<string, unknown>; value: string }[];
  pair_results: DynamicPair[]; baseline_current_result?: DynamicPair | null;
  assessment_id?: Uuid; assessment_kind?: AssessmentInput['kind']; assessment_reference?: { model: 'assessment.assessment'; id: Uuid; sha256: string };
}
export interface DynamicWeightFraction { numerator: string; denominator: string }
export interface DynamicWeightedResult {
  schema_version: 'weighted-combination-result-1'; denominator: 'all_configured_weights';
  denominator_weight: string; met_weight: string; known_weight: string; unknown_weight: string;
  threshold_fraction: string; minimum_known_fraction: string;
  lower_fraction: DynamicWeightFraction; upper_fraction: DynamicWeightFraction; known_fraction: DynamicWeightFraction;
  core_gate: { operator: 'all' | 'at_least'; minimum_met: number | null };
  core_state: DynamicState; coverage_state: 'met' | 'unknown'; threshold_state: DynamicState;
  unknown_policy: 'bounded_no_imputation'; reason_codes: string[];
  members: { signal_ref: string; classification: 'core' | 'general'; weight: string; state: DynamicState }[];
}
export type DynamicGroup = {
  path: number[]; operator: 'all' | 'any' | 'at_least'; state: DynamicState;
  operands: DynamicState[]; signal_refs: string[]; minimum_met: number | null;
} | {
  path: number[]; operator: 'weighted'; state: DynamicState;
  operands: DynamicState[]; signal_refs: string[]; minimum_met: null; weighted: DynamicWeightedResult;
};
export interface DynamicCombination { state: DynamicState; groups: DynamicGroup[] }
export interface DynamicSensitivityPreset {
  schema_version: 'dynamic-sensitivity-preset-1'; preset_key: string; display_name: string; description: string;
  config_id: Uuid; config_checksum: string;
}
export interface DynamicLifecycle {
  state: 'current' | 'replay' | 'superseded'; action: 'create_event' | 'continue_episode' | 'close_episode' | 'no_event' | 'blocked';
  reason_codes: string[]; event_id: Uuid | null; episode_id: Uuid | null;
}
export interface DynamicView {
  schema_version: 'dynamic-evaluation-view-2'; evaluation_id: Uuid; patient_id: Uuid; task: TaskView;
  mode: 'current' | 'replay'; assessment_at: Timestamp; known_at: Timestamp; context_sequence: Version;
  status: 'configuration_pending' | 'insufficient' | 'completed' | 'failed' | 'superseded';
  decision: 'met' | 'not_met' | 'unknown'; validity: 'valid' | 'invalid'; computed_at: Timestamp;
  policy_id: Uuid; window_policy_id: Uuid; input_sha256: string; implementation_sha256: string;
  missing_items: AssessmentReason[]; context_states: AssessmentContextStates | null; limitations: AssessmentReason[];
  applicability: 'applicable' | 'not_applicable' | 'insufficient'; signals: DynamicSignal[];
  combinations: Record<'trigger' | 'reset' | 'retrigger', DynamicCombination> | null;
  sensitivity_preset?: DynamicSensitivityPreset | null;
  selected_refs: Record<string, Record<string, unknown> | null>; exclusions: Record<string, unknown>[];
  relation_refs: { model: string; id: Uuid; sha256: string }[];
  relation_proofs: { relation_id: Uuid; references: { model: string; id: Uuid; sha256: string }[]; reason_codes: string[] }[];
  clinical_selections: { choice: AssessmentClinicalSelection; input_role: 'current' | 'baseline'; actor_id: Uuid; saved_at: Timestamp | null;
    references: Record<string, unknown>[]; purpose: string; verification_id: Uuid }[];
  lifecycle: DynamicLifecycle | null; reason_code: string | null;
  automatic_trigger?: { schedule: { model: 'configurations.configversion'; id: Uuid; sha256: string };
    source: { model: 'tasks.outboxevent'; id: Uuid; sha256: string }; trigger_task_id: Uuid } | null;
}
export interface DynamicTriggerHistory {
  patient_id: Uuid; next_cursor: string | null; snapshot_version: string;
  items: { task_id: Uuid; status: TaskView['status']; status_version: Version; created_at: Timestamp; error_code: string | null;
    schedule_id: Uuid; source_id: Uuid; event_type: 'verification.changed' | 'configuration.changed' | 'clinical.context_changed' | 'dynamic.timer';
    occurred_at: Timestamp; evaluation_task_id: Uuid | null }[];
}
export interface ReminderInput {
  target_type: ReminderTarget; target_id: Uuid; target_version: Version; due_at: Timestamp;
  timezone: string; quiet_start: string | null; quiet_end: string | null; enabled?: boolean;
}
export interface ReminderChangeInput {
  expected_version: Version; action: 'disable' | 'enable' | 'pause' | 'snooze' | 'configure'; due_at: Timestamp | null;
  target_version?: Version; timezone?: string; quiet_start?: string | null; quiet_end?: string | null; enabled?: boolean;
}
export interface ReminderView {
  reminder_id: Uuid; patient_id: Uuid; version: Version; target_type: ReminderTarget; target_id: Uuid; target_version: Version;
  target_state: 'pending' | 'completed' | 'cancelled' | 'changed' | 'unavailable'; status: ReminderStatus;
  timezone: string; quiet_start: string | null; quiet_end: string | null;
  channel: string | null; channel_state: 'unavailable' | 'available'; channel_reason: string | null;
  due_at: Timestamp | null; next_due_at: Timestamp | null; last_notice_status: 'none' | 'pending' | DeliveryStatus; allowed_actions: string[];
}
export type ReminderList = Page<ReminderView>;
export interface ReminderTargetQuery extends PageQuery { target_type: ReminderTarget }
export interface ReminderTargetView {
  target_id: Uuid; patient_id: Uuid; target_type: ReminderTarget; target_version: Version;
  target_state: ReminderView['target_state']; saved_at: Timestamp;
  form_type: 'observation' | 'report' | 'therapy' | 'correction' | null;
}
export interface ReminderTargetList extends Page<ReminderTargetView> {
  capability_state: 'available' | 'unavailable'; capability_reason: string | null;
}
export interface DeliveryQuery extends PageQuery { patient_id?: Uuid; status?: DeliveryStatus }
export interface DeliveryView {
  attempt_id: Uuid; patient_id: Uuid; notice_kind: 'clinical' | 'material_reminder'; channel: string; status: DeliveryStatus;
  authorized_at: Timestamp; started_at: Timestamp | null; accepted_at: Timestamp | null; delivered_at: Timestamp | null;
  read_at: Timestamp | null; receipt_refs: string[]; allowed_actions: string[];
}
export type DeliveryList = Page<DeliveryView>;
export interface DeliveryResolveInput { expected_attempt_status: 'started' | 'accepted' | 'uncertain'; reason: string }

export interface RequestIdentityV2 {
  principalId: Uuid; audience: 'family' | 'support'; patientId: Uuid | null;
  scopeGeneration: number; operationId: string; objectId: Uuid | null;
  objectVersion: Version | null; requestSequence: number;
}
export type SubmitV2 =
  | { phase: 'ready' }
  | { phase: 'pending'; requestId: Uuid; intentId: Uuid; generation: Version }
  | { phase: 'committed'; receipt: WriteReceipt; readState: 'loading' | 'denied' }
  | { phase: 'success'; receipt: WriteReceipt; readVersion: Version }
  | { phase: 'failed'; code: string; mayRetry: boolean }
  | { phase: 'conflict'; currentVersion: Version };
