// 对外字段引用 BACK 1.0.0；运行时仍须执行 JSON Schema 和领域校验。
export type Uuid = string;
export type Version = string;
export type Decimal = string;
export type Timestamp = string;
export type NullableText = string | null;
export type RecordKind = 'observation' | 'report' | 'therapy';
export type CaptureMethod = 'manual' | 'ocr_confirmed' | 'voice_confirmed';
export type VerificationStatus = 'unverified' | 'pending' | 'verified' | 'needs_correction' | 'revoked';

export interface MedicalTime {
  raw_text: NullableText;
  id?: Uuid;
  patient_id?: Uuid;
  precision: 'unknown' | 'year' | 'month' | 'month_range' | 'relative' | 'date' | 'minute' | 'second';
  local_year?: number | null;
  local_month?: number | null;
  end_year?: number | null;
  end_month?: number | null;
  relative_text?: NullableText;
  local_date: NullableText;
  local_datetime: NullableText;
  timezone_name: NullableText;
  utc_offset_minutes: number | null;
  instant: Timestamp | null;
  resolution_status: 'unknown' | 'local_only' | 'resolved' | 'ambiguous' | 'invalid';
}
export type MedicalTimeInput = Omit<MedicalTime, 'id' | 'patient_id' | 'instant' | 'resolution_status'> & {
  instant: null;
  resolution_status: 'unknown' | 'local_only' | 'ambiguous' | 'invalid';
};
export interface SourceReference {
  source_revision_id: Uuid | null;
  record_version_id: Uuid;
  field_id: Uuid;
  file_id: Uuid | null;
  page_id: Uuid | null;
  anchor_id: Uuid | null;
  verification_ids: Uuid[];
  mapping_id: Uuid | null;
}
export interface FieldInput {
  client_field_id: Uuid;
  field_code: string;
  concept_code: NullableText;
  dictionary_entry_id: Uuid | null;
  original_name: NullableText;
  value_type: 'decimal' | 'categorical' | 'text' | 'time';
  value_state: 'known' | 'unknown' | 'invalid';
  raw_text: NullableText;
  raw_numeric: Decimal | null;
  categorical_value: NullableText;
  qualifier: 'eq' | 'lt' | 'le' | 'gt' | 'ge' | null;
  raw_unit: NullableText;
  method: NullableText;
  reference_interval: NullableText;
  report_flag: NullableText;
  medical_time: MedicalTimeInput | null;
  sample_time_origin: 'report' | 'item' | 'observation' | 'therapy' | 'unknown';
  anchor_id: Uuid | null;
  candidate_field_id: Uuid | null;
}
export interface ClinicalField extends Omit<FieldInput, 'client_field_id' | 'anchor_id' | 'candidate_field_id' | 'medical_time'> {
  field_id: Uuid;
  ordinal: number;
  medical_time: MedicalTime | null;
  quality_reasons: string[];
  source_reference: SourceReference;
}
export interface FamilyField {
  field: ClinicalField;
  verification_status: VerificationStatus;
  verification_purpose: NullableText;
}
export interface ReportInput {
  external_ref: NullableText;
  source_organization: NullableText;
  report_status: 'unknown' | 'preliminary' | 'final' | 'corrected';
  sample_time: MedicalTimeInput | null;
  result_time: MedicalTimeInput | null;
  file_group_id: Uuid | null;
  replaces_report_id: Uuid | null;
}
export interface ReportMetadata extends Omit<ReportInput, 'sample_time' | 'result_time'> {
  report_id: Uuid;
  sample_time: MedicalTime | null;
  result_time: MedicalTime | null;
}
export interface Confirmation {
  candidate_field_id: Uuid;
  decision: 'accepted' | 'modified' | 'unknown' | 'excluded';
  confirmed_text: NullableText;
}
export interface RecordInput {
  kind: RecordKind;
  capture_method: CaptureMethod;
  source_revision_id: Uuid | null;
  field_config_version: Uuid;
  report: ReportInput | null;
  fields: FieldInput[];
  original_file_ids: Uuid[];
  draft_id: Uuid | null;
  draft_version: Version | null;
  candidate_set_id: Uuid | null;
  confirmations: Confirmation[];
}
export interface CorrectionInput { expected_version: Version; reason: string; replacement: RecordInput }
export interface FamilyRecord {
  patient_id: Uuid;
  display_id: string;
  record_id: Uuid;
  record_version_id: Uuid;
  version: Version;
  kind: RecordKind;
  origin_type: 'self_report' | 'report_transcription' | 'authorized_clinical';
  capture_method: CaptureMethod | 'authorized_import';
  recorded_at: Timestamp;
  quality_status: 'pending' | 'valid' | 'needs_correction';
  fields: FamilyField[];
  report: ReportMetadata | null;
  editable_field_ids: Uuid[];
  verification_coverage: 'none' | 'partial' | 'all';
  correction_reason: NullableText;
  author: AuthorView;
  original_files: FileView[];
  original_files_complete: boolean;
}
export interface Capability {
  capability_id: string;
  state: 'available' | 'disabled' | 'unavailable' | 'read_only';
  reason_code: NullableText;
  config_version: Uuid | null;
  limits?: { max_bytes?: number; max_pages?: number; max_pixels?: number; max_items?: number; media_types?: string[] };
}
export interface SessionView {
  csrf_token: string;
  principal_id: Uuid;
  account_status: 'active';
  expires_at: Timestamp;
  audience: 'family' | 'support';
  capabilities: Capability[];
}
export interface RelationView {
  relation_id: Uuid;
  patient_id: Uuid;
  display_id: string;
  status: 'active' | 'revoked' | 'expired';
  version: Version;
  valid_from: Timestamp;
  valid_until: Timestamp | null;
  actions: string[];
}
export interface ApplicationInput { evidence_ref: string; note: NullableText; requested_access: 'read' | 'read_write' | 'review_decides' }
export interface ApplicationView {
  application_id: Uuid;
  version: Version;
  status: 'pending' | 'approved' | 'rejected' | 'withdrawn';
  created_at: Timestamp;
  reason: NullableText;
  patient_id: Uuid | null;
  evidence_ref: string;
  note: NullableText;
  requested_access: 'read' | 'read_write' | 'review_decides' | null;
}
export interface Page<T> { items: T[]; next_cursor: NullableText; snapshot_version: string }
export interface RecordSearch {
  medical_time_id?: Uuid;
  kind?: RecordKind;
  concept_code?: string;
  origin_type?: FamilyRecord['origin_type'];
  verification_status?: VerificationStatus;
  from_date?: string;
  to_date?: string;
  scope_timezone?: string;
  time_group?: 'known' | 'unknown' | 'all';
  cursor?: string;
  limit?: number;
}
export interface TrendComparison {
  previous: SourceReference;
  current: SourceReference;
  direction: 'up' | 'down' | 'unchanged' | 'not_comparable';
  reason: NullableText;
}
export interface TrendView { records: Page<FamilyRecord>; comparisons: TrendComparison[] }
export interface DictionarySearch { category: string; query: string; cursor?: string; limit?: number }
export interface DictionaryView {
  version: Uuid;
  match_state: 'none' | 'unique' | 'ambiguous';
  options: { id: Uuid; concept_code: string; label: string; aliases: string[] }[];
  next_cursor: NullableText;
}
export type DraftField = Pick<FieldInput, 'client_field_id'> & Partial<Omit<FieldInput, 'client_field_id' | 'field_code'>> & { field_code?: NullableText };
export interface DraftPayload {
  original_file_ids?: Uuid[];
  kind?: RecordKind;
  capture_method?: CaptureMethod;
  source_revision_id?: Uuid | null;
  report?: ReportInput | null;
  fields?: DraftField[];
  candidate_set_id?: Uuid | null;
  confirmations?: Confirmation[];
  correction_reason?: NullableText;
}
export interface DraftInput {
  form_type: RecordKind | 'correction';
  field_config_version: Uuid;
  payload: DraftPayload;
  correction_record_id: Uuid | null;
  correction_version: Version | null;
}
export interface DraftUpdate { expected_version: Version; draft: DraftInput }
export interface DraftView {
  draft_id: Uuid;
  version: Version;
  saved_at: Timestamp;
  expires_at: Timestamp | null;
  status: 'editing' | 'submitted' | 'abandoned' | 'expired';
  draft: DraftInput;
}
export interface IntentInput {
  request_id: Uuid;
  operation_id: string;
  target_key: string;
  schema_version: '1.0.0';
  payload: object;
}
export interface IntentView {
  intent_id: Uuid;
  request_id: Uuid;
  generation: Version;
  status: 'prepared' | 'committed' | 'failed';
  expires_at: Timestamp;
  payload_sha256: string;
}
export interface WriteReceipt {
  resource_type: string;
  resource_id: Uuid;
  version: Version;
  saved_at: Timestamp;
  request_id: Uuid;
}
export interface RequestView {
  operation_id: string;
  target_key: string;
  prepared_at: Timestamp;
  intent_id: Uuid;
  request_id: Uuid;
  state: 'pending' | 'processing' | 'committed' | 'failed' | 'unknown' | 'not_found';
  may_retry: boolean;
  receipt: WriteReceipt | null;
  error_code: NullableText;
  generation?: Version;
  expires_at?: Timestamp;
  payload_sha256?: string;
  application_input?: ApplicationInput | { expected_state: 'pending'; expected_version: Version; reason: string } | null;
  controlled_input?: ReleaseInput | ContactInput | StateAction | GateAction | ConfigInput | ApprovalInput | ValidationInput;
}
export interface StateAction { expected_state: string; expected_version?: Version; reason: string }
export interface GateAction { expected_gate_version: Version; reason: string }
export interface ContactInput {
  contact_id: Uuid; contact_name: string; purpose: string; channel: string; channel_value: string;
  source_revision_id: Uuid; approval_config_id: Uuid; valid_until: Timestamp;
}
export interface ReleaseInput {
  environment: string;
  components: { kind: 'rule' | 'window' | 'verification' | 'mapping' | 'visibility' | 'content' | 'notification'; config_id: Uuid }[];
  contact_version_id: Uuid; validation_config_id: Uuid;
}
export interface ApiError {
  schema_version: '1.0.0';
  code: string;
  message: string;
  trace_id: string;
  retryable: boolean;
  recovery: 'none' | 'reauthenticate' | 'reload' | 'query_request' | 'query_task' | 'retry_after' | 'review_input';
  request_id: Uuid | null;
  task_id: Uuid | null;
  field_errors: { path: string; code: string; message: string }[];
}
export interface ApiSuccess<T> { schema_version: '1.0.0'; trace_id: string; data: T }
export interface FileDeclaration { upload_id: Uuid; checksum: string; size_bytes: number; media_type: string; ordinal: number }
export interface FileGroupInput { draft_id: Uuid; purpose: 'report' | 'therapy'; files: FileDeclaration[] }
export interface FileView {
  file_id: Uuid;
  status: 'quarantined' | 'available' | 'failed' | 'disposed';
  checksum: string;
  size_bytes: number;
  media_type: string;
  content_path: NullableText;
}
export interface FilePageView { page_id: Uuid; ordinal: number; width: number; height: number; rotation: 0 | 90 | 180 | 270; file: FileView }
export interface FileGroupView {
  group_id: Uuid;
  version: Version;
  status: 'initiated' | 'uploaded' | 'validating' | 'available' | 'failed' | 'superseded' | 'disposed';
  pages: FilePageView[];
  allowed_actions: string[];
}
export interface FileGroupComplete { expected_version: Version; file_ids: Uuid[] }
export interface RecognitionInput { file_group_id: Uuid; file_group_version: Version }
export interface AnchorView {
  anchor_id: Uuid;
  page_id: Uuid;
  kind: 'page' | 'bbox';
  original_text: string;
  region: { left: number; top: number; width: number; height: number } | null;
}
export interface CandidateField {
  field_config_version: Uuid;
  proposed_field: FieldInput | null;
  candidate_field_id: Uuid;
  field_path: string;
  raw_text: NullableText;
  recognition_status: 'recognized' | 'uncertain' | 'missing' | 'unlocated';
  anchor: AnchorView | null;
  confidence: Decimal | null;
}
export interface CandidateSetView {
  candidate_set_id: Uuid;
  file_group_id: Uuid;
  file_group_version: Version;
  status: 'pending_confirmation' | 'confirmed' | 'superseded' | 'cancelled';
  fields: CandidateField[];
}
export type TaskStatus = 'accepted' | 'blocked' | 'queued' | 'running' | 'awaiting_confirmation' | 'completed' | 'failed' | 'uncertain' | 'awaiting_resolution' | 'cancel_requested' | 'cancelled' | 'superseded';
export interface TaskView {
  task_id: Uuid;
  task_type: 'file_validation' | 'ocr' | 'assessment' | 'alert_evaluation' | 'summary' | 'notification' | 'disposition' | 'reconcile';
  request_id: Uuid | null;
  accepted_at: Timestamp;
  status: TaskStatus;
  status_version: Version;
  poll_after_ms: number | null;
  allowed_actions: string[];
  result_type: 'file_group' | 'candidate_set' | 'voice_input' | 'record_version' | 'assessment' | 'alert_evaluation' | 'summary' | 'notification' | 'disposition' | null;
  result_ref: Uuid | null;
  error_code: NullableText;
}
export type ContactView = { state: 'unavailable' } | {
  state: 'available'; contact_version_id: Uuid; contact_name: string; purpose: string;
  channel: string; channel_value: string; valid_until: Timestamp;
};
export interface FamilyClassification {
  classification_result?: 'meets' | 'not_meets' | 'insufficient' | 'not_applicable';
  conditions?: Record<string, 'met' | 'not_met' | 'insufficient'>;
}
export interface FamilyMs {
  ms_total?: Decimal;
  ms_threshold_result?: boolean;
  contributions?: Partial<Record<'cns_involvement' | 'hemorrhagic_manifestations' | 'active_arthritis' | 'platelet_count' | 'ldh' | 'fibrinogen' | 'ferritin', Decimal>>;
}
export interface FamilyClinicalProjection {
  content_id: Uuid;
  content_version: Version;
  approved_text: string;
  source_refs: SourceReference[];
  contact: ContactView;
  level?: 'attention' | 'warning' | 'urgent';
  classification?: FamilyClassification;
  ms?: FamilyMs;
}
export interface HomeView {
  assessment_state: 'not_evaluated' | 'configuration_pending' | 'insufficient' | 'current' | 'stale';
  assessed_at: Timestamp | null;
  current_alert_id: Uuid | null;
  patient_id: Uuid;
  display_id: string;
  service_state: 'enabled' | 'disabled' | 'unavailable';
  data_state: 'empty' | 'present' | 'incomplete';
  processing_state: 'idle' | 'pending' | 'running' | 'failed';
  alert_state: 'none' | 'current' | 'invalid';
  read_state: 'unread' | 'read' | 'not_applicable';
  recent_records: FamilyRecord[];
  capabilities: Capability[];
  clinical?: FamilyClinicalProjection;
}
export interface AlertView {
  patient_id: Uuid;
  display_id: string;
  alert_id: Uuid;
  validity: 'valid' | 'invalid';
  created_at: Timestamp;
  read_at: Timestamp | null;
  delivery_status: 'not_sent' | 'pending' | 'accepted' | 'delivered' | 'failed' | 'uncertain';
  clinical?: FamilyClinicalProjection;
}
export interface SummaryInput {
  from_date: string; to_date: string; scope_timezone: string; include_unknown_time: boolean;
  record_kinds: Array<'observation' | 'report' | 'therapy'>; include_source_status: boolean;
}
export interface ConfigInput {
  kind: 'dictionary' | 'mapping' | 'verification' | 'window' | 'rule' | 'visibility' | 'content' | 'summary' | 'notification' | 'provider' | 'validation';
  config_key: string; schema_version: string; payload: Record<string, unknown>; source_revision_id: Uuid | null;
}
export interface ApprovalInput {
  config_checksum: string; approval_role: string; evidence_ref: string; environment: string;
  audience: string[]; valid_until: Timestamp | null;
}
export interface ValidationInput {
  config_checksum: string; environment: string; artifact_sha256: string; dataset_revision_id: Uuid;
  evidence_ref: string; evidence_sha256: string;
}
export interface SummaryView {
  patient_id: Uuid;
  display_id: string;
  summary_id: Uuid;
  scope: SummaryInput;
  generated_at: Timestamp;
  validity: 'valid' | 'invalid';
  needs_regeneration: boolean;
  records: FamilyRecord[];
  clinical?: FamilyClinicalProjection;
}

