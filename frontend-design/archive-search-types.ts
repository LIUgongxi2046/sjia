import type { DocumentView } from './v2-types';
import type { MedicalTimeInput } from './types';
import type { OriginalSourceView } from '../guardian-h5/src/api/originalSourceSchema';

export interface ArchiveSearchInput {
  cursor?: string; limit?: number; query?: string; organization?: string;
  document_type?: DocumentView['document_type']; category?: 'all' | 'inpatient' | 'outpatient' | 'pending';
  episode_id?: string; document_id?: string; from_date?: string; to_date?: string; from_time?: MedicalTimeInput; to_time?: MedicalTimeInput; confirmed_text?: string; project?: string;
  origin_type?: 'self_report' | 'report_transcription' | 'authorized_clinical';
  verification_status?: 'self' | 'unverified' | 'pending' | 'verified' | 'needs_correction' | 'revoked';
  include_unknown_time?: boolean; unknown_organization?: boolean; unknown_episode?: boolean;
}
export interface ArchiveEpisode {
  episode_id: string; label: string; episode_type: string; organization: string | null; record_version_id: string;
}
export interface ArchiveSearchItem {
  document: DocumentView;
  pages: { page_id: string; file_page_id: string; file_id: string; page_order: number; object_version: string; checksum: string;
    status: 'available' | 'quarantined' | 'failed' | 'disposed'; media_type: 'image/png' | 'image/jpeg' | 'image/webp' | 'application/pdf' | 'text/plain';
    created_at: string; use_authorizations: OriginalSourceView['originals'][number]['use_authorizations']; purpose_copies: OriginalSourceView['originals'][number]['purpose_copies'] }[];
  episodes: ArchiveEpisode[];
  snippet: { document_version_id: string; leading_text: string; matched_text: string; trailing_text: string;
    leading_truncated: boolean; trailing_truncated: boolean; source_scope: 'confirmed_document_text' } | null;
  matched_fields: ('organization' | 'document_type' | 'confirmed_text' | 'episode_label')[];
  verification_status: 'unverified' | 'pending' | 'verified' | 'needs_correction' | 'revoked'; limitations: string[];
}
export interface ArchiveSearchView {
  patient_id: string; items: ArchiveSearchItem[]; next_cursor: string | null; snapshot_version: string;
  matched_count: number; authorized_document_count: number; elapsed_ms: number;
  facets: { organizations: string[]; episodes: ArchiveEpisode[]; unknown_organization_count: number;
    unknown_time_count: number; unknown_episode_count: number };
}
