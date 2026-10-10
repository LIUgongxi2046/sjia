import { useCallback, useEffect, useState } from 'react';
import { fetchJson } from './client';
import { readSummary, type SummaryList, type SummaryRequestList, type SummarySchema, type SummaryView } from './summaries';

export function useSummaryResource<T extends SummaryList | SummaryRequestList | SummaryView>(path: string | null, owner: string,
  patientId: string | null, schema: SummarySchema, summaryId?: string) {
  const [resource, setResource] = useState<{ path: string | null; owner: string; data: T | null; error: Error | null; loading: boolean }>(
    { path, owner, data: null, error: null, loading: !!path });
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => {
    setResource({ path, owner, data: null, error: null, loading: !!path });
    setVersion(value => value + 1);
  }, [path, owner]);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ path, owner, data: null, error: null, loading: !!path });
    const visibility = () => { controller.abort(); reload(); };
    document.addEventListener('visibilitychange', visibility);
    if (path && patientId && document.visibilityState === 'visible') void (summaryId
      ? readSummary(patientId, summaryId, controller.signal)
      : fetchJson<unknown>(path, { signal: controller.signal })).then(async value => {
      const { validateSummary } = await import('./summarySchema');
      const data = validateSummary<T>(schema, value);
      if (data.patient_id !== patientId) throw new Error('摘要返回资料与当前患儿不一致。');
      if ('summaries' in data && data.summaries.some(item => item.patient_id !== patientId)) throw new Error('摘要目录包含其他患儿资料。');
      if ('requests' in data && data.requests.some(item => item.patient_id !== patientId)) throw new Error('摘要请求目录包含其他患儿资料。');
      if (!controller.signal.aborted) setResource({ path, owner, data, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setResource({ path, owner, data: null, error: failure, loading: false });
    });
    return () => { controller.abort(); document.removeEventListener('visibilitychange', visibility); };
  }, [path, owner, patientId, schema, summaryId, version, reload]);
  const current = resource.path === path && resource.owner === owner;
  return { data: current ? resource.data : null, error: current ? resource.error : null, loading: current ? resource.loading : !!path, reload };
}
