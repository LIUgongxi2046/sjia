import { loadCurrentPatient } from './load-current-patient.mjs';

const { patientCase: data, sources } = await loadCurrentPatient(new URL('./', import.meta.url));
const content = document.querySelector('#content');
const times = new Map(data.time_contexts.map(time => [time.id, time.raw_text]));
const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const paragraph = (parent, text, className) => parent.append(element('p', text, className));
const sourceLink = (parent, id) => {
  const source = sources.get(id);
  const link = element('a', `查看来源：${source.label}`);
  link.href = source.url;
  link.target = '_blank';
  link.rel = 'noopener';
  parent.append(link);
};
const section = (id, title) => {
  const node = element('section');
  node.id = id;
  node.append(element('h2', title));
  content.append(node);
  return node;
};
const table = (parent, headers, rows) => {
  const wrapper = element('div', undefined, 'table-scroll');
  const grid = element('table');
  const head = element('thead');
  const titles = element('tr');
  headers.forEach(title => titles.append(element('th', title)));
  head.append(titles);
  const body = element('tbody');
  rows.forEach(values => {
    const row = element('tr');
    values.forEach(value => row.append(element('td', value === null ? '未提供' : String(value))));
    body.append(row);
  });
  grid.append(head, body);
  wrapper.append(grid);
  parent.append(wrapper);
};

