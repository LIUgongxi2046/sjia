import type { ArchiveSearchInput, ArchiveSearchView } from '../../../frontend-design/archive-search-types';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

const schema = 'archive-search.schema.json#/$defs/';

export async function searchArchives(patientId: string, query: ArchiveSearchInput, signal?: AbortSignal): Promise<ArchiveSearchView> {
  validateApiValue(`${schema}Uuid`, patientId);
  validateApiValue(`${schema}ArchiveSearchInput`, query);
  const result = validateApiValue<ArchiveSearchView>(`${schema}ArchiveSearchView`, await fetchJson<unknown>(
    `/api/v2/patients/${encodeURIComponent(patientId)}/archives/search`, { method: 'POST', body: query, signal }));
  if (result.patient_id !== patientId || result.items.length > (query.limit ?? 20)
    || result.matched_count > result.authorized_document_count) throw new Error('目录范围或数量与本次查询不一致。');
  const ids = new Set<string>();
  for (const item of result.items) {
    const value = item.document;
    if (value.patient_id !== patientId || ids.has(value.document_id) || query.document_id && value.document_id !== query.document_id
      || item.pages.length !== value.page_ids.length || item.pages.some((page, index) => page.page_id !== value.page_ids[index] || page.page_order !== index + 1)
      || item.snippet && (item.snippet.document_version_id !== value.version_id
        || !value.confirmed_text?.includes(item.snippet.leading_text + item.snippet.matched_text + item.snippet.trailing_text)))
      throw new Error('文书版本、原件页序或原文片段与准确来源不一致。');
    ids.add(value.document_id);
  }
  return result;
}