export interface AuthorView { display_id: NullableText; role: 'family' | 'professional' | 'service'; is_self: boolean }
export interface AuthBootstrap { csrf_token: string; identity_state: 'available' | 'disabled' | 'unavailable'; return_paths: string[] }
export interface ApplicationAction { expected_state: 'pending'; expected_version: Version; reason: string }
export interface RequestRecoveryQuery { operation_id?: string; cursor?: string; limit?: number }
export interface SourceResolution {
  patient_id: Uuid; display_id: string; record_id: Uuid; record_version_id: Uuid;
  version: Version; field_id: Uuid; source: SourceReference;
}
export interface FieldConfigQuery { kind: RecordKind; version?: Uuid }
export interface FieldDefinition {
  field_code: string; label: string; value_type: FieldInput['value_type']; required: boolean; writable: boolean;
  allowed_values: string[]; allowed_units: string[]; dictionary_category: NullableText;
  minimum: Decimal | null; maximum: Decimal | null; max_length: number | null;
  max_occurrences: number; time_required: boolean;
}
export interface FieldConfigView {
  patient_id: Uuid; config_version: Uuid | null; checksum: string | null;
  state: 'available' | 'disabled' | 'read_only'; reason_code: NullableText;
  kind: RecordKind; fields: FieldDefinition[]; watch_field_codes: string[];
}
export interface HistoryQuery {
  kind?: RecordKind; precision?: MedicalTime['precision']; from_date?: string; to_date?: string;
  scope_timezone?: string; cursor?: string; limit?: number;
}
export interface HistoryEntry { medical_time_id: Uuid; medical_time: MedicalTime; sources: SourceReference[] }

