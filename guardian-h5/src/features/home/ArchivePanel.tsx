import { useState, type ReactNode } from 'react';
import { fieldLabel, fieldValue, originLabels, verificationLabel, type RecordItem } from '../../api/records';
import { ContextLink } from '../../components/ContextLink';
import { Thermometer, Activity, FileText, Pill, ChevronRight } from '../../components/ThreeDIcon';
import { medicalSourceTimeLabel } from '../../components/recordTimePresentation';
import { navigationMemory } from '../../state/navigation';
import { useCompanionPreference } from '../../state/companionPreference';
import { CompanionArtwork, CompanionImageChooser } from './CompanionImageChooser';
import { archiveEntries, archiveRecordCount, selectedArchiveEntries, type ArchiveCategory, type ArchiveEntry } from './archiveProjection';
import { recordSnapshotPath, selectLatestMedicalEntries } from './observationSelection';
import symptomIllustration from '../../../../ui-delivery/assets/master/symptom-trio.png';
import archiveStyles from '../../styles/rich-archive.css?inline';

const categories = [
  { id: 'temperature', title: '体温记录', icon: Thermometer, entry: '/register/observations' },
  { id: 'symptoms', title: '症状观察', icon: Activity, entry: '/register/observations' },
  { id: 'reports', title: '检验资料', icon: FileText, entry: '/register/reports' },
  { id: 'therapies', title: '治疗资料', icon: Pill, entry: '/register/therapies' },
] as const;
const symptomCodes = ['temp', 'rash', 'joint_discomfort'];
const symptomLabels = ['体温', '皮疹观察', '关节不适观察'];
const symptomTitles = ['体温', '皮疹', '关节表现'];
function entryPath({ record, field }: ArchiveEntry) {
  return recordSnapshotPath({ recordId: record.record_id, recordVersionId: record.detail.record_version_id, fieldId: field.field_id });
}
function summary(entries: ArchiveEntry[]) {
  const selected = selectLatestMedicalEntries(entries);
  if (!entries.length) return '尚无记录';
  if (selected.state === 'single' || selected.state === 'latest') return fieldValue(selected.entries[0].field);
  return selected.state === 'same_time' ? `${selected.entries.length} 条同一时段记录` : `${selected.entries.length} 条记录，先后待核对`;
}

/** Data-only presentation. The production parent owns all access checks and reads.
 * DEV specimens may supply explicit synthetic records without granting any access. */
