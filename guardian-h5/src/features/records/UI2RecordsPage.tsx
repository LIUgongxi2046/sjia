import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router';
import { ArrowRight, ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react';
import { CalendarDays, ClipboardList, FileClock, FileText, Layers, Pill, Thermometer, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { searchRecords } from '../../api/endpoints';
import { fieldLabel, fieldValue, originLabels, timeLabel, verificationLabel } from '../../api/records';
import type { RecordItem } from '../../api/records';
import { useAccess } from '../../state/access';
import { defaultRecordFilters, navigationMemory } from '../../state/navigation';
import type { RecordFilters } from '../../state/navigation';

const categories = ['全部', '体温症状', '检验报告', '治疗资料'] as const;
const labels: Record<string, string> = { observation: '体温症状', report: '检验报告', therapy: '治疗资料', professional: '获准临床资料' };

function RecordRow({ record }: { record: RecordItem }) {
  const detail = record.detail;
  const primary = detail.fields.find(field => field.field_code === (record.kind === 'therapy' ? 'therapy_status' : 'temp'));
  const fields = detail.fields.filter(field => field !== primary && field.value_state === 'known');
  const medicalTime = detail.report?.sample_time ?? primary?.medical_time ?? null;
  const timeTitle = detail.report?.sample_time ? '报告采样时间' : primary ? `${fieldLabel(primary)}时间` : '项目时间';
  const Icon = record.kind === 'observation' ? Thermometer : record.kind === 'therapy' ? Pill : FileText;
  return <section className="card form-card">
    <ContextLink className="list-row" to={`/records/${record.record_id}`}><span className="row-icon"><Icon size={30} /></span>
      <span className="row-text"><strong>{labels[record.kind]}</strong><small>{primary ? fieldValue(primary) : record.kind === 'report' ? `${detail.fields.length} 项结果` : '观察记录'}</small></span><ChevronRight size={17} />
    </ContextLink>
    <p>{fields.slice(0, 3).map(field => `${fieldLabel(field)}：${fieldValue(field)}`).join(' · ') || '查看原始记录'}{fields.length > 3 ? ` · 另有 ${fields.length - 3} 项` : ''}</p>
    <div className="pair"><span>{timeTitle}</span><strong>{medicalTime ? timeLabel(medicalTime) : '各项目时间见完整记录'}</strong></div>
    <div className="pair"><span>资料来源</span><strong>{originLabels[detail.origin_type]}{detail.source_organization ? ` · ${detail.source_organization}` : ''}</strong></div>
    <div className="pair"><span>核对状态</span><strong>{primary ? verificationLabel(primary) : '核对状态见详情'}</strong></div>
  </section>;
}

function queryFor(filters: RecordFilters): Record<string, unknown> {
  return { limit: 20,
    kind: ({ '全部': undefined, '体温症状': 'observation', '检验报告': 'report', '治疗资料': 'therapy' } as const)[filters.category],
    original_name: filters.project || undefined, source_organization: filters.organization || undefined,
    origin_type: filters.source === 'all' ? undefined : filters.source,
    verification_status: ['all', 'self'].includes(filters.verification) ? undefined : filters.verification,
    confirmed_by_self: filters.verification === 'self', from_date: filters.start || undefined, to_date: filters.end || undefined };
}

export function UI2RecordsPage() {
  const access = useAccess();
  const location = useLocation();
  const navigate = useNavigate();
  const owner = `${access.principalId ?? 'anonymous'}:${access.patientId ?? 'none'}`;
  const [filters, setFilters] = useState<RecordFilters>(() => {
    const current = navigationMemory.records;
    const value = current.owner === owner ? { ...current.filters } : defaultRecordFilters();
    const category = new URLSearchParams(location.search).get('category');
    if (category && categories.includes(category as RecordFilters['category'])) value.category = category as RecordFilters['category'];
    navigationMemory.records = { owner, filters: value };
    return value;
  });
  const [query, setQuery] = useState(() => queryFor(filters));
  const [appliedFilters, setAppliedFilters] = useState(filters);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  const more = useRef<AbortController | null>(null);
  const canRead = !!access.principalId && !!access.patientId && !access.error && !access.loading;
  const dateError = !!filters.start && !!filters.end && filters.start > filters.end;
  const active = access.relations.find(relation => relation.patient_id === access.patientId);

  useLayoutEffect(() => {
    if (location.search) navigate('/records', { replace: true });
  }, [location.search, navigate]);
  function change<K extends keyof RecordFilters>(key: K, value: RecordFilters[K], apply = false) {
    const next = { ...filters, [key]: value };
    navigationMemory.records = { owner, filters: next }; setFilters(next);
    if (apply) applyFilters(next);
  }
  function applyFilters(value = filters) {
    if (value.start && value.end && value.start > value.end) return;
    generation.current += 1; more.current?.abort();
    setRecords([]); setNextCursor(null); setError(null); setLoading(canRead);
    setAppliedFilters(value);
    setQuery(queryFor(value));
  }
  function clear() {
    const value = defaultRecordFilters();
    navigationMemory.records = { owner, filters: value }; setFilters(value); applyFilters(value);
  }
  useEffect(() => {
    const controller = new AbortController();
    const current = ++generation.current;
    more.current?.abort();
    setRecords([]); setNextCursor(null); setError(null); setLoadingMore(false); setLoading(canRead);
    if (canRead && access.patientId) {
      void searchRecords(access.patientId, query, { signal: controller.signal, csrfToken: access.csrfToken ?? undefined }).then(result => {
        if (!controller.signal.aborted && current === generation.current) { setRecords(result.records); setNextCursor(result.next_cursor); }
      }, failure => {
        if (!(failure instanceof Error)) throw failure;
        if (!controller.signal.aborted && current === generation.current) setError(failure);
      }).finally(() => { if (!controller.signal.aborted && current === generation.current) setLoading(false); });
    }
    return () => { controller.abort(); more.current?.abort(); generation.current += 1; };
  }, [access.patientId, access.csrfToken, canRead, query, reload]);
  async function loadMore() {
    if (!access.patientId || !nextCursor || loadingMore || more.current) return;
    const controller = new AbortController(); more.current = controller;
    const current = generation.current;
    setLoadingMore(true); setError(null);
    try {
      const result = await searchRecords(access.patientId, { ...query, cursor: nextCursor }, { signal: controller.signal, csrfToken: access.csrfToken ?? undefined });
      if (!controller.signal.aborted && current === generation.current) { setRecords(value => [...value, ...result.records]); setNextCursor(result.next_cursor); }
    } catch (failure) {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted && current === generation.current) { setRecords([]); setNextCursor(null); setError(failure); }
    } finally {
      if (more.current === controller) more.current = null;
      if (!controller.signal.aborted && current === generation.current) setLoadingMore(false);
    }
  }
  return <UI2FamilyFrame title="记录" screenId="UI2-05" note="时间轴、档案、摘要和草稿各有明确入口。" backTo="/home" primary="records">
    <UI2ReadState loading={loading} error={error} onRetry={() => setReload(value => value + 1)} />
    {canRead && !loading && !error && <>
      <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{active?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>
      <div className="feature-grid"><button onClick={() => navigate('/archives')}><Layers size={34} /><strong>长期档案</strong><small>按医院与就诊查找</small></button><button onClick={() => navigate('/summaries')}><FileText size={34} /><strong>就医摘要</strong><small>准备本次沟通资料</small></button></div>
      <div className="section-heading"><h2>记录时间轴</h2><button className="text-action" onClick={() => change('open', !filters.open)}>筛选<ChevronRight size={14} /></button></div>
      <div className="segments" role="tablist" aria-label="记录类别">{categories.map(category => <button key={category} role="tab" aria-selected={filters.category === category} className={filters.category === category ? 'selected' : ''} onClick={() => change('category', category, true)}>{category}</button>)}</div>
      {filters.open && <section className="card form-card" aria-label="筛选记录" onBlur={event => {
        if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) applyFilters();
      }} onKeyDown={event => { if (event.key === 'Enter' && !event.nativeEvent.isComposing && event.target instanceof HTMLInputElement) { event.preventDefault(); applyFilters(); } }}>
        <div className="field-grid"><label className="field"><span>开始日期</span><input type="date" value={filters.start} onChange={event => change('start', event.target.value)} /></label>
          <label className="field"><span>结束日期</span><input type="date" value={filters.end} aria-invalid={dateError} aria-describedby={dateError ? 'record-date-error' : undefined} onChange={event => change('end', event.target.value)} /></label></div>
        {dateError && <p id="record-date-error" role="alert">结束日期不能早于开始日期。</p>}
        <label className="field"><span>来源医院</span><input placeholder="输入医院名称" autoComplete="off" maxLength={120} value={filters.organization} onChange={event => change('organization', event.target.value)} /></label>
        <label className="field"><span>核对状态</span><select value={filters.verification} onChange={event => change('verification', event.target.value as RecordFilters['verification'])}>
          <option value="all">全部状态</option><option value="self">本人确认</option><option value="pending">待专业核对</option><option value="verified">已专业核对</option>
          {['unverified', 'needs_correction', 'revoked'].includes(filters.verification) && <option value={filters.verification}>{({ unverified: '未核对', needs_correction: '需补正', revoked: '已撤销' } as Record<string, string>)[filters.verification]}</option>}
        </select></label>
        <details><summary>更多筛选条件</summary><label className="field"><span>检验项目名称</span><input placeholder="输入报告上的项目名称" type="search" autoComplete="off" maxLength={120} aria-describedby="record-project-hint" value={filters.project} onChange={event => change('project', event.target.value)} /></label>
          <p id="record-project-hint">仅设置查找条件。登录并读取记录后按原项目名称查找，获准字典接入后可选择标准项目。</p>
          <label className="field"><span>资料来源</span><select value={filters.source} onChange={event => change('source', event.target.value as RecordFilters['source'])}><option value="all">全部来源</option><option value="self_report">监护人自报</option><option value="report_transcription">报告转录</option><option value="authorized_clinical">获准临床资料</option></select></label>
          <label className="field"><span>专业核对状态</span><select value={filters.verification === 'self' ? 'all' : filters.verification} onChange={event => change('verification', event.target.value as RecordFilters['verification'])}><option value="all">全部核对状态</option><option value="unverified">未核对</option><option value="pending">待核对</option><option value="verified">已核对</option><option value="needs_correction">需补正</option><option value="revoked">已撤销</option></select></label>
        </details><button className="button secondary" onClick={clear}>清除筛选</button>
      </section>}
      {(appliedFilters.category !== '全部' || appliedFilters.organization || appliedFilters.project || appliedFilters.start || appliedFilters.end || appliedFilters.source !== 'all' || appliedFilters.verification !== 'all') && <p role="status">已选：{appliedFilters.category}{appliedFilters.organization ? ` · 医院：${appliedFilters.organization}` : ''}{appliedFilters.project ? ` · 项目：${appliedFilters.project}` : ''}{appliedFilters.source !== 'all' ? ` · ${originLabels[appliedFilters.source]}` : ''}{appliedFilters.verification !== 'all' ? ` · ${({ self: '本人确认', pending: '待专业核对', verified: '已专业核对', unverified: '未核对', needs_correction: '需补正', revoked: '已撤销' } as Record<string, string>)[appliedFilters.verification]}` : ''}{appliedFilters.start || appliedFilters.end ? ` · ${appliedFilters.start || '不限开始'} 至 ${appliedFilters.end || '不限结束'}` : ''}</p>}
      {!records.length ? <section className="card"><div className="empty"><span className="empty-icon"><ClipboardList size={54} /></span><h3>{Object.entries(query).some(([key, value]) => key !== 'limit' && value !== undefined && value !== false) ? '没有符合筛选条件的记录' : '记录从这里开始'}</h3><p>体温、报告和治疗资料，按实际发生时间排列。</p><button className="text-action" onClick={() => navigate('/register/reports')}>添加已有资料<ArrowRight size={16} /></button></div></section>
        : ['已有医学时间', '时间待补充'].map(group => {
          const values = records.filter(record => (record.detail.fields.some(field => field.medical_time && field.medical_time.precision !== 'unknown') ? '已有医学时间' : '时间待补充') === group);
          return values.length > 0 && <section key={group}><div className="section-heading"><h2>{group}</h2></div>{values.map(record => <RecordRow key={record.record_id} record={record} />)}</section>;
        })}
      {nextCursor && <button className="button secondary" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? '正在读取后续记录' : '读取更多记录'}</button>}
      <section className="card"><button className="list-row" onClick={() => navigate('/records/period')}><span className="row-icon"><CalendarDays size={30} /></span><span className="row-text"><strong>观察时段</strong><small>明确时间范围与检查间隔</small></span><ChevronRight size={17} /></button>
        <button className="list-row" onClick={() => navigate('/drafts')}><span className="row-icon"><FileClock size={30} /></span><span className="row-text"><strong>我的草稿</strong><small>继续本人尚未完成的记录</small></span><ChevronRight size={17} /></button></section>
      <div className="note blue"><ShieldCheck size={17} /><p>没有新增资料，不代表当前健康状况正常。</p></div>
    </>}
  </UI2FamilyFrame>;
}
