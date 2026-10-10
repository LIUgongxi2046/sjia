import { useCallback, useEffect, useRef, useState } from 'react';
import type { RelationDirectory } from '../../../frontend-design/archive-relations-types';
import { readRelationDirectory } from './archiveRelations';

export function useArchiveRelations(patientId: string | null, principalId: string | null) {
  const key = patientId && principalId ? JSON.stringify([patientId, principalId]) : null;
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{ key: string | null; data: RelationDirectory | null; loading: boolean; error: Error | null }>({ key, data: null, loading: !!key, error: null });
  const pending = useRef<AbortController | null>(null);
  const loading = useRef(false);
  const reload = useCallback(() => { pending.current?.abort(); setState({ key, data: null, loading: !!key, error: null }); setAttempt(value => value + 1); }, [key]);
  useEffect(() => { const change = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', change); return () => document.removeEventListener('visibilitychange', change); }, []);
  useEffect(() => {
    const controller = new AbortController(); pending.current = controller; loading.current = !!key && visible;
    setState({ key, data: null, loading: !!key && visible, error: null });
    if (key && patientId && visible) void readRelationDirectory(patientId, null, controller.signal).then(data => {
      if (!controller.signal.aborted) setState({ key, data, loading: false, error: null });
    }, failure => { if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setState({ key, data: null, loading: false, error: failure });
    }).finally(() => { if (pending.current === controller) loading.current = false; });
    return () => controller.abort();
  }, [key, patientId, visible, attempt]);
  function more() {
    if (!patientId || !visible || loading.current || state.key !== key || !state.data?.next_cursor) return;
    const existing = state.data;
    const controller = new AbortController(); pending.current = controller; loading.current = true;
    setState({ key, data: existing, loading: true, error: null });
    void readRelationDirectory(patientId, existing.next_cursor, controller.signal).then(value => {
      if (value.snapshot_version !== existing.snapshot_version) throw new Error('关系目录已有更新，请重新读取。');
      const sources = [...new Map([...existing.sources, ...value.sources].map(item => [item.id, item])).values()];
      const candidates = [...new Map([...existing.candidates, ...value.candidates].map(item => [JSON.stringify([item.source_kind, item.left_id, item.right_id]), item])).values()];
      const relations = [...new Map([...existing.relations, ...value.relations].map(item => [item.relation_id, item])).values()];
      if (!controller.signal.aborted) setState({ key, data: { ...value, sources, candidates, relations }, loading: false, error: null });
    }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setState({ key, data: null, loading: false, error: failure });
    }).finally(() => { if (pending.current === controller) loading.current = false; });
  }
  const current = visible && state.key === key;
  return { data: current ? state.data : null, loading: visible && (current ? state.loading : !!key), error: current ? state.error : null, reload, more, visible };
}
