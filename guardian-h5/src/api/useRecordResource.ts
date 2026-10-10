import { useCallback, useEffect, useState } from 'react';
import { fetchJson } from './client';
import type { RecordView } from './records';
import type { RecordHistoryView } from './endpoints';

export function useRecordResource<T extends RecordView | RecordHistoryView>(path: string | null, patientId: string | null, schema: 'RecordView' | 'RecordHistoryView', expectedId: string | null, identityField: 'record_id' | 'record_version_id' = 'record_id') {
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [resource, setResource] = useState<{ path: string | null; patientId: string | null; data: T | null; error: Error | null; loading: boolean }>(
    { path, patientId, data: null, error: null, loading: !!path });
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => {
    setResource({ path, patientId, data: null, error: null, loading: !!path });
    setVersion(value => value + 1);
  }, [path, patientId]);
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ path, patientId, data: null, error: null, loading: !!path });
    if (path && visible) void fetchJson<unknown>(path, { signal: controller.signal }).then(async value => {
      const { validateRecordRead } = await import('./recordSearchSchema');
      const data = validateRecordRead<T>(schema, value);
      if (data.patient_id !== patientId) throw new Error('记录所属患儿与当前选择不一致，请核验访问关系并重新选择。');
      const actualId = identityField === 'record_id' ? data.record_id : 'record_version_id' in data ? data.record_version_id : null;
      if (actualId !== expectedId) throw new Error('返回对象与当前记录请求不一致，本次内容未被采用。');
      if (!controller.signal.aborted) setResource({ path, patientId, data, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setResource({ path, patientId, data: null, error: failure, loading: false });
    });
    return () => controller.abort();
  }, [path, patientId, schema, version, expectedId, identityField, visible]);
  const current = visible && resource.path === path && resource.patientId === patientId;
  return { data: current ? resource.data : null, error: current ? resource.error : null, loading: visible && (current ? resource.loading : !!path), reload };
}