export function ArchivePanel({ records, status, patientLabel, badgeLabel = '按当前权限读取', canWrite = false, onViewRecord, readState }: {
  records: RecordItem[] | null; status: string | null; patientLabel: string; badgeLabel?: string;
  canWrite?: boolean; onViewRecord: (entry: ArchiveEntry) => void; readState?: ReactNode;
}) {
  const { image } = useCompanionPreference();
  const [selected, setSelected] = useState(navigationMemory.archive.category);
  const [time, setTime] = useState(navigationMemory.archive.time);
  const [showHistory, setShowHistory] = useState(time !== 'latest');
  const [symptomImageFailed, setSymptomImageFailed] = useState(false);
  const entries = records ? archiveEntries(records) : [];
  const times = [...new Map(entries.filter(({ field }) => field.medical_time_id && field.medical_time && field.medical_time.precision !== 'unknown')
    .map(({ field }) => [field.medical_time_id!, medicalSourceTimeLabel(field.medical_time)])).entries()];
  const category = categories.find(item => item.id === selected);
  function change(value: string) { navigationMemory.archive.category = value; setSelected(value); }
  function changeTime(value: string) { navigationMemory.archive.time = value; setTime(value); }
  function view(entries: ArchiveEntry[], category: ArchiveCategory) {
    if (!records || status) return;
    const latest = selectLatestMedicalEntries(entries);
    if (latest.state === 'single' || latest.state === 'latest') onViewRecord(latest.entries[0]);
    else change(category);
  }
  const timeControl = <div className="archive-time-control">
    <div className="segments" role="group" aria-label="档案时间范围"><button type="button" aria-pressed={!showHistory} className={!showHistory ? 'selected' : ''} onClick={() => { setShowHistory(false); changeTime('latest'); }}>最近资料</button><button type="button" aria-pressed={showHistory} className={showHistory ? 'selected' : ''} onClick={() => setShowHistory(true)}>历史时间</button></div>
    {showHistory && <label className="field"><span>查看记录时间</span><select value={time} onChange={event => changeTime(event.target.value)}><option value="latest">各类最近可排序记录</option>{times.map(([value, label]) => <option value={value} key={value}>{label}</option>)}{!['latest', 'unknown'].includes(time) && !times.some(([value]) => value === time) && <option value={time}>原选择时间当前不可读取</option>}<option value="unknown">时间待补充</option></select></label>}
    <p className="section-note">{time === 'latest' ? '按各项原始医学时间分别展示；先后无法确定时保留全部候选。' : time === 'unknown' ? '仅显示没有明确医学时间的资料。' : '查看所选医学时间的已有记录。'}记录不代表当前病情。</p>
  </div>;
  const selectedEntries = (category?: ArchiveCategory) => selectedArchiveEntries(category ? archiveEntries(records ?? [], category) : entries, time);
  const patientInitial = !patientLabel.trim() || ['孩子的记录', '当前患儿'].includes(patientLabel) ? '？' : Array.from(patientLabel.trim())[0];
  const shownObservations = symptomCodes.flatMap(code => selectedArchiveEntries(entries.filter(entry => entry.record.kind === 'observation' && entry.field.field_code === code), time));
  return <div className="health-archive" aria-label="健康档案分类资料"><style>{archiveStyles}</style>
    <div className="archive-patient"><span className="archive-patient-initial" aria-hidden="true">{patientInitial}</span><strong>{patientLabel}</strong><span className="badge">{badgeLabel}</span></div>
    {readState}
    {category ? <><button type="button" className="text-action" onClick={() => change('')}>返回档案分类</button>{timeControl}<section className="card form-card"><h3>{category.title}</h3>
      {records && !selectedEntries(category.id).length && <p>所选范围暂无本类资料</p>}
      {records && selectedEntries(category.id).map(entry => <article key={entry.field.field_id} className="report-item"><h3>{fieldLabel(entry.field)}：{fieldValue(entry.field)}</h3><p>{medicalSourceTimeLabel(entry.field.medical_time)}</p><p>{originLabels[entry.record.detail.origin_type] ?? entry.record.detail.origin_type} · {verificationLabel(entry.field)}</p><ContextLink className="text-action" to={entryPath(entry)}>查看完整记录</ContextLink></article>)}
      {canWrite && <ContextLink className="text-action" to={category.entry}>登记{category.title}</ContextLink>}
    </section></> : <>
      <div className="archive-child-stage"><div className="archive-child-view"><CompanionArtwork image={image} />{symptomCodes.map((code, index) => <button type="button" key={code} className={`symptom-pin ${['fever', 'rash', 'joint'][index]}`} disabled={!!status} aria-label={`查看${symptomLabels[index]}记录，身体位置仅为示意`} onClick={() => view(selectedArchiveEntries(entries.filter(entry => entry.record.kind === 'observation' && entry.field.field_code === code), time), index === 0 ? 'temperature' : 'symptoms')}><span>{index + 1}</span></button>)}</div>
        <div className="archive-summary-grid">{categories.map(({ id, title, icon: Icon }) => {
          const scoped = time === 'latest' ? archiveEntries(records ?? [], id) : selectedEntries(id);
          const value = status ?? (id === 'temperature' ? summary(scoped) : `${archiveRecordCount(scoped)} 份记录`);
          return <button type="button" key={id} className={`archive-summary ${id}`} onClick={() => change(id)}><Icon size={19} /><strong>{title}</strong><span>{value}</span></button>;
        })}</div>
      </div>
      <div className="archive-symptom-trio">{!symptomImageFailed ? <img src={symptomIllustration} alt="体温、皮疹、关节症状分类示意，不代表实际表现" onError={() => setSymptomImageFailed(true)} /> : <button type="button" className="text-action" onClick={() => setSymptomImageFailed(false)}>重新加载症状示意图</button>}
        <div className="archive-symptom-cards">{symptomCodes.map((code, index) => {
          const matching = selectedArchiveEntries(entries.filter(entry => entry.record.kind === 'observation' && entry.field.field_code === code), time);
          const chosen = selectLatestMedicalEntries(matching), one = chosen.state === 'single' || chosen.state === 'latest' ? chosen.entries[0] : null;
          return <button type="button" key={code} disabled={!!status} onClick={() => view(matching, index === 0 ? 'temperature' : 'symptoms')}><strong><span>{index + 1}</span>{symptomTitles[index]}</strong><b>{status ?? summary(matching)}</b>{one && <small>{verificationLabel(one.field)}</small>}<ChevronRight size={14} /></button>;
        })}</div>
      </div>
      <p className="section-note archive-illustration-note">部位仅为示意，不代表实际受累位置；观察记录不等于诊断。</p>
      <CompanionImageChooser />{timeControl}
      <details className="archive-source-details"><summary>记录时间、来源与核对</summary>
        <p className="section-note">份数仅统计当前可读记录，同一份记录的多个字段只计一次。</p>
        {shownObservations.length === 0 && <p className="section-note">{status ?? '所选范围暂无症状记录'}</p>}
        {shownObservations.map(entry => <article className="archive-source-row" key={entry.field.field_id}>
          <strong>{fieldLabel(entry.field)}：{fieldValue(entry.field)}</strong><p>{medicalSourceTimeLabel(entry.field.medical_time)}</p>
          <p>{originLabels[entry.record.detail.origin_type] ?? entry.record.detail.origin_type} · {verificationLabel(entry.field)}</p>
          <ContextLink className="text-action" to={entryPath(entry)}>查看完整记录</ContextLink>
        </article>)}
      </details>
    </>}
  </div>;
}
