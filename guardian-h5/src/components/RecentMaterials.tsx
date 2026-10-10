import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { fetchJson } from '../api/client';
import { type FieldConfigView } from '../api/endpoints';
import { fieldLabel, fieldValue, timeLabel, type RecordView } from '../api/records';
import { useResource } from '../api/useResource';
import { useAccess } from '../state/access';
import { ChoiceBar, Section } from './ClinicalUI';
import { ReadState } from './ReadState';
import { SourceGallery } from './SourceGallery';

export function RecentMaterials({ kind }: { kind: 'report' | 'therapy' }) {
  const { patientId } = useAccess();
  const [records, setRecords] = useState<Array<{ record_id: string; detail: RecordView }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('关注指标');
  const [reload, setReload] = useState(0);
  const config = useResource<FieldConfigView>(kind === 'report' && patientId ? `/api/patients/${patientId}/field-config?kind=report` : null);
  useEffect(() => {
    const controller = new AbortController();
    setRecords([]);
    setError(null);
    if (!patientId) return;
    setLoading(true);
    void fetchJson<{ records: typeof records }>(`/api/patients/${patientId}/records/search`, { method: 'POST', body: { kind, limit: 1 }, signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setRecords(value.records);
    }, failure => { if (!controller.signal.aborted) setError(failure.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [patientId, kind, reload]);
  const record = records[0]?.detail;
  const fields = record?.fields ?? [];
  const medication = fields.find(field => field.field_code === 'medication_name');
  const watch = config.data?.state === 'available' ? config.data.watch_field_codes : [];
  const visible = fields.filter(field => filter === '全部指标' || (filter === '关注指标' ? watch.includes(field.field_code) || Boolean(field.concept_code && watch.includes(field.concept_code)) : Boolean(field.report_flag)));
  return <>
    <ReadState loading={loading} error={error} onRetry={() => setReload(value => value + 1)} />
    {!loading && !error && !record && <Section title={kind === 'report' ? '最近检验' : '治疗记录'}><p>尚无本类记录。</p></Section>}
    {record && <Section title={kind === 'report' ? '最近检验' : '治疗记录'}>
      {kind === 'report' ? <p>采样：{timeLabel(record.report?.sample_time ?? null)} · {fields.length} 项结果</p> : <><h2>{medication ? fieldValue(medication) : '治疗名称未填写'}</h2><p>{fields.filter(field => ['therapy_status', 'therapy_start', 'therapy_end'].includes(field.field_code)).map(field => `${fieldLabel(field)}：${fieldValue(field)}`).join(' · ')}</p></>}
      <Link className="text-action" to={`/records/${record.record_id}`}>{kind === 'report' ? '查看报告记录' : '查看使用记录'}</Link>
      <SourceGallery photos={record.attachments.map(photo => ({ id: photo.file_id, url: `/api/files/${photo.file_id}/content`, label: `第 ${photo.ordinal} 张原图` }))} />
    </Section>}
    {record && kind === 'report' && <Section title="报告指标">
      <ChoiceBar label="报告指标范围" options={['关注指标', '报告标记', '全部指标']} value={filter} onChange={setFilter} />
      <ReadState loading={config.loading} error={config.error} onRetry={config.reload} />
      {filter === '关注指标' && config.data?.state !== 'available' && <p>关注指标配置暂时不可用，可以查看全部原始指标。</p>}
      {visible.map(field => <div key={field.field_id} className="result-row"><div><strong>{fieldLabel(field)}</strong>{field.report_flag && <p>报告原文标记：{field.report_flag}</p>}</div><strong>{fieldValue(field)}</strong></div>)}
      {!visible.length && !config.loading && <p>这份报告没有符合当前条件的指标。</p>}
      <p className="field-hint">标记保留报告原文；未标记的结果不代表正常。</p>
    </Section>}
  </>;
}
