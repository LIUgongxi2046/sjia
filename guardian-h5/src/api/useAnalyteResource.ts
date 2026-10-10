import { useCallback, useEffect, useState } from 'react';
import { searchAnalyte, type AnalyteTrend } from './endpoints';

export function useAnalyteResource(principalId: string | null, patientId: string | null, conceptCode: string | null, enabled: boolean) {
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [version, setVersion] = useState(0);
  const ready = enabled && visible && !!principalId && !!patientId && !!conceptCode;
  const scope = ready ? JSON.stringify([principalId, patientId, conceptCode]) : null;
  const [resource, setResource] = useState<{ scope: string | null; data: AnalyteTrend | null; error: Error | null; loading: boolean }>(
    { scope, data: null, error: null, loading: ready });
  const reload = useCallback(() => {
    setResource({ scope, data: null, error: null, loading: ready });
    setVersion(value => value + 1);
  }, [scope, ready]);
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ scope, data: null, error: null, loading: ready });
    if (ready && patientId && conceptCode) void searchAnalyte(patientId, conceptCode, { signal: controller.signal }).then(data => {
      if (!controller.signal.aborted) setResource({ scope, data, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setResource({ scope, data: null, error: failure, loading: false });
    });
    return () => controller.abort();
  }, [scope, ready, patientId, conceptCode, version]);
  const current = ready && resource.scope === scope;
  return { data: current ? resource.data : null, error: current ? resource.error : null,
    loading: ready && (!current || resource.loading), reload };
}
