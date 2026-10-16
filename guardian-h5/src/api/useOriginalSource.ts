import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, fetchJson } from './client';
import { useAccess } from '../state/access';
import type { RecordView } from './records';
import type { OriginalSourceView } from './originalSourceSchema';

export function useOriginalSource(record: RecordView | null) {
  const access = useAccess();
  // Keep identity stable during same-scope refreshes: denial requires an explicit retry.
  const key = record && access.principalId && access.patientId
    ? JSON.stringify([access.principalId, access.patientId, access.scopeVersion, record.patient_id, record.record_id, record.record_version_id, record.version, record.attachments]) : null;
  const currentKey = useRef(key); currentKey.current = key;
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const ready = !!key && visible && !access.loading && !access.error && record?.patient_id === access.patientId
    && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  const [resource, setResource] = useState<{ key: string | null; data: OriginalSourceView | null; error: Error | null; loading: boolean }>({ key, data: null, error: null, loading: ready });
  const denied = useRef<{ key: string; error: Error } | null>(null), retrying = useRef(false), mounted = useRef(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const reload = useCallback(async () => {
    if (retrying.current || !ready) return;
    retrying.current = true;
    try {
      if (denied.current?.key === key) await access.refresh();
      // Parents clear their record while revalidating; the captured denied key may
      // resume only within this unchanged access scope, never for another record.
      if (!mounted.current || currentKey.current !== null && currentKey.current !== key
        || !access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion)) return;
      denied.current = null;
      setResource({ key, data: null, error: null, loading: true }); setAttempt(value => value + 1);
    } finally { retrying.current = false; }
  }, [key, ready, access.refresh, access.hasCurrentAccess, access.principalId, access.patientId, access.scopeVersion]);
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const isCurrent = () => !controller.signal.aborted && currentKey.current === key && document.visibilityState === 'visible'
      && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
    const denial = denied.current?.key === key ? denied.current : null;
    setResource({ key, data: null, error: denial?.error ?? null, loading: ready && !denial });
    if (ready && key && record && !denial && isCurrent()) void fetchJson<unknown>(`/api/record-versions/${record.record_version_id}/originals`, { signal: controller.signal, isCurrent }).then(async value => {
      if (!isCurrent()) return;
      const { validateOriginalSource } = await import('./originalSourceSchema');
      if (!isCurrent()) return;
      const data = validateOriginalSource(value);
      if (data.patient_id !== access.patientId || data.record_id !== record.record_id || data.record_version_id !== record.record_version_id || data.record_version !== record.version
        || data.originals.length !== record.attachments.length || data.originals.some((file, index) => file.file_id !== record.attachments[index].file_id || file.ordinal !== record.attachments[index].ordinal)) {
        throw new Error('原件元数据与当前患儿、记录版本或附图顺序不一致。');
      }
      if (isCurrent()) setResource({ key, data, error: null, loading: false });
    }).then(undefined, failure => {
      if (!isCurrent()) return;
      if (!(failure instanceof Error)) throw failure;
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) denied.current = { key, error: failure };
      setResource({ key, data: null, error: failure, loading: false });
    });
    return () => controller.abort();
  }, [key, ready, attempt, access.hasCurrentAccess]);
  const current = ready && resource.key === key;
  return { data: current ? resource.data : null, error: current ? resource.error : null, loading: ready && (current ? resource.loading : true), reload };
}
