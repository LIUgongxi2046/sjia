import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { searchRecords } from '../../api/endpoints';
import type { RecordItem } from '../../api/records';
import { UI2ReadState } from '../../components/UI2ReadState';
import { useAccess } from '../../state/access';
import { ArchivePanel } from './ArchivePanel';
import { recordSnapshotPath } from './observationSelection';

/** One permission-bound read sequence; rejected/stale pages never become partial totals. */
export function HealthArchive() {
  const access = useAccess(), navigate = useNavigate();
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const owner = canRead ? `${access.principalId}:${access.patientId}:${access.scopeVersion}` : null;
  const [resource, setResource] = useState<{ owner: string | null; records: RecordItem[] | null; error: Error | null }>({ owner: null, records: null, error: null });
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ owner, records: null, error: null });
    if (!owner || !access.patientId) return () => controller.abort();
    const principal = access.principalId, patient = access.patientId, scope = access.scopeVersion;
    void (async () => {
      const records: RecordItem[] = [];
      const seen = new Set<string>();
      let cursor: string | null = null;
      do {
        const page = await searchRecords(patient, { limit: 100, cursor }, { signal: controller.signal });
        if (controller.signal.aborted || !access.hasCurrentAccess(principal, patient, scope)) return;
        records.push(...page.records); cursor = page.next_cursor;
        if (cursor && seen.has(cursor)) throw new Error('档案分页未完成，请重新读取。');
        if (cursor) seen.add(cursor);
      } while (cursor);
      if (!controller.signal.aborted && access.hasCurrentAccess(principal, patient, scope)) setResource({ owner, records, error: null });
    })().catch(failure => { if (!controller.signal.aborted) setResource({ owner, records: null, error: failure instanceof Error ? failure : new Error('档案读取未完成。') }); });
    return () => controller.abort();
  }, [owner, reload, access.principalId, access.patientId, access.scopeVersion, access.hasCurrentAccess]);
  const current = owner !== null && owner === resource.owner;
  const records = current ? resource.records : null, error = current ? resource.error : null;
  const loading = canRead && !records && !error;
  const active = access.relations.find(relation => relation.patient_id === access.patientId);
  const status = !canRead ? '需要核验权限' : error ? '读取失败' : loading ? '正在读取' : null;
  return <ArchivePanel records={records} status={status} patientLabel={canRead ? active?.display_id ?? '当前患儿' : '孩子的记录'}
    badgeLabel={canRead ? '按当前权限读取' : '访问待核验'} canWrite={canRead && !!active?.actions.includes('write_record')}
    readState={<UI2ReadState loading={loading} error={error} onRetry={() => setReload(value => value + 1)} />}
    onViewRecord={({ record, field }) => {
      if (!records || !access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion)) return;
      navigate(recordSnapshotPath({ recordId: record.record_id, recordVersionId: record.detail.record_version_id, fieldId: field.field_id }));
    }} />;
}
