import { fetchJson } from './client';
import { validateApiValue } from './schema';

export type VoiceInputMode = 'LOCAL_AUDIO_CONFIRMED_TEXT' | 'SIMULATION_TEXT_ONLY';
export type VoiceCandidatePath = 'temperature' | 'temperature_unit' | 'symptoms' | 'observed_time' | 'note';
export interface VoiceCandidates {
  temperature: number | null;
  temperature_unit: 'C' | 'F' | null;
  symptoms: string[];
  observed_time: string | null;
  note: string | null;
}
export interface VoiceDecision {
  field_path: VoiceCandidatePath;
  decision: 'accepted' | 'modified' | 'unknown' | 'excluded';
  selected_value: string | string[] | Record<string, unknown> | null;
  record_field_codes: string[];
}
export interface VoiceInputRead {
  id: string; patient_id: string; request_id: string; input_mode: VoiceInputMode;
  status: 'awaiting_review' | 'ready' | 'running' | 'awaiting_confirmation' | 'saved' | 'failed' | 'uncertain' | 'cancelled' | 'revoked';
  status_version: string; transcript: string; transcript_sha256: string;
  original_file_id: string; field_config_id: string;
  task_id: string | null; task_status: string | null; task_status_version: string | null;
  provider_config_id: string | null; purpose_copy_id: string | null;
  candidates: VoiceCandidates | null; decisions: VoiceDecision[] | null;
  saved_record_version_id: string | null; raw_response_file_id: string | null;
  provider_evidence: Array<Record<string, unknown>>;
  transcription_evidence: Record<string, unknown>;
  confirmed_at: string; created_at: string; decided_at: string | null;
}
export interface VoiceSourceOption {
  source_revision_id: string; family_authorization_id: string; external_authorization_id: string;
}
export interface VoiceOriginalOption {
  original_file_id: string; checksum: string; confirmed_text: string; source_revision_id: string;
  purpose_copy_id: string | null; state: 'available' | 'awaiting_review';
  purpose_request_id: string | null; purpose_task_id: string | null;
  purpose_state: 'not_requested' | 'processing' | 'pending_review' | 'approved' | 'rejected' | 'revoked' | 'disposed' | 'failed';
  purpose_reason_code: string | null;
}
export interface VoiceInputOptions {
  patient_id: string; state: 'available' | 'unavailable'; reason_code: string | null;
  field_config_id: string | null; provider_config_id: string | null;
  source_options: VoiceSourceOption[]; original_options: VoiceOriginalOption[];
  input_modes: VoiceInputMode[];
}
export interface VoiceCreateInput {
  original_file_id: string; request_id: string; authorization_id: string; field_config_id: string;
  confirmed_text: string; transcript_confirmed: true; input_mode: VoiceInputMode;
  transcription_evidence_id?: string | null;
}
export interface VoiceProcessInput {
  expected_status_version: string; purpose_copy_id: string; provider_config_id: string;
}
export interface VoiceAdoptInput {
  voice_input_id: string; expected_status_version: string; decisions: VoiceDecision[];
}
export interface VoiceAdoptionResponse {
  voice_input: VoiceInputRead;
  record: import('./records').RecordView;
  idempotent_replay: boolean;
}

function checkedVoice(value: unknown, patientId: string, voiceId?: string): VoiceInputRead {
  const result = validateApiValue<VoiceInputRead>('voice-input.schema.json#/$defs/VoiceInputView', value);
  if (result.patient_id !== patientId || (voiceId !== undefined && result.id !== voiceId)) {
    throw new Error('语音资料与当前患儿或请求对象不一致。');
  }
  return result;
}

export async function readVoiceInputOptions(patientId: string, signal?: AbortSignal, isCurrent?: () => boolean): Promise<VoiceInputOptions> {
  const value = await fetchJson<unknown>(`/api/v2/patients/${patientId}/voice-input-options`, { signal, isCurrent });
  const result = validateApiValue<VoiceInputOptions>('voice-input.schema.json#/$defs/OptionsView', value);
  if (result.patient_id !== patientId) throw new Error('语音配置与当前患儿不一致。');
  return result;
}

export async function createVoiceInput(patientId: string, payload: VoiceCreateInput,
  signal?: AbortSignal): Promise<VoiceInputRead> {
  validateApiValue('voice-input.schema.json#/$defs/CreateInput', payload);
  const value = await fetchJson<unknown>(`/api/v2/patients/${patientId}/voice-inputs`, {
    method: 'POST', body: payload, signal,
  });
  const result = checkedVoice(value, patientId);
  if (result.request_id !== payload.request_id) throw new Error('语音保存回执与本次请求不一致。');
  return result;
}

export async function readVoiceInput(patientId: string, voiceId: string,
  signal?: AbortSignal): Promise<VoiceInputRead> {
  return checkedVoice(await fetchJson<unknown>(`/api/v2/patients/${patientId}/voice-inputs/${voiceId}`,
    { signal }), patientId, voiceId);
}

export async function lookupVoiceInput(patientId: string, requestId: string,
  signal?: AbortSignal): Promise<VoiceInputRead> {
  const value = await fetchJson<unknown>(`/api/v2/patients/${patientId}/voice-inputs?request_id=${encodeURIComponent(requestId)}`,
    { signal });
  const result = checkedVoice(value, patientId);
  if (result.request_id !== requestId) throw new Error('语音查询返回其他请求的资料。');
  return result;
}

export async function processVoiceInput(patientId: string, voiceId: string, payload: VoiceProcessInput,
  signal?: AbortSignal): Promise<VoiceInputRead> {
  validateApiValue('voice-input.schema.json#/$defs/ProcessInput', payload);
  return checkedVoice(await fetchJson<unknown>(`/api/v2/patients/${patientId}/voice-inputs/${voiceId}/process`,
    { method: 'POST', body: payload, signal }), patientId, voiceId);
}

export async function cancelVoiceInput(patientId: string, voiceId: string, expectedStatusVersion: string,
  signal?: AbortSignal): Promise<VoiceInputRead> {
  const payload = { expected_status_version: expectedStatusVersion };
  validateApiValue('voice-input.schema.json#/$defs/CancelInput', payload);
  return checkedVoice(await fetchJson<unknown>(`/api/v2/patients/${patientId}/voice-inputs/${voiceId}/cancel`,
    { method: 'POST', body: payload, signal }), patientId, voiceId);
}

export async function adoptVoiceInput(patientId: string, voiceId: string, payload: VoiceAdoptInput,
  signal?: AbortSignal): Promise<VoiceAdoptionResponse> {
  validateApiValue('voice-input.schema.json#/$defs/AdoptInput', payload);
  if (payload.voice_input_id !== voiceId) throw new Error('本人采用对象与语音记录不一致。');
  const value = await fetchJson<VoiceAdoptionResponse>(`/api/v2/patients/${patientId}/voice-inputs/${voiceId}/adopt`,
    { method: 'POST', body: payload, signal });
  validateApiValue('voice-input.schema.json#/$defs/AdoptionView', value);
  const result = checkedVoice(value.voice_input, patientId, voiceId);
  if (result.status !== 'saved' || result.saved_record_version_id !== value.record.record_version_id
      || value.record.patient_id !== patientId || value.record.kind !== 'observation'
      || value.record.capture_method !== 'voice_confirmed') {
    throw new Error('语音采用回执与保存记录不一致。');
  }
  return { ...value, voice_input: result };
}
