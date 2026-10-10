import { useEffect, useState } from 'react';
import { searchRecords } from '../../api/endpoints';
import { fieldLabel, fieldValue, originLabels, timeLabel, verificationLabel, type RecordView } from '../../api/records';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { Thermometer, Activity, FileText, Pill, ChevronRight } from '../../components/ThreeDIcon';
import { useAccess } from '../../state/access';
import { navigationMemory } from '../../state/navigation';
import child from '../../../../ui-delivery/assets/2x/child-neutral-front@2x.png';

const categories = [
  { id: 'temperature', title: '体温记录', kind: 'observation', icon: Thermometer, entry: '/register/observations' },
  { id: 'symptoms', title: '症状记录', kind: 'observation', icon: Activity, entry: '/register/observations' },
  { id: 'reports', title: '检验报告', kind: 'report', icon: FileText, entry: '/register/reports' },
  { id: 'therapies', title: '治疗资料', kind: 'therapy', icon: Pill, entry: '/register/therapies' },
];

function ArchiveSection({ category }: { category: typeof categories[number] }) {
  const { patientId, relations } = useAccess();
  const [records, setRecords] = useState<Array<{ record_id: string; detail: RecordView }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [generation, setGeneration] = useState(0);
  const [time, setTime] = useState(navigationMemory.archive.time);
  const allowed = relations.find(relation => relation.patient_id === patientId)?.actions ?? [];
  useEffect(() => {
    const controller = new AbortController();
    if (!patientId) return;
    setLoading(true);
    setError(null);
    setRecords([]);
    async function readAll() {
      const entries: typeof records = [];
      let cursor: string | null = null;
      do {
        const result = await searchRecords(patientId!, { kind: category.kind, concept_code: category.id === 'temperature' ? 'temp' : undefined, limit: 100, cursor }, { signal: controller.signal });
        if (controller.signal.aborted) return;
        entries.push(...result.records as typeof records);
        cursor = result.next_cursor as string | null;
      } while (cursor);
      if (!controller.signal.aborted) setRecords(entries);
    }
    void readAll().then(() => { if (!controller.signal.aborted) setLoading(false); }, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setError(failure); setLoading(false); }
    });
    return () => controller.abort();
  }, [patientId, category, generation]);
  const entries = records.flatMap(record => record.detail.fields.filter(field => category.id === 'temperature' ? field.field_code === 'temp' : category.id === 'symptoms' ? field.field_code !== 'temp' : true).map(field => ({ record, field })));
  const known = entries.filter(({ field }) => field.medical_time && field.medical_time.precision !== 'unknown');
  const times = [...new Map(known.map(({ field }) => [field.medical_time_id!, timeLabel(field.medical_time)])).entries()];
  const selected = time === 'latest' ? times[0]?.[0] : time;
  const shown = entries.filter(({ field }) => {
    if (time === 'unknown') return !field.medical_time || field.medical_time.precision === 'unknown';
    const value = field.medical_time;
    return value && value.precision !== 'unknown' && field.medical_time_id === selected;
  });
  return <section className="card form-card"><h3>{category.title}</h3>
    <UI2ReadState loading={loading} error={error} onRetry={() => setGeneration(value => value + 1)} />
    {!loading && !error && !entries.length && <p>暂无本类资料</p>}
    {!loading && !error && entries.length > 0 && <>
      <div className="field"><label htmlFor="archive-time">查看记录时间</label><select id="archive-time" value={time} onChange={event => { setTime(event.target.value); navigationMemory.archive.time = event.target.value; }}>
        <option value="latest">{times.length ? `最新记录 · ${times[0][1]}` : '尚无明确医学时间'}</option>
        {times.length > 1 && times.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
        {!['latest', 'unknown'].includes(time) && !times.some(([value]) => value === time) && <option value={time}>原选择时间当前不可读取</option>}
        <option value="unknown">时间待补充</option>
      </select></div>
      <p role="status">{time === 'latest' ? times.length ? '最新记录' : '尚无明确的医学时间，请查看时间待补充资料。' : time === 'unknown' ? '时间待补充' : '查看当时记录'}</p>
      {!shown.length && <p>所选时间没有可展示的资料。</p>}
      {shown.map(({ record, field }) => <article key={field.field_id} className="report-item">
        <h3>{fieldLabel(field)}：{fieldValue(field)}</h3><p>{timeLabel(field.medical_time)}</p>
        <p>{originLabels[record.detail.origin_type] ?? record.detail.origin_type} · {verificationLabel(field)}</p>
        <ContextLink className="text-action" to={`/records/${record.record_id}`}>查看完整记录</ContextLink>
      </article>)}
    </>}
    {allowed.includes('write_record') && <ContextLink className="text-action" to={category.entry}>登记{category.title}</ContextLink>}
  </section>;
}

export function HealthArchive() {
  const [selected, setSelected] = useState(navigationMemory.archive.category);
  const category = categories.find(item => item.id === selected);
  function change(value: string) {
    navigationMemory.archive.category = value;
    navigationMemory.archive.time = 'latest';
    setSelected(value);
  }
  return <div aria-label="健康档案分类资料">
    {category ? <><button type="button" className="text-action" onClick={() => change('')}>返回档案分类</button><ArchiveSection key={category.id} category={category} /></> : <>
      <img src={child} width={124} height={169} alt="通用儿童形象" />
      <p className="section-note">按记录中的发生或采样时间查看。</p>
      <div className="quick-grid">{categories.map(({ id, title, icon: Icon }) => <button key={id} type="button" onClick={() => change(id)}><Icon size={32} /><strong>{title}</strong><ChevronRight size={22} /></button>)}</div>
    </>}
  </div>;
}
