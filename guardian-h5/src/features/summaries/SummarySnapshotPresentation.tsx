import type { SummaryKind, SummaryView } from '../../api/summaries';
import { fieldLabel, fieldValue, originLabels, verificationLabel } from '../../api/records';
import { ContextLink } from '../../components/ContextLink';
import { ChevronRight, FileText, ShieldCheck } from '../../components/ThreeDIcon';
import { eventTimeLabel, medicalSourceTimeLabel } from '../../components/recordTimePresentation';

const sections: Array<[SummaryKind, string]> = [['observation', '体温与症状'], ['report', '检验报告'], ['therapy', '治疗资料']];
export function summaryRecordCount(summary: SummaryView, kind: SummaryKind) {
  return new Set(summary.items.filter(item => item.snapshot.kind === kind).map(item => item.snapshot.record_id)).size;
}
export function SummarySnapshotPresentation({ summary, trend }: { summary: SummaryView | null; trend?: React.ReactNode }) {
  const available = summary !== null && summary.validity === 'valid' && !summary.needs_regeneration;
  return <>
    <section className="rich-summary-cover">
      <span className="badge">{available ? '资料摘要 · 固定版本' : '资料摘要 · 尚无有效内容'}</span>
      <h2>{summary ? `${summary.display_id}的就医资料` : '就医资料摘要'}</h2>
      <p>{summary ? `${summary.from_date} — ${summary.to_date}` : '尚未生成，请先选择资料范围'}</p>
      {summary && <p>范围时区：{summary.scope_timezone}</p>}
      <div className="rich-summary-counts">{sections.map(([kind, title]) => <span key={kind}>{title}<strong>
        {available ? summary.record_kinds && !summary.record_kinds.includes(kind) ? '未纳入' : `${summaryRecordCount(summary, kind)} 条` : '—'}
      </strong></span>)}</div>
    </section>
    <section className="rich-summary-block"><h3>体温与症状</h3>
      <section className="card form-card">{available ? <>
        {trend}
        {summary.items.some(item => item.snapshot.kind === 'observation') ? <details className="rich-summary-observations"><summary>查看体温与症状记录 · {summaryRecordCount(summary, 'observation')} 条</summary>
          <SummaryRecordGroups summary={summary} kind="observation" />
        </details> : <SummaryRecordGroups summary={summary} kind="observation" />}
      </> : <p>尚无当前可见资料</p>}</section>
    </section>
    <section className="rich-summary-block"><h3>检验与治疗</h3><section className="card rich-summary-records">
      {sections.slice(1).map(([kind, title]) => <details className="rich-summary-kind" key={kind}>
        <summary className="list-row"><span className="row-icon"><FileText size={30} /></span><span className="row-text"><strong>{title}</strong>
          <small>{available ? summary.record_kinds && !summary.record_kinds.includes(kind) ? '本次未纳入' : `${summaryRecordCount(summary, kind)} 条固定版本记录` : '尚无当前可见资料'}</small></span><ChevronRight size={17} /></summary>
        {available && <SummaryRecordGroups summary={summary} kind={kind} compact />}
      </details>)}
    </section></section>
    {available && summary.items.some(item => item.snapshot.kind === 'professional') && <section className="rich-summary-block"><h3>获准临床资料</h3>
      <section className="card form-card"><SummaryRecordGroups summary={summary} kind="professional" /></section></section>}
    <div className="note blue"><ShieldCheck size={20} /><p>{summary ? `${eventTimeLabel(summary.generated_at)} 生成；资料或可见范围变化后需要重新生成。` : '尚未生成摘要，本页只展示资料结构。'}原始事实与来源共同保留，不自动生成诊断或用药建议。</p></div>
    <section className="rich-summary-block"><h3>资料来源与缺项</h3><section className="card form-card">
      {!available ? <p>尚无当前可见资料</p> : <>
        <p>{summary.items.some(item => item.snapshot.field.value_state !== 'known' || !item.snapshot.field.medical_time || item.snapshot.field.medical_time.precision === 'unknown')
          ? '部分原值或医学时间尚未确定，具体项目保留原有状态。' : '具体依据版本随各项资料保留。'}</p>
        <p>未知医学时间：{summary.include_unknown_time ? '纳入并保留未知状态' : '未选择纳入'}。统计按记录标识去重，不代表原件份数。</p>
        {summary.include_source_status === false && <p>本次未选择来源与核对状态说明；具体依据版本仍可查询。</p>}
        {summary.items.some(item => item.snapshot.confirmation_snapshot_state === 'not_recorded') && <p>部分历史项目未保存本人确认证明，当前状态不能推断。</p>}
        {summary.items.some(item => item.snapshot.source_anchor_snapshot_state === 'not_recorded') && <p>部分历史项目未保存原件页码，具体记录版本仍可查询。</p>}
      </>}
    </section></section>
  </>;
}

