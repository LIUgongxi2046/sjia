import { useCallback, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react';
import { Activity, FileText, History, Image, UsersRound } from '../../components/ThreeDIcon';
import { UI2FamilyFrame } from '../../components/UI2FamilyFrame';
import { UI2Sheet } from '../../components/UI2Frame';
import { UI2ReadState } from '../../components/UI2ReadState';
import { ContextLink } from '../../components/ContextLink';
import { PrivateSourceGallery } from '../../components/PrivateSourceGallery';
import { useRecordResource } from '../../api/useRecordResource';
import { useAccess } from '../../state/access';
import { fieldLabel, fieldValue, originLabels, timeLabel, verificationLabel, type RecordView } from '../../api/records';

const tabs = ['原始内容', '来源与状态', '时间与版本'] as const;
const kinds: Record<string, string> = { observation: '体温症状', report: '检验报告', therapy: '治疗资料', professional: '获准临床资料' };
const captures: Record<string, string> = { manual: '手动填写', ocr_confirmed: '识别确认', voice_confirmed: '语音确认', authorized_import: '获准资料导入' };
const qualities: Record<string, string> = { pending: '待检查', valid: '格式检查通过', needs_correction: '格式需要补正' };

export function UI2RecordDetailPage() { return <RecordDetail version={false} />; }
export function UI2RecordVersionPage() { return <RecordDetail version />; }

function RecordDetail({ version }: { version: boolean }) {
  const access = useAccess();
  const { recordId, versionId } = useParams();
  const navigate = useNavigate();
  const [tab, setTab] = useState<(typeof tabs)[number]>('原始内容');
  const [selecting, setSelecting] = useState(false);
  const close = useCallback(() => setSelecting(false), []);
  const canRead = !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const id = version ? versionId : recordId;
  const resource = useRecordResource<RecordView>(canRead && id ? `/api/${version ? 'record-versions' : 'records'}/${id}` : null,
    access.patientId, 'RecordView', id ?? null, version ? 'record_version_id' : 'record_id');
  const record = canRead ? resource.data : null;
  const relation = access.relations.find(value => value.patient_id === access.patientId);
  const concepts = record ? [...new Map(record.fields.filter(field => field.concept_code).map(field => [field.concept_code!, field])).values()] : [];
  const files = record ? [...new Map([...record.attachments, ...record.fields.filter(field => field.source_anchor).map(field => ({
    file_id: field.source_anchor!.file_id, ordinal: field.source_anchor!.page_ordinal,
  }))].map(file => [file.file_id, file])).values()] : [];
  const missing = tab === '原始内容' ? ['项目名称', '原始数值', '比较符', '原始单位'] : tab === '来源与状态'
    ? ['原始文书', '来源页码', '本人确认', '专业核对'] : ['医学时间', '文书形成时间', '平台取得时间', '当前版本'];
  return <UI2FamilyFrame title={version ? '历史版本' : '记录详情'} screenId={version ? 'UI2-10' : 'UI2-06'}
    note={version ? '历史内容只读，继续执行当前权限。' : '原值、医学时间、来源与核对状态共同展示。'}
    backTo={version && record ? `/records/${record.record_id}/history` : '/records'}
    overlay={record && selecting && <UI2Sheet title="选择历次检验项目" onClose={close}>
      {concepts.map(field => <ContextLink className="list-row" key={field.concept_code} to={`/records/analytes/${encodeURIComponent(field.concept_code!)}`}><span className="row-text"><strong>{fieldLabel(field)}</strong><small>每次结果保留原值、时间与来源</small></span><ChevronRight size={17} /></ContextLink>)}
    </UI2Sheet>}>
    {canRead && <button className="patient" onClick={() => navigate('/associations')}><span className="patient-icon"><UsersRound size={23} /></span><span><strong>{relation?.display_id ?? '选择患儿'}</strong><small>查看本人获准的资料</small></span><ChevronDown size={17} /></button>}
    <UI2ReadState loading={resource.loading} error={resource.error} onRetry={resource.reload} />
    <section className="card record-heading"><FileText size={42} /><div><span className="eyebrow">{version ? '历史只读内容' : '资料详情'}</span><h2>{record ? kinds[record.kind] : '尚未选择记录'}</h2></div><span className="badge neutral">{record ? `版本 ${record.version}` : '待读取'}</span></section>
    <div className="segments" role="tablist" aria-label="记录详情内容">{tabs.map(value => <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? 'selected' : ''} onClick={() => setTab(value)}>{value}</button>)}</div>
    {!record && <section className="card form-card">{missing.map(label => <div className="pair" key={label}><span>{label}</span><strong>待读取</strong></div>)}</section>}
    {record && tab === '原始内容' && record.fields.map(field => <section className="card form-card" key={field.field_id}>
      <div className="pair"><span>项目名称</span><strong>{fieldLabel(field)}</strong></div>
      <div className="pair"><span>原始数值</span><strong>{field.raw_numeric ?? '未提供'}</strong></div>
      <div className="pair"><span>比较符</span><strong>{field.qualifier ?? '无法确定'}</strong></div>
      <div className="pair"><span>原始单位</span><strong>{field.raw_unit ?? '未提供'}</strong></div>
      <details><summary>{fieldLabel(field)}的完整字段</summary>
        <div className="pair"><span>原始内容</span><strong>{fieldValue(field)}</strong></div>
        <div className="pair"><span>原始文字</span><strong>{field.raw_text ?? '未提供'}</strong></div>
        <div className="pair"><span>观察或使用状态</span><strong>{field.categorical_value ?? '无法确定'}</strong></div>
        <div className="pair"><span>测量或检测方法</span><strong>{field.method ?? '无法确定'}</strong></div>
        <div className="pair"><span>参考区间</span><strong>{field.reference_interval ?? '未提供'}</strong></div>
        <div className="pair"><span>报告标记</span><strong>{field.report_flag ?? '未提供'}</strong></div>
        <div className="pair"><span>医学时间</span><strong>{timeLabel(field.medical_time)}</strong></div>
        <div className="pair"><span>时间精度</span><strong>{field.medical_time?.precision ?? 'unknown'}</strong></div>
        <div className="pair"><span>时区</span><strong>{field.medical_time?.timezone_name ?? '无法确定'}</strong></div>
        <div className="pair"><span>专业核对</span><strong>{verificationLabel(field)}</strong></div>
        {record.therapy_events?.filter(event => event.source_field_id === field.field_id).map(event => <details key={event.medication_event_id}>
          <summary>已保存治疗事件及子项</summary>
          <div className="pair"><span>实际使用状态</span><strong>{event.status?.value_state === 'known'
            ? ({ ongoing: '持续使用', ended: '已结束', not_started: '尚未使用' } as const)[event.status.value!] : '无法确定'}</strong></div>
          <div className="pair"><span>事件医学时间</span><strong>{timeLabel(event.medical_time)}</strong></div>
          {event.note !== undefined && <div className="pair"><span>资料说明</span><strong>{event.note ?? '未提供'}</strong></div>}
          {event.items.map(item => <div key={item.medication_event_item_id}>
            <div className="pair"><span>原药名</span><strong>{item.medication_name}</strong></div>
            {item.dose !== undefined && <div className="pair"><span>资料中的剂量</span><strong>{item.dose ?? '无法确定'}{item.dose_unit ? ` ${item.dose_unit}` : ''}</strong></div>}
            {item.concentration_text !== undefined && <div className="pair"><span>原始浓度</span><strong>{item.concentration_text ?? '未提供'}</strong></div>}
            {item.route_text !== undefined && <div className="pair"><span>原始给药途径</span><strong>{item.route_text ?? '未提供'}</strong></div>}
            {item.frequency_text !== undefined && <div className="pair"><span>原始使用频次</span><strong>{item.frequency_text ?? '未提供'}</strong></div>}
            {item.interval_value !== undefined && <div className="pair"><span>原始使用间隔</span><strong>{item.interval_value ?? '无法确定'}{item.interval_unit ? ` ${item.interval_unit}` : ''}</strong></div>}
          </div>)}
          <div className="pair"><span>事件标识</span><strong>{event.medication_event_id}</strong></div>
          {event.previous_event_id && <div className="pair"><span>原版本事件标识</span><strong>{event.previous_event_id}</strong></div>}
        </details>)}
        {field.source_anchor && <><p>识别原文：{field.source_anchor.original_text}</p><PrivateSourceGallery files={[{ file_id: field.source_anchor.file_id, ordinal: field.source_anchor.page_ordinal }]} /></>}
      </details>
    </section>)}
    {record && tab === '来源与状态' && <>
      <section className="card form-card"><div className="pair"><span>资料来源</span><strong>{originLabels[record.origin_type]}</strong></div>
        <div className="pair"><span>来源医院</span><strong>{record.source_organization ?? '未提供'}</strong></div>
        <div className="pair"><span>登记方式</span><strong>{captures[record.capture_method]}</strong></div>
        <div className="pair"><span>格式质量</span><strong>{qualities[record.quality_status]}</strong></div>
      </section>
      {record.fields.map(field => <section className="card form-card" key={field.field_id}><h3>{fieldLabel(field)}</h3>
        <div className="pair"><span>原始文书</span><strong>{field.source_anchor ? '已保存原文出处' : '未附此项原文出处'}</strong></div>
        <div className="pair"><span>来源页码</span><strong>{field.source_anchor?.page_ordinal ?? '未提供'}</strong></div>
        <div className="pair"><span>本人确认</span><strong>{record.family_confirmation
          ? `${record.family_confirmation.confirmed_by_self ? '本人已确认' : '登记人员已确认'} · ${new Date(record.family_confirmation.confirmed_at).toLocaleString('zh-CN')}`
          : field.confirmation_id ? '已保存逐项确认，确认人员尚未读取' : '未读取确认证明'}</strong></div>
        <div className="pair"><span>专业核对</span><strong>{verificationLabel(field)}</strong></div>
      </section>)}
      {record.report && <section className="card form-card"><h3>完整报告来源</h3>
        <div className="pair"><span>报告标识</span><strong>{record.report.external_ref ?? '未提供'}</strong></div>
        <div className="pair"><span>结果状态</span><strong>{record.report.report_status}</strong></div>
        <div className="pair"><span>报告采样时间</span><strong>{timeLabel(record.report.sample_time)}</strong></div>
        <div className="pair"><span>报告出具时间</span><strong>{timeLabel(record.report.result_time)}</strong></div>
      </section>}
    </>}
    {record && tab === '时间与版本' && <section className="card form-card">
      {record.fields.map(field => <div className="pair" key={field.field_id}><span>{fieldLabel(field)}医学时间</span><strong>{timeLabel(field.medical_time)}</strong></div>)}
      <div className="pair"><span>文书形成时间</span><strong>{record.report ? timeLabel(record.report.result_time) : '未提供'}</strong></div>
      <div className="pair"><span>平台取得时间</span><strong>当前记录未返回原件接收时间</strong></div>
      <div className="pair"><span>登记时间</span><strong>{new Date(record.recorded_at).toLocaleString()}</strong></div>
      <div className="pair"><span>{version ? '所读版本' : '当前版本'}</span><strong>{record.version}</strong></div>
      <div className="pair"><span>更正时间</span><strong>{record.corrected_at ? new Date(record.corrected_at).toLocaleString() : '未更正'}</strong></div>
      <div className="pair"><span>更正原因</span><strong>{record.correction_reason ?? '首次登记'}</strong></div>
    </section>}
    <section className="card">
      {record && files.length ? <ContextLink className="list-row" to={`/archives/source?version=${record.record_version_id}`}><span className="row-icon"><Image size={30} /></span><span className="row-text"><strong>查看原始资料</strong><small>每项内容都保留原文出处</small></span><ChevronRight size={17} /></ContextLink> : <button className="list-row" disabled><span className="row-icon"><Image size={30} /></span><span className="row-text"><strong>查看原始资料</strong><small>{record ? '本份资料尚未附原图' : '每项内容都保留原文出处'}</small></span><ChevronRight size={17} /></button>}
      <button className="list-row" disabled={!record || !concepts.length} onClick={() => setSelecting(true)}><span className="row-icon"><Activity size={30} /></span><span className="row-text"><strong>历次检验</strong></span><ChevronRight size={17} /></button>
      {!version && (record?.allowed_actions?.includes('read_history') ? <ContextLink className="list-row" to={`/records/${record.record_id}/history`}><span className="row-icon"><History size={30} /></span><span className="row-text"><strong>更正与历史</strong></span><ChevronRight size={17} /></ContextLink> : <button className="list-row" disabled><span className="row-icon"><History size={30} /></span><span className="row-text"><strong>更正与历史</strong></span><ChevronRight size={17} /></button>)}
    </section>
    {!version && (record?.allowed_actions?.includes('correct_record') ? <ContextLink className="button secondary" to={`/records/${record.record_id}/correct`}>更正记录</ContextLink> : <button className="button secondary" disabled>更正记录</button>)}
    {version && record && <ContextLink className="button secondary" to={`/records/${record.record_id}`}>查看当前记录</ContextLink>}
    <div className="note blue"><ShieldCheck size={17} /><p>未提供的数据保持未知，不能作为临床判断依据。</p></div>
  </UI2FamilyFrame>;
}