document.querySelector('#patient-summary').textContent = `${data.patient.display_name} · 原文基本资料：${data.patient.reported_age.raw_text} · ${data.patient.display_id}`;
const profile = section('profile', '基本资料与随访');
paragraph(profile, `主诉：${data.patient.chief_complaint}`);
paragraph(profile, '姓名、出生日期及年龄对应日期未提供。');
paragraph(profile, data.followup.text);
paragraph(profile, '随访具体日期未提供，治疗信息按原文保留。', 'muted');
sourceLink(profile, data.followup.source_id);
const diagnoses = section('diagnoses', '来源文章记录的诊断');
data.diagnoses.forEach(item => { const row = element('article'); row.append(element('h3', item.name)); paragraph(row, item.basis); diagnoses.append(row); });
const history = section('history', '病程与病史');
data.history.forEach(item => { const row = element('article'); if (item.time_ref) paragraph(row, times.get(item.time_ref), 'muted'); paragraph(row, item.text); history.append(row); });
const findings = section('findings', '体格检查与其他检查');
data.qualitative_findings.forEach(item => paragraph(findings, item.text));
const labs = section('measurements', '实验室检查与测量');
data.measurement_panels.forEach(panel => {
  const block = element('article'); block.append(element('h3', panel.label));
  paragraph(block, times.get(panel.time_ref), 'muted');
  if (panel.source_quote) paragraph(block, panel.source_quote, 'muted');
  table(block, ['项目', '原始结果', '单位', '参考范围', '原报告标记'], panel.items.map(item => [item.name, item.raw_text, item.unit, item.reference_interval, item.flag]));
  sourceLink(block, panel.source_id); labs.append(block);
});
const microbiology = section('microbiology', '病原学检查');
data.microbiology.forEach(group => {
  const block = element('article'); block.append(element('h3', `${group.method} · 标本：${group.specimen ?? '未提供'}`));
  paragraph(block, times.get(group.time_ref), 'muted');
  table(block, ['项目', '原始结果', '原报告解释'], group.results.map(result => [result.organism, result.raw_text, result.interpretation])); microbiology.append(block);
});
const imaging = section('imaging', '影像报告与原始附件');
paragraph(imaging, '图片与具体检查报告的对应关系待确认。', 'muted');
data.imaging_studies.forEach(study => paragraph(imaging, study.report_text));
const gallery = element('div', undefined, 'assets');
[...sources.values()].filter(source => source.kind === 'image').forEach(source => {
  const figure = element('figure'); const link = element('a'); link.href = source.url; link.target = '_blank'; link.rel = 'noopener';
  const image = element('img'); image.src = source.url; image.alt = source.label;
  link.append(image); figure.append(link, element('figcaption', source.label)); gallery.append(figure);
});
imaging.append(gallery);
const therapy = section('therapy', '治疗经过');
data.therapy_events.forEach(event => {
  const row = element('article'); row.append(element('h3', event.time_text));
  paragraph(row, event.source_quote);
  paragraph(row, `记录药物：${event.medications.join('、')}`);
  if (event.procedures.length) paragraph(row, `其他处理：${event.procedures.join('、')}`);
  if (event.dose !== null) paragraph(row, `剂量：${event.dose} ${event.dose_unit ?? '单位未提供'}；频次：${event.frequency ?? '未提供'}`);
  if (event.note) paragraph(row, event.note, 'muted');
  therapy.append(row);
});
const activity = section('disease-activity', '疾病活动度随访');
paragraph(activity, 'SJADAS、SMS、AJC、ESR 按原表转录；评分版本和 ESR 列单位未提供。', 'muted');
table(activity, ['日期', ...data.disease_activity.columns, '记录背景', '备注'], data.disease_activity.rows.map(row => [row.date, row.sjadas, row.sms, row.ajc, row.physician_global, row.parent_patient_global, row.esr, row.context, row.note]));
sourceLink(activity, data.disease_activity.source_id);
const followups = section('followups', '随访与治疗反应');
const scoreRowsByDate = new Map(data.disease_activity.rows.map(row => [row.date, row]));
const laboratoryGroups = new Map([...data.measurement_panels, ...data.microbiology].map(group => [group.id, group]));
paragraph(followups, '明确日期的评估与日期未提供的复查分别保留，全部结果引用原始资料。', 'muted');
data.followup_records.forEach(record => {
  const block = element('article'); block.dataset.followupId = record.id;
  block.append(element('h3', record.label));
  paragraph(block, record.date ?? times.get(record.time_ref), 'muted');
  record.outcomes.forEach(outcome => paragraph(block, `${outcome.name}：${outcome.status}`));
  if (record.score_row_date) {
    const score = scoreRowsByDate.get(record.score_row_date);
    paragraph(block, `SJADAS：${score.sjadas}；SMS：${score.sms}；AJC：${score.ajc}；医生主观评分：${score.physician_global}；家长/患者主观评分：${score.parent_patient_global}；ESR：${score.esr}`);
    if (score.note) paragraph(block, score.note);
  }
  record.laboratory_refs.forEach(id => {
    const group = laboratoryGroups.get(id);
    if (group.items) group.items.forEach(item => paragraph(block, `${item.name}：${item.raw_text}`));
    if (group.results) group.results.forEach(result => paragraph(block, result.raw_text));
  });
  if (record.kind === 'latest_followup') data.followup.current_medication_assertions.forEach(medication => {
    paragraph(block, `${medication.name}：${medication.dose === null ? '剂量未提供' : `${medication.dose} ${medication.unit}`}；频次：${medication.frequency ?? '未提供'}`);
  });
  sourceLink(block, record.source_id); followups.append(block);
});
const differential = section('differentials', '原文鉴别诊断');
data.differential_diagnoses.forEach(item => { paragraph(differential, `${item.name}：${item.source_status}`); });
const issues = section('data-issues', '资料待确认事项');
data.data_issues.forEach(issue => { const row = element('article', undefined, 'issue'); paragraph(row, issue.description); paragraph(row, issue.handling, 'muted'); issues.append(row); });
const original = section('original', '完整来源');
const details = element('details'); details.append(element('summary', '展开病例原文'));
const sourceResponse = await fetch(sources.get('case-text').url);
if (!sourceResponse.ok) throw new Error(`病例原文读取失败：${sourceResponse.status}`);
details.append(element('pre', await sourceResponse.text())); original.append(details);
content.dataset.loaded = 'true';
