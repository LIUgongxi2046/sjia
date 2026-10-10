import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { ArrowRight, ChevronDown, ChevronRight, Search, ShieldCheck } from 'lucide-react';
import { CalendarDays, FileClock, FileText, Layers, UsersRound } from '../../components/ThreeDIcon';
import type { ArchiveSearchInput, ArchiveSearchItem } from '../../../../frontend-design/archive-search-types';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { useAccess } from '../../state/access';
import { defaultArchiveFilters, navigationMemory, type ArchiveFilters } from '../../state/navigation';
import { useArchiveSearch } from '../../api/useArchiveSearch';
import { originLabels } from '../../api/records';
import { documentTimeLabel } from './documentTimeLabel';
import { documentLabels } from './documentLabels';
const categories = [['all', '全部'], ['inpatient', '住院'], ['outpatient', '门诊'], ['pending', '待归组']] as const;
const verificationLabels = { all: '全部状态', self: '本人确认', pending: '待专业核对', verified: '已专业核对', unverified: '未核对', needs_correction: '需补正', revoked: '已撤销' };

function queryFor(filters: ArchiveFilters): ArchiveSearchInput {
  const values: ArchiveSearchInput = { limit: 20, category: filters.category };
  if (filters.query.trim()) values.query = filters.query.trim();
  if (filters.organization.trim()) values.organization = filters.organization.trim();
  if (filters.project.trim()) values.project = filters.project.trim();
  if (filters.documentType) values.document_type = filters.documentType as ArchiveSearchInput['document_type'];
  if (filters.episode) values.episode_id = filters.episode;
  if (filters.start) values.from_date = filters.start;
  if (filters.end) values.to_date = filters.end;
  if (filters.source !== 'all') values.origin_type = filters.source;
  if (filters.verification !== 'all') values.verification_status = filters.verification;
  if (filters.unknownTime) values.include_unknown_time = true;
  if (filters.unknownOrganization) values.unknown_organization = true;
  if (filters.unknownEpisode) values.unknown_episode = true;
  return values;
}

function DocumentRow({ item }: { item: ArchiveSearchItem }) {
  const value = item.document;
  return <section className="card form-card" aria-label={`${documentLabels[value.document_type]}文书`}>
    <ContextLink className="list-row" to={`/archives/source?document=${encodeURIComponent(value.document_id)}`}>
      <span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>{documentLabels[value.document_type]}</strong><small>{value.organization ?? '来源医院未知'} · 文书版本 {value.version}</small></span><ChevronRight size={17} />
    </ContextLink>
    <div className="pair"><span>文书形成时间</span><strong>{documentTimeLabel(value.documented_time)}</strong></div>
    <div className="pair"><span>就诊归属</span><strong>{item.episodes.map(episode => episode.label).join('；') || '就诊待归组'}</strong></div>
    <div className="pair"><span>核对状态</span><strong>{verificationLabels[item.verification_status]}</strong></div>
    <p>原件：{item.pages.length} 页；{item.pages.every(page => page.status === 'available') ? '当前可读取' : '部分原件暂不可读取'}；{value.grouping_status === 'confirmed' ? '已确认归组' : value.grouping_status === 'needs_correction' ? '归组需补正' : '待确认归组'}。</p>
    {item.snippet && <p aria-label="已确认原文片段">{item.snippet.leading_truncated ? '…' : ''}{item.snippet.leading_text}<mark>{item.snippet.matched_text}</mark>{item.snippet.trailing_text}{item.snippet.trailing_truncated ? '…' : ''}</p>}
    <ContextLink className="text-action" to={`/archives/source?document=${encodeURIComponent(value.document_id)}`}>定位原文依据<ArrowRight size={16} /></ContextLink>
    <ContextLink className="text-action" to={`/archives/grouping?document=${encodeURIComponent(value.document_id)}`}>查看文书归组<ArrowRight size={16} /></ContextLink>
  </section>;
}

export function UI2ArchivePage() {
  const access = useAccess(), navigate = useNavigate();
  const owner = access.principalId && access.patientId ? JSON.stringify([access.principalId, access.patientId]) : null;
  return <ArchiveContent key={owner} owner={owner} navigate={navigate} />;
}

