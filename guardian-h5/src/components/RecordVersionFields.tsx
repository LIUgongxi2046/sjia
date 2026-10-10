import { fieldLabel, fieldValue, timeLabel, verificationLabel, type RecordView } from '../api/records';
import { Disclosure, Facts } from './ClinicalUI';
import { SourceGallery } from './SourceGallery';

export function RecordVersionFields({ record }: { record: RecordView }) {
  return <>
    {record.fields.map(field => <section key={field.field_id} className="report-item" aria-label={fieldLabel(field)}>
      <h3>{fieldLabel(field)}</h3><p>{fieldValue(field)} · {timeLabel(field.medical_time)} · {verificationLabel(field)}</p>
      <Disclosure title={`${fieldLabel(field)}的完整字段`}>
        <Facts items={[
          ['原始文字', field.raw_text ?? '未提供'], ['原始数值', field.raw_numeric ?? '未提供'],
          ['原始单位', field.raw_unit ?? '未提供'], ['原报告比较符', field.qualifier ?? '无法确定'],
          ['观察状态', field.categorical_value ?? '无法确定'], ['测量或检测方法', field.method ?? '无法确定'],
          ['参考区间', field.reference_interval ?? '未提供'], ['报告标记', field.report_flag ?? '未提供'],
          ['医学时间', timeLabel(field.medical_time)], ['时间精度', field.medical_time?.precision ?? 'unknown'],
          ['时区', field.medical_time?.timezone_name ?? '无法确定'], ['专业核对', verificationLabel(field)],
        ]} />
        {field.source_anchor && <><p>识别原文：{field.source_anchor.original_text}</p><img className="source-photo" src={`/api/files/${field.source_anchor.file_id}/content`} alt={`${fieldLabel(field)}对应的原图`} /></>}
      </Disclosure>
    </section>)}
    {record.report && <Disclosure title="完整报告来源"><Facts items={[
      ['报告机构', record.source_organization ?? '未提供'], ['报告标识', record.report.external_ref ?? '未提供'],
      ['结果状态', record.report.report_status], ['采样时间', timeLabel(record.report.sample_time)], ['出具时间', timeLabel(record.report.result_time)],
    ]} /></Disclosure>}
    {record.attachments.map((photo, index) => <Disclosure key={photo.file_id} title={`查看第 ${photo.ordinal} 张原图`}><SourceGallery initialIndex={index} photos={record.attachments.map(value => ({ id: value.file_id, url: `/api/files/${value.file_id}/content`, label: `版本 ${record.version} 第 ${value.ordinal} 张原图` }))} /></Disclosure>)}
  </>;
}
