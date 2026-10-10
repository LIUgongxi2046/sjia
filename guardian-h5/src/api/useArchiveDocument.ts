import { useCallback, useEffect, useState } from 'react';
import type { DocumentView } from '../../../frontend-design/v2-types';
import { readArchiveDocument } from './archiveDocuments';

export function useArchiveDocument(documentId: string | null, patientId: string | null, owner: string | null) {
  const key = documentId && patientId && owner ? JSON.stringify([owner, patientId, documentId]) : null;
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [resource, setResource] = useState<{ key: string | null; data: DocumentView | null; error: Error | null; loading: boolean }>(
    { key, data: null, error: null, loading: !!key });
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => {
    setResource({ key, data: null, error: null, loading: !!key });
    setAttempt(value => value + 1);
  }, [key]);
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ key, data: null, error: null, loading: !!key && visible });
    if (key && documentId && patientId && visible) void readArchiveDocument(documentId, patientId, controller.signal).then(data => {
      if (!controller.signal.aborted) setResource({ key, data, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setResource({ key, data: null, error: failure, loading: false });
    });
    return () => controller.abort();
  }, [key, documentId, patientId, visible, attempt]);
  const current = visible && resource.key === key;
  return { data: current ? resource.data : null, error: current ? resource.error : null,
    loading: visible && (current ? resource.loading : !!key), reload };
}
