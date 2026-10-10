import { useCallback, useEffect, useState } from 'react';
import { ApiError, fetchJson } from './client';
import { useAccess } from '../state/access';
import type { RecordView } from './records';
import type { OriginalSourceView } from './originalSourceSchema';

export function useOriginalSource(record: RecordView | null) {
  const access = useAccess();
  const key = record && access.principalId && access.patientId && !access.loading && !access.error
    ? JSON.stringify([access.principalId, access.patientId, record.record_id, record.record_version_id, record.version]) : null;
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [resource, setResource] = useState<{ key: string | null; data: OriginalSourceView | null; error: Error | null; loading: boolean }>({ key, data: null, error: null, loading: !!key });
  const [attempt, setAttempt] = useState(0);
  const reload = useCallback(() => { setResource({ key, data: null, error: null, loading: !!key }); setAttempt(value => value + 1); }, [key]);
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ key, data: null, error: null, loading: !!key && visible });
    if (key && record && visible) void fetchJson<unknown>(`/api/record-versions/${record.record_version_id}/originals`, { signal: controller.signal }).then(async value => {
      const { validateOriginalSource } = await import('./originalSourceSchema');
      const data = validateOriginalSource(value);
      if (data.patient_id !== access.patientId || data.record_id !== record.record_id || data.record_version_id !== record.record_version_id || data.record_version !== record.version
        || data.originals.length !== record.attachments.length || data.originals.some((file, index) => file.file_id !== record.attachments[index].file_id || file.ordinal !== record.attachments[index].ordinal)) {
        throw new Error('原件元数据与当前患儿、记录版本或附图顺序不一致。');
      }
      if (!controller.signal.aborted) setResource({ key, data, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) {
        setResource({ key, data: null, error: failure, loading: false });
        if (failure instanceof ApiError && failure.status === 401) void access.refresh();
      }
    });
    return () => controller.abort();
  }, [key, visible, attempt, access.refresh]);
  const current = visible && resource.key === key;
  return { data: current ? resource.data : null, error: current ? resource.error : null, loading: visible && (current ? resource.loading : !!key), reload };
}