/** Preserve snapshot order and keep each immutable version separate; no time ranking. */
function SummaryRecordGroups({ summary, kind, compact = false }: { summary: SummaryView; kind: string; compact?: boolean }) {
  const items = summary.items.filter(item => item.snapshot.kind === kind);
  if (items.length === 0) return <p className="rich-summary-empty">{summary.record_kinds && kind !== 'professional' && !summary.record_kinds.includes(kind as SummaryKind)
    ? '本次未选择此类资料' : '此范围没有当前可见的资料'}</p>;
  const groups = new Map<string, SummaryView['items']>();
  for (const item of items) {
    const key = `${item.snapshot.record_id}:${item.record_version_id}`;
    const group = groups.get(key);
    if (group) group.push(item); else groups.set(key, [item]);
  }
  const title = sections.find(([value]) => value === kind)?.[1] ?? '获准临床资料';
  return <div className={`rich-summary-record-groups${compact ? ' compact' : ''}`}>{[...groups].map(([key, group]) => {
    const first = group[0].snapshot.field;
    return <details className="rich-summary-record" key={key} data-summary-record-version={group[0].record_version_id}>
      <summary><span className="rich-summary-record-heading"><strong>{title} · {group.length} 项</strong><ChevronRight size={15} /></span>
        <span className="rich-summary-record-preview">{fieldLabel(first)}：{fieldValue(first)}</span>
        <span className="rich-summary-record-time">{fieldLabel(first)}医学时间：{medicalSourceTimeLabel(first.medical_time)}</span>
      </summary>
      <SummaryFields summary={summary} items={group} />
    </details>;
  })}</div>;
}

function SummaryFields({ summary, items }: { summary: SummaryView; items: SummaryView['items'] }) {
  return <>{items.map(item => {
    const { field } = item.snapshot;
    return <article className="rich-summary-field" key={`${item.record_version_id}:${item.field_id}`}>
      <div className="rich-summary-value"><h4>{fieldLabel(field)}</h4><strong>{fieldValue(field)}</strong></div>
      <p className="rich-summary-time">医学时间：{medicalSourceTimeLabel(field.medical_time)}</p>
      {summary.include_source_status !== false && <p className="rich-summary-time">{originLabels[item.snapshot.origin_type] ?? item.snapshot.origin_type} · {verificationLabel(field)}</p>}
      <ContextLink className="text-action" to={`/records/versions/${item.record_version_id}`}>查看摘要依据版本<ChevronRight size={15} /></ContextLink>
      <details><summary>原始内容与来源详情</summary>
        {field.raw_text !== null && field.raw_numeric !== null && <div className="pair"><span>原始文本</span><strong>{field.raw_text}</strong></div>}
        {field.method !== null && <div className="pair"><span>检验方法</span><strong>{field.method}</strong></div>}
        {field.reference_interval !== null && <div className="pair"><span>参考区间</span><strong>{field.reference_interval}</strong></div>}
        {field.report_flag !== null && <div className="pair"><span>报告标记</span><strong>{field.report_flag}</strong></div>}
        {summary.include_source_status !== false && <>
          <div className="pair"><span>来源机构</span><strong>{item.snapshot.source_organization ?? '未提供'}</strong></div>
          <div className="pair"><span>本人确认证明</span><strong>{item.snapshot.confirmation_snapshot_state === 'not_recorded' ? '原摘要未保存' : field.confirmation_id ? '已保存确认证明' : '尚无确认证明'}</strong></div>
          {field.source_anchor && <div className="pair"><span>来源页码</span><strong>第 {field.source_anchor.page_ordinal} 页</strong></div>}
        </>}
      </details>
    </article>;
  })}</>;
}