// 以下类型仅表达客户端所有权，不能作为新增服务端字段提交。
export interface AccessContext {
  principalId: Uuid;
  audience: 'family';
  patientId: Uuid;
  relationId: Uuid;
  relationVersion: Version;
  scopeGeneration: number;
}
export interface RequestIdentity {
  context: Readonly<AccessContext>;
  objectId: string;
  objectVersion: Version | null;
  sequence: number;
}
export type RemoteState<T> =
  | { status: 'idle' | 'loading' | 'revalidating' }
  | { status: 'ready'; data: T; identity: RequestIdentity }
  | { status: 'empty'; identity: RequestIdentity }
  | { status: 'error'; error: ApiError | TransportError };
export interface TransportError { kind: 'network' | 'timeout' | 'protocol'; requestId: Uuid | null; taskId: Uuid | null }
export type SubmissionState =
  | { status: 'unavailable' | 'ready' }
  | { status: 'preparing'; requestId: Uuid }
  | { status: 'submitting' | 'uncertain' | 'checking'; intent: IntentView; identity: RequestIdentity }
  | { status: 'failed'; intent: IntentView; mayRetry: true }
  | { status: 'conflict'; expectedVersion: Version }
  | { status: 'committed'; receipt: WriteReceipt | null; intent: IntentView }
  | { status: 'saved'; receipt: WriteReceipt; record: FamilyRecord };
