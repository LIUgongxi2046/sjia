export interface PersonalTaskInput { title: string; description: string | null; retention_policy_id: string }
export interface PersonalTaskChange { expected_version: string; action: 'complete' | 'cancel' }
export interface PersonalTaskView {
  task_id: string; patient_id: string; version: string; title: string; description: string | null;
  status: 'pending' | 'completed' | 'cancelled'; retention_policy_id: string;
  created_at: string; updated_at: string; finished_at: string | null; allowed_actions: ('complete' | 'cancel')[];
}
export interface PersonalTaskList { items: PersonalTaskView[]; next_cursor: string | null; directory_version: string }
export interface PersonalTaskOptions {
  items: { policy_id: string; policy_key: string; version: string }[];
  capability_state: 'available' | 'unavailable'; capability_reason: string | null;
}
export interface PersonalTaskIdentity {
  operation_id: 'personal_record_task_create' | 'personal_record_task_change'; target_key: string;
  request_id: string; schema_version: '2.0.0'; payload: PersonalTaskInput | PersonalTaskChange;
}
export interface PersonalTaskReceipt {
  schema_version: '2.0.0'; resource_type: 'personal_record_task'; resource_id: string; version: string;
  saved_at: string; request_id: string; task_id: null;
}
export interface PersonalTaskRequest {
  intent_id: string; request_id: string; operation_id: PersonalTaskIdentity['operation_id']; target_key: string;
  prepared_at: string; state: 'pending' | 'committed' | 'failed' | 'unknown'; may_retry: boolean;
  receipt: PersonalTaskReceipt | null; error_code: string | null; generation: string; expires_at: string;
  payload_sha256: string; record_task_input: PersonalTaskInput | PersonalTaskChange;
}
