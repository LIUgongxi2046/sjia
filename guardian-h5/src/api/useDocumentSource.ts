import { useCallback, useEffect, useState } from 'react';
import type { ArchiveSearchItem } from '../../../frontend-design/archive-search-types';
import { searchArchives } from './archiveSearch';
import { fetchJson } from './client';
import { validateApiValue } from './schema';

export function useDocumentSource(patientId: string | null, principalId: string | null, documentId: string, versionId?: string | null) {
  const key = patientId && principalId ? JSON.stringify([patientId, principalId, documentId, versionId]) : null;
  const [visible, setVisible] = useState(document.visibilityState === 'visible'), [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string | null; data: ArchiveSearchItem | null; error: Error | null; loading: boolean }>({ key, data: null, error: null, loading: !!key });
  const reload = useCallback(() => { setState({ key, data: null, error: null, loading: !!key }); setAttempt(value => value + 1); }, [key]);
  useEffect(() => { const change = () => setVisible(document.visibilityState === 'visible'); document.addEventListener('visibilitychange', change);
    return () => document.removeEventListener('visibilitychange', change); }, []);
  useEffect(() => {
    const controller = new AbortController();
    setState({ key, data: null, error: null, loading: !!key && visible });
    const read = async () => {
      if (!patientId) throw new Error('原文读取尚未取得当前患儿身份。');
      validateApiValue('archive-search.schema.json#/$defs/Uuid', documentId);
      if (versionId) validateApiValue('archive-search.schema.json#/$defs/Uuid', versionId);
      const value = versionId ? validateApiValue<ArchiveSearchItem>('archive-search.schema.json#/$defs/Item', await fetchJson<unknown>(
        `/api/v2/archive-document-versions/${encodeURIComponent(versionId)}/source`, { signal: controller.signal }))
        : (await searchArchives(patientId, { document_id: documentId, limit: 1 }, controller.signal)).items[0];
      if (!value || value.document.patient_id !== patientId || value.document.document_id !== documentId
        || versionId && value.document.version_id !== versionId || value.pages.length !== value.document.page_ids.length
        || value.pages.some((page, index) => page.page_id !== value.document.page_ids[index] || page.page_order !== index + 1))
        throw new Error('原始文书身份、准确版本或原页顺序不一致。');
      return value;
    };
    if (key && visible) void read().then(data => { if (!controller.signal.aborted) setState({ key, data, error: null, loading: false }); },
      failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setState({ key, data: null, error: failure, loading: false }); });
    return () => controller.abort();
  }, [key, patientId, documentId, versionId, visible, attempt]);
  const current = visible && state.key === key;
  return { data: current ? state.data : null, error: current ? state.error : null, loading: visible && (current ? state.loading : !!key), reload };
}