export interface FormState {
  context: Readonly<AccessContext>;
  formType: DraftInput['form_type'];
  payload: DraftPayload;
  inputRevision: number;
  savedInputRevision: number | null;
  draft: DraftView | null;
  composing: boolean;
  submission: SubmissionState;
}
export type VoiceState = 'idle' | 'connecting' | 'listening' | 'stopping' | 'reviewing' | 'error';
export type HomeDisplayState = 'loading' | 'access' | 'disabled' | 'failed' | 'pending' | 'evaluating' | 'not_evaluated' | 'incomplete' | 'clear' | 'attention' | 'warning' | 'urgent' | 'expired';
export interface NavigationMemory {
  principalId: Uuid;
  patientId: Uuid;
  scopeGeneration: number;
  pathname: string;
  locationKey: string;
  scroll: number;
  filters: RecordSearch;
  panel: 'temperature' | 'symptoms' | 'labs' | 'therapy' | null;
  selectedInstant: Timestamp | null;
}
export interface FrontendActions {
  selectRelation: (relation: RelationView) => void;
  clearRestrictedState: (reason: 'logout' | 'expired' | 'revoked' | 'account_changed' | 'hidden') => void;
  adoptRead: <T>(identity: RequestIdentity, response: T) => void;
  adoptTask: (identity: RequestIdentity, task: TaskView) => void;
  editField: (clientFieldId: Uuid, value: DraftField) => void;
  submitRecord: (input: RecordInput, inputRevision: number) => Promise<void>;
  querySubmission: () => Promise<void>;
}
