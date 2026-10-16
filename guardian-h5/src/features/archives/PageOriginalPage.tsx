import { eventTimeLabel } from '../../components/recordTimePresentation';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowDown as ChevronDown, ChevronRight } from '../../components/ThreeDIcon';
import { Image, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { PrivateSourceGallery } from '../../components/PrivateSourceGallery';
import { PrivateDocumentSource } from '../../components/PrivateDocumentSource';
import { useAccess } from '../../state/access';
import { useEndpointAccess } from '../../state/useEndpointAccess';
import { readCandidates, readPageOriginal } from '../../api/archiveFacts';
import type { FactCandidateSet, PageOriginalView } from '../../../../frontend-design/v2-types';

const labels = ['文书类型', '来源医院', '原件版本', '文书形成时间', '平台取得时间', '允许用途', '副本复核状态'];
export function PageOriginalPage({ pageId }: { pageId: string }) {
  const access = useAccess(), navigate = useNavigate(), boundary = useEndpointAccess(pageId);
  const [source, setSource] = useState<PageOriginalView | null>(null), [candidate, setCandidate] = useState<FactCandidateSet | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState<Error | null>(null), [version, setVersion] = useState(0);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const ready = !!access.principalId && !!access.patientId && !access.loading && !access.error && visible;
  useEffect(() => {
    const changed = () => { const shown = document.visibilityState === 'visible'; setVisible(shown); if (!shown) { setSource(null); setCandidate(null); } else { void access.refresh(); setVersion(value => value + 1); } };
    document.addEventListener('visibilitychange', changed); return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    if (boundary.blocked.current) return;
    const controller = new AbortController(); setSource(null); setCandidate(null); setError(null); setLoading(ready);
    if (ready) void readPageOriginal(pageId, access.patientId!, controller.signal).then(async original => {
      boundary.guard(controller.signal);
      const value = original.page.candidate_set_id ? await readCandidates(original.page, controller.signal) : null;
      if (boundary.current(controller.signal)) { setSource(original); setCandidate(value); setLoading(false); }
    }).then(undefined, value => {
      if (!(value instanceof Error)) throw value;
      if (boundary.current(controller.signal)) { setSource(null); setCandidate(null); setError(value); setLoading(false); boundary.deny(value); }
    });
    return () => controller.abort();
  }, [ready, access.principalId, access.patientId, access.scopeVersion, pageId, version]);
  const metadata = candidate?.extraction;
  const values = source ? [metadata ? `${metadata.document_type}（提取候选，文书归组分别确认）` : '文书分类待确认',
    metadata?.organization ?? '来源医院未知', source.page.file_object_version,
    metadata?.documented_time_text ? `${metadata.documented_time_text}（原始文字候选）` : '文书形成时间未知',
    eventTimeLabel(source.created_at), source.use_authorizations.map(value => `${value.purpose}（${value.environment}）`).join('；') || '尚无当前用途授权',
    source.purpose_copies.map(value => `${value.purpose}：${value.status}；${value.review_id ? value.revoked_at ? '复核已撤销' : '已有复核记录' : '复核待完成'}`).join('；') || '尚无副本复核记录'] : labels.map(() => '待读取');
  return <UI2FamilyFrame title="原始资料" screenId="UI2-33" note="显示来源版本、用途与文书页面。" backTo={source ? `/archives/batches/${source.page.batch_id}` : '/archives/batches'}>
    <button className="patient rich-record-patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <UI2ReadState loading={loading || access.loading} error={error} onRetry={() => boundary.revalidate(() => { setError(null); setVersion(value => value + 1); })} />
    <section className="card source-view"><Image size={58} /><h2>{source ? '已保存原始文书' : '尚未选择原始文书'}</h2><p>完整原件与提取内容共同保留，支持按页面查看。</p>
      {source ? ['image/png', 'image/jpeg', 'image/webp'].includes(source.file.media_type) ? <PrivateSourceGallery files={[{ file_id: source.file.file_id, checksum: source.file.checksum, ordinal: source.ordinal, content_path: source.file.content_path }]} triggerClassName="button secondary" triggerContent="查看原件" /> : <PrivateDocumentSource file={source.file} /> : <button className="button secondary" disabled={!ready} onClick={() => navigate('/register/reports')}>选择照片</button>}
    </section>
    <section className="card form-card">{labels.map((label, index) => <div className="pair" key={label}><span>{label}</span><strong style={{ overflowWrap: 'anywhere' }}>{values[index]}</strong></div>)}</section>
    <section className="card"><ContextLink className="list-row" to={`/archives/confirm?page=${pageId}${source ? `&batch=${source.page.batch_id}` : ''}`}><span className="row-text"><strong>逐项内容与时间</strong></span><ChevronRight size={17} /></ContextLink><ContextLink className="list-row" to={`/archives/grouping?page=${pageId}${source ? `&batch=${source.page.batch_id}` : ''}`}><span className="row-text"><strong>查看文书归组</strong></span><ChevronRight size={17} /></ContextLink></section>
  </UI2FamilyFrame>;
}
