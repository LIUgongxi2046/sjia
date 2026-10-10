import { useState } from 'react';
import { ChoiceBar, Disclosure, Field, SelectField, TimeField } from './ClinicalUI';
import { DictionaryField } from './DictionaryField';

const types = ['体温症状', '检验报告', '治疗资料'];

export function CorrectionFields() {
  const [type, setType] = useState(types[0]);
  return <Disclosure title="查看可以更正的内容">
    <p>读取记录后仅显示对应内容。当前可查看字段结构，不能更改实际记录类型。</p>
    <ChoiceBar label="更正字段结构" options={types} value={type} onChange={setType} />
    <div hidden={type !== '体温症状'} className="field-group">
      <Field label="体温数值" numeric /><SelectField label="体温单位" options={['尚未选择', '摄氏度（°C）', '华氏度（°F）', '无法确定']} /><TimeField label="测量时间" /><Field label="测量方式" />
      <DictionaryField label="观察项目" /><SelectField label="观察状态" options={['无法确定', '观察到', '未观察到']} /><TimeField label="症状发生时间" /><Field label="观察说明" multiline />
    </div>
    <div hidden={type !== '检验报告'} className="field-group">
      <Field label="报告标识或来源引用" /><Field label="报告机构" /><SelectField label="报告结果状态" options={['无法确定', '初步结果', '最终结果', '更正结果']} /><TimeField label="采样时间" /><TimeField label="报告出具时间" />
      <Field label="报告原项目名" /><SelectField label="原报告比较符" options={['尚未选择', '=', '<', '≤', '>', '≥']} /><Field label="原始数值" numeric /><Field label="原始单位" />
      <DictionaryField label="检验项目" /><DictionaryField label="报告单位" /><Field label="检测方法" /><Field label="参考区间原文" /><TimeField label="项目独立采样时间" />
    </div>
    <div hidden={type !== '治疗资料'} className="field-group">
      <Field label="原药名" /><SelectField label="实际使用状态" options={['无法确定', '持续使用', '已结束']} /><TimeField label="开始时间" /><TimeField label="结束时间" /><Field label="补充说明" multiline />
    </div>
    <Disclosure title="资料出处"><SelectField label="资料来源" options={['尚未选择', '监护人自报', '报告转录']} /><Field label="来源描述或引用" /><p>原始照片保留。照片需要补充时，通过获准文件服务添加并重新核对关联字段。</p></Disclosure>
  </Disclosure>;
}
