import type { DocumentInput, DocumentView, RequestView, WriteReceipt } from './v2-types';
export interface RegroupOutput extends Required<DocumentInput> { document_id: string | null; metadata_confirmed: true }
export interface RegroupInput { batch_id: string; sources: { document_id: string; expected_version: string }[]; outputs: RegroupOutput[]; unassigned_page_ids: string[]; reason: string }
export interface RegroupIdentity { operation_id: 'archive_documents_regroup'; target_key: string; request_id: string; schema_version: '2.0.0'; payload: RegroupInput }
export interface RegroupResult { regrouping_id: string; patient_id: string; batch_id: string; sources: { document_id: string; version_id: string; version: string; page_ids: string[] }[]; outputs: { document_id: string; version_id: string; version: string; page_ids: string[] }[]; retired_document_ids: string[]; unassigned_page_ids: string[] }
export interface RegroupReceipt extends Omit<WriteReceipt, 'resource_type'> { resource_type: 'archive_regrouping'; result: RegroupResult }
export interface RegroupRequest extends Pick<RequestView, 'intent_id' | 'request_id' | 'operation_id' | 'target_key' | 'prepared_at' | 'state' | 'may_retry' | 'error_code'> { generation: string; expires_at: string; payload_sha256: string; regroup_input: RegroupInput; receipt: RegroupReceipt | null }
export interface RegroupOptions { patient_id: string; batch_id: string; documents: DocumentView[] }
