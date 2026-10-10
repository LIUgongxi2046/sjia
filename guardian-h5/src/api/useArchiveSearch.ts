import { useCallback, useEffect, useRef, useState } from 'react';
import type { ArchiveSearchInput, ArchiveSearchView } from '../../../frontend-design/archive-search-types';
import { searchArchives } from './archiveSearch';

export function useArchiveSearch(patientId: string | null, principalId: string | null, query: ArchiveSearchInput) {
  const queryKey = JSON.stringify(query);
  const key = patientId && principalId ? JSON.stringify([patientId, principalId, queryKey]) : null;
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string | null; data: ArchiveSearchView | null; loading: boolean; error: Error | null }>({ key, data: null, loading: !!key, error: null });
  const pending = useRef<AbortController | null>(null), busy = useRef(false);
  const reload = useCallback(() => { pending.current?.abort(); setState({ key, data: null, loading: !!key, error: null }); setAttempt(value => value + 1); }, [key]);
  useEffect(() => { const change = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', change); return () => document.removeEventListener('visibilitychange', change); }, []);
  useEffect(() => {
    const controller = new AbortController(); pending.current = controller; busy.current = !!key && visible;
    setState({ key, data: null, loading: !!key && visible, error: null });
    if (key && patientId && visible) void searchArchives(patientId, JSON.parse(queryKey) as ArchiveSearchInput, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ key, data, loading: false, error: null });
    }, failure => { if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setState({ key, data: null, loading: false, error: failure });
    }).finally(() => { if (pending.current === controller) busy.current = false; });
    return () => { pending.current?.abort(); busy.current = false; };
  }, [key, patientId, queryKey, visible, attempt]);
  function more() {
    if (!patientId || !visible || busy.current || state.key !== key || !state.data?.next_cursor) return;
    const existing = state.data, controller = new AbortController(); pending.current = controller; busy.current = true;
    const cursor = existing.next_cursor;
    if (!cursor) throw new Error('目录尚无后续页面。');
    setState({ key, data: existing, loading: true, error: null });
    void searchArchives(patientId, { ...JSON.parse(queryKey) as ArchiveSearchInput, cursor }, controller.signal).then(value => {
      const previous = new Set(existing.items.map(item => item.document.document_id));
      if (value.snapshot_version !== existing.snapshot_version || value.matched_count !== existing.matched_count
        || value.items.some(item => previous.has(item.document.document_id))) throw new Error('文书目录已有更新，请重新读取。');
      if (!controller.signal.aborted) setState({ key, data: { ...value, items: [...existing.items, ...value.items] }, loading: false, error: null });
    }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setState({ key, data: null, loading: false, error: failure });
    }).finally(() => { if (pending.current === controller) busy.current = false; });
  }
  const current = visible && state.key === key;
  return { data: current ? state.data : null, loading: visible && (current ? state.loading : !!key), error: current ? state.error : null, reload, more, visible };
}