function ArchiveContent({ owner, navigate }: { owner: string | null; navigate: ReturnType<typeof useNavigate> }) {
  const access = useAccess();
  const remembered = navigationMemory.archives.owner === owner ? navigationMemory.archives : null;
  const [filters, setFilters] = useState<ArchiveFilters>(remembered?.filters ?? defaultArchiveFilters());
  const [applied, setApplied] = useState<ArchiveFilters>(remembered?.applied ?? defaultArchiveFilters());
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const ready = !!owner && !access.loading && !access.error;
  const resource = useArchiveSearch(ready ? access.patientId : null, ready ? access.principalId : null, queryFor(applied));
  const dateError = !!filters.start && !!filters.end && filters.start > filters.end;
  useEffect(() => { navigationMemory.archives = { owner, filters, applied }; }, [owner, filters, applied]);
  function change<K extends keyof ArchiveFilters>(key: K, value: ArchiveFilters[K]) { setFilters(current => ({ ...current, [key]: value })); }
  function apply(value = filters) { if (!value.start || !value.end || value.start <= value.end) setApplied(value); }
  function clear() { const value = defaultArchiveFilters(); value.open = filters.open; setFilters(value); apply(value); }
  const data = ready ? resource.data : null;
  const filtered = Object.entries(queryFor(applied)).some(([key, value]) => key !== 'limit' && !(key === 'category' && value === 'all'));
  return <UI2FamilyFrame title="长期档案" screenId="UI2-25" note="按医院、就诊和文书查找，随时回到原文。" backTo="/records">
    <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
    <div className="feature-grid"><button onClick={() => navigate('/archives/batches')}><Layers size={34} /><strong>添加一批资料</strong><small>原始资料直接整理</small></button><button onClick={() => navigate('/summaries')}><FileText size={34} /><strong>就医摘要</strong><small>准备本次沟通资料</small></button></div>
    <div className="section-heading"><h2>按就诊查找</h2><button className="text-action" aria-expanded={filters.open} onClick={() => { apply(); change('open', !filters.open); }}>筛选</button></div>
    <label className="search-field"><Search size={18} /><input type="search" aria-label="查找文书" placeholder="医院、文书名称或已确认原文" autoComplete="off" maxLength={200} value={filters.query} onChange={event => change('query', event.target.value)} onBlur={() => apply()} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); apply(); } }} /></label>
    <div className="segments" aria-label="就诊类别">{categories.map(([key, label]) => <button key={key} aria-pressed={filters.category === key} className={filters.category === key ? 'active' : ''} onClick={() => { const value = { ...filters, category: key }; setFilters(value); apply(value); }}>{label}</button>)}</div>
    {filters.open && <section className="card form-card" aria-label="筛选文书" onBlur={event => { if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) apply(); }} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && event.target instanceof HTMLInputElement) { event.preventDefault(); apply(); } }}>
      <div className="field-grid"><label className="field"><span>开始日期</span><input type="date" value={filters.start} onChange={event => change('start', event.target.value)} /></label><label className="field"><span>结束日期</span><input type="date" value={filters.end} aria-invalid={dateError} onChange={event => change('end', event.target.value)} /></label></div>
      {dateError && <p role="alert">结束日期不能早于开始日期。</p>}
      <label className="field"><span>来源医院</span><input placeholder="输入医院名称" list="archive-organizations" autoComplete="off" maxLength={200} value={filters.organization} onChange={event => change('organization', event.target.value)} /></label>
      <datalist id="archive-organizations">{data?.facets.organizations.map(value => <option key={value} value={value} />)}</datalist>
      <label className="field"><span>核对状态</span><select value={filters.verification} onChange={event => change('verification', event.target.value as ArchiveFilters['verification'])}>{Object.entries(verificationLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <details><summary>更多筛选条件</summary>
        <label className="field"><span>文书类型</span><select value={filters.documentType} onChange={event => change('documentType', event.target.value)}><option value="">全部文书</option>{Object.entries(documentLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="field"><span>就诊</span><select value={filters.episode} onChange={event => change('episode', event.target.value)}><option value="">全部就诊</option>{data?.facets.episodes.map(value => <option key={value.episode_id} value={value.episode_id}>{value.label}</option>)}</select></label>
        <label className="field"><span>检验项目名称</span><input type="search" autoComplete="off" maxLength={200} value={filters.project} onChange={event => change('project', event.target.value)} /></label>
        <label className="field"><span>资料来源</span><select value={filters.source} onChange={event => change('source', event.target.value as ArchiveFilters['source'])}><option value="all">全部来源</option>{Object.entries(originLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="check-line"><input type="checkbox" checked={filters.unknownTime} onChange={event => change('unknownTime', event.target.checked)} />日期筛选同时包含时间未知资料</label>
        <label className="check-line"><input type="checkbox" checked={filters.unknownOrganization} onChange={event => change('unknownOrganization', event.target.checked)} />仅查找来源医院未知资料</label>
        <label className="check-line"><input type="checkbox" checked={filters.unknownEpisode} onChange={event => change('unknownEpisode', event.target.checked)} />仅查找就诊未知资料</label>
      </details><button className="button secondary" onClick={clear}>清除筛选</button>
    </section>}
    <UI2ReadState loading={access.loading || resource.loading && !data} error={resource.error} onRetry={() => { void access.refresh(); resource.reload(); }} />
    {data && <>
      <p role="status">符合条件 {data.matched_count} 份文书；本次读取 {data.items.length} 份。</p>
      {!data.items.length ? <section className="card"><div className="empty"><span className="empty-icon"><Layers size={54} /></span><h3>{filtered ? '没有符合筛选条件的文书' : '让资料有序可查'}</h3><p>保留原始文书，逐项整理时间和来源。</p><button className="text-action" onClick={() => navigate('/register/reports')}>添加已有资料<ArrowRight size={16} /></button></div></section> : data.items.map(item => <DocumentRow key={item.document.document_id} item={item} />)}
      {data.next_cursor && <button className="button secondary" disabled={resource.loading} onClick={resource.more}>{resource.loading ? '正在读取后续文书' : '读取更多文书'}</button>}
    </>}
    <section className="card"><ContextLink className="list-row" to="/archives/grouping"><span className="row-icon"><CalendarDays size={30} /></span><span className="row-text"><strong>查看待归组资料</strong><small>时间或就诊未确定的资料也能查找</small></span><ChevronRight size={17} /></ContextLink><ContextLink className="list-row" to="/drafts"><span className="row-icon"><FileClock size={30} /></span><span className="row-text"><strong>我的草稿</strong><small>继续本人尚未完成的记录</small></span><ChevronRight size={17} /></ContextLink></section>
    <div className="note blue"><ShieldCheck size={17} /><p>没有新增资料，不代表当前健康状况正常。</p></div>
  </UI2FamilyFrame>;
}
