import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight, ShieldCheck } from '../../components/ThreeDIcon';
import { FileText, History, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { useRecordResource } from '../../api/useRecordResource';
import type { RecordHistoryView } from '../../api/endpoints';
import { eventTimeLabel } from '../../components/recordTimePresentation';
import { HistoryVersionDisclosure } from './HistoryVersionDisclosure';
import { originLabels } from '../../api/records';

export function UI2HistoryPage() {
  const access = useAccess();
  const navigate = useNavigate();
  const { recordId } = useParams();
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const resource = useRecordResource<RecordHistoryView>(canRead && recordId ? `/api/records/${recordId}/history` : null,
    access.patientId, 'RecordHistoryView', recordId ?? null);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [expanded, setExpanded] = useState<{ binding: string; id: string; version: string } | null>(null);
  const binding = JSON.stringify([access.principalId, access.patientId, access.scopeVersion, recordId]);
  const candidate = canRead && visible && !resource.loading && !resource.error ? resource.data : null;
  const invalidHistory = !!candidate && (candidate.record_id !== recordId || candidate.patient_id !== access.patientId
    || new Set(candidate.versions.map(value => value.record_version_id)).size !== candidate.versions.length);
  const data = invalidHistory ? null : candidate;
  useEffect(() => {
    const changed = () => {
      setVisible(document.visibilityState === 'visible');
      if (document.visibilityState !== 'visible') setExpanded(null);
    };
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => { setExpanded(null); }, [binding, canRead, resource.loading, resource.error, visible, invalidHistory]);
  const expandedId = data && expanded?.binding === binding && data.versions.some(value => value.record_version_id === expanded.id && value.version === expanded.version) ? expanded.id : null;
  useEffect(() => {
    if (expanded && !expandedId) setExpanded(null);
  }, [expanded, expandedId]);
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const versions = data ? [...data.versions].sort((first, second) => BigInt(first.version) < BigInt(second.version) ? 1 : BigInt(first.version) > BigInt(second.version) ? -1 : 0) : [];
  const historical = versions.filter(value => value.version !== data?.current_version);
  return <UI2FamilyFrame title="更正历史" screenId="UI2-09" note="保留原始版本及更正关系。" backTo={recordId ? `/records/${recordId}` : '/records'}>
    {canRead && <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>}
    <UI2ReadState loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    {invalidHistory && <p role="alert">历史列表与当前记录不一致或包含重复版本，本次内容未被采用。</p>}
    {data && versions.length === 0 && <section className="card"><div className="empty"><span className="empty-icon"><History size={54} /></span><h3>尚无版本记录</h3><p>更正会保留原始内容、原因和核对变化。</p></div></section>}
    {data && versions.map(value => <section className="card form-card" key={value.record_version_id}>
      <div className="section-heading"><h2>版本 {value.version}</h2><span className="badge neutral">{value.version === data.current_version ? '当前版本' : '历史只读内容'}</span></div>
      <div className="pair"><span>登记时间</span><strong>{eventTimeLabel(value.recorded_at)}</strong></div>
      <div className="pair"><span>资料来源</span><strong>{originLabels[value.origin_type]}</strong></div>
      <div className="pair"><span>格式质量</span><strong>{({ pending: '待检查', valid: '格式检查通过', needs_correction: '格式需要补正' } as Record<string, string>)[value.quality_status]}</strong></div>
      <button type="button" className="text-action" aria-expanded={expandedId === value.record_version_id}
        aria-controls={`history-details-${value.record_version_id}`}
        onClick={() => setExpanded(expandedId === value.record_version_id ? null : { binding, id: value.record_version_id, version: value.version })}>
        {expandedId === value.record_version_id ? '收起' : '展开'}版本 {value.version} 详情
      </button>
      {expandedId === value.record_version_id && <HistoryVersionDisclosure key={`${binding}:${value.record_version_id}:${value.version}`} recordId={data.record_id} versionId={value.record_version_id} version={value.version} />}
      <ContextLink className="list-row" to={`/records/versions/${value.record_version_id}`}><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>查看版本 {value.version} 完整内容</strong><small>更正原因、原始资料和逐项核对状态</small></span><ChevronRight size={17} /></ContextLink>
    </section>)}
    {data ? <ContextLink className="button secondary" to={`/records/${data.record_id}`}>返回当前记录</ContextLink>
      : <button className="button secondary" disabled>返回当前记录</button>}
    <section className="card">
      <button className="list-row" disabled={!historical.length} onClick={() => navigate(`/records/versions/${historical[0].record_version_id}`)}><span className="row-text"><strong>查看历史版本</strong></span><ChevronRight size={17} /></button>
    </section>
    {data && <details className="card form-card"><summary>查看版本信息</summary><div className="pair"><span>版本数量</span><strong>{versions.length}</strong></div><div className="pair"><span>当前版本</span><strong>{data.current_version}</strong></div><p>历史版本只读，专业核对状态见版本的各项字段。</p></details>}
    <div className="note blue"><ShieldCheck size={17} /><p>历史记录仍按当前权限读取。</p></div>
  </UI2FamilyFrame>;
}
