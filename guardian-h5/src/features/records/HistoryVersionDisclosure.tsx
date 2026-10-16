import { useEffect, useState } from 'react';
import { readRecordVersion } from '../../api/endpoints';
import { fieldLabel, verificationLabel, type RecordView } from '../../api/records';
import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useAccess } from '../../state/access';
import { useEndpointAccess } from '../../state/useEndpointAccess';

/** Mounted only for an explicit expansion. No prefetch or retained version cache. */
export function HistoryVersionDisclosure({ recordId, versionId, version }: { recordId: string; versionId: string; version: string }) {
  const access = useAccess();
  const endpoint = useEndpointAccess(JSON.stringify([access.principalId, access.patientId, access.scopeVersion, recordId, versionId, version]));
  const [result, setResult] = useState<{ data: RecordView | null; error: string | null }>({ data: null, error: null });
  useEffect(() => {
    const controller = new AbortController();
    let current = true;
    const clear = () => {
      current = false;
      controller.abort();
      setResult({ data: null, error: null });
    };
    const visibility = () => { if (document.visibilityState !== 'visible') clear(); };
    document.addEventListener('visibilitychange', visibility);
    if (endpoint.current(controller.signal) && !access.loading && !access.error) {
      void readRecordVersion(versionId, controller.signal).then(data => {
        if (!current || !endpoint.current(controller.signal)) return;
        if (data.patient_id !== access.patientId || data.record_id !== recordId || data.record_version_id !== versionId || String(data.version) !== version) {
          throw new Error('返回对象与所选版本不一致，本次内容未被采用。');
        }
        setResult({ data, error: null });
      }).catch(failure => {
        if (!current || !endpoint.current(controller.signal)) return;
        const denied = endpoint.deny(failure);
        setResult({ data: null, error: denied ? '当前权限无法读取此版本，请重新核验访问权限。' : '版本详情读取失败，本次内容未被采用。可收起后重新展开。' });
      });
    }
    return () => { current = false; controller.abort(); document.removeEventListener('visibilitychange', visibility); };
    // The owner keys this component by principal/patient/scope/record/version and
    // removes it on loading/error/hide. Each mount is a new request generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const data = !access.loading && !access.error && endpoint.current() ? result.data : null;
  return <div id={`history-details-${versionId}`} className="history-version-details" aria-label={`版本 ${version} 详情`}>
    {result.error ? <p role="alert">{result.error}</p> : !data ? <p role="status">正在读取版本详情</p> : <>
      <div className="pair"><span>更正原因</span><strong>{data.correction_reason?.trim() ? data.correction_reason : '未提供更正原因'}</strong></div>
      <div className="pair"><span>平台登记时间</span><strong>{eventTimeLabel(data.recorded_at)}</strong></div>
      <div className="pair"><span>平台更正时间</span><strong>{data.corrected_at ? eventTimeLabel(data.corrected_at) : '未提供更正时间'}</strong></div>
      <p>操作者信息尚未由数据契约提供。</p>
      <h3>获准字段的核对状态</h3>
      {data.fields.length ? data.fields.map(field => <div className="pair" key={field.field_id}><span>{fieldLabel(field)}</span><strong>{verificationLabel(field)}</strong></div>) : <p>当前版本未返回可展示的字段核对状态。</p>}
      <p>仅显示本次获准字段的状态，不表示全部字段，也不推断版本间的核对变化。</p>
    </>}
  </div>;
}
