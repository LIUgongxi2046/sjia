import { useEffect, useId, useState } from 'react';
import type { ReactNode } from 'react';
import { Camera, ChevronRight, Maximize2, Pill, Thermometer } from './ThreeDIcon';
import child from '../../../ui-delivery/identity/master/toddler-boy-autumn.png';
import symptomIllustration from '../../../ui-delivery/assets/master/symptom-trio.png';

export interface HomeObservation { label: string; value: string; time?: string; context?: string; notice?: string; disabled?: boolean }
/** Pure visual components shared by the real permission-bound home and DEV specimens.
 * Values always come from the caller; no clinical interpretation or sample data here. */
export function HomeCompanion({ name, description, observations, patientPicker, onArchive, onObserve, onViewObservation, companionImage, children }: {
  name: string; description?: string; observations: HomeObservation[]; patientPicker?: ReactNode;
  onArchive: () => void; onObserve?: () => void; onViewObservation?: (index: number) => void;
  companionImage?: { src: string; label: string }; children?: ReactNode;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => setImageFailed(false), [companionImage?.src]);
  const [symptomImageFailed, setSymptomImageFailed] = useState(false);
  const observationId = useId();
  const firstObservation = observations[0];
  // Group only identical, supplied display strings across every shown card.
  // This does not combine records, infer chronology, or total pending counts.
  const sharedTime = observations.length > 1 && firstObservation.time && firstObservation.time !== '时间待补充'
    && observations.every(item => !item.disabled && item.time === firstObservation.time && item.context === firstObservation.context)
    ? firstObservation : null;
  const pendingNotices = observations.map((item, index) => ({ ...item, index })).filter(item => item.notice);
  const descriptionFor = (index: number) => [observations[index] && sharedTime && `${observationId}-time`,
    observations[index]?.notice && `${observationId}-notice-${index}`].filter(Boolean).join(' ') || undefined;
  return <section className="home-twin" aria-label="孩子的记录">
    <div className="home-twin-heading">
      <details className="home-patient"><summary><span className="home-patient-copy"><strong>{name}</strong>{description && <span>{description}</span>}</span><ChevronRight size={14} /></summary>{patientPicker}</details>
      <button onClick={onArchive}>健康档案<Maximize2 size={14} /></button>
    </div>
    <div className="home-child-stage"><div className="home-child-view">
      {!imageFailed ? <img key={companionImage?.src ?? child} src={companionImage?.src ?? child} alt="通用儿童插画；标记只表示资料分类，不代表实际受累位置" onError={() => setImageFailed(true)} /> : <button className="text-action" onClick={() => setImageFailed(false)}>重新加载形象</button>}
      {['fever', 'rash', 'joint'].map((kind, index) => <button key={kind} className={`symptom-pin ${kind}`} aria-label={`查看${observations[index]?.label ?? '症状'}记录入口，身体位置仅为示意`} aria-describedby={descriptionFor(index)} disabled={observations[index]?.disabled} onClick={() => onViewObservation ? onViewObservation(index) : onObserve?.()}><span>{index + 1}</span></button>)}
    </div></div>
    <div className="symptom-compact">
      {!symptomImageFailed ? <img src={symptomIllustration} alt="体温、皮疹、关节症状示意，不代表孩子的实际表现" onError={() => setSymptomImageFailed(true)} /> : <button className="text-action" onClick={() => setSymptomImageFailed(false)}>重新加载症状示意图</button>}
      {sharedTime && <div className="home-observation-time" id={`${observationId}-time`}>
        <span>{observations.map(item => item.label).join('、')}：{sharedTime.context ?? '记录时间'}</span>
        <span>{sharedTime.time}</span>
      </div>}
      <div className="symptom-columns">{observations.map((item, index) => <button key={item.label} aria-describedby={descriptionFor(index)} disabled={item.disabled} onClick={() => onViewObservation ? onViewObservation(index) : onObserve?.()}>
        <strong><span>{index + 1}</span>{item.label}</strong>
        {!sharedTime && item.context && <small className="home-observation-context">{item.context}</small>}
        <small className="home-observation-value">{item.value}</small>
        {!sharedTime && item.time && <small className="home-observation-card-time">{item.time}</small>}
      </button>)}</div>
      {pendingNotices.length > 0 && <details className="home-observation-notices">
        <summary><span>时间待确认<span className="home-observation-notice-scope">涉及：{pendingNotices.map(item => item.label).join('、')}</span></span><ChevronRight size={14} /></summary>
        <dl>{pendingNotices.map(item => <div key={item.index} id={`${observationId}-notice-${item.index}`}><dt>{item.label}</dt><dd>{item.notice}</dd></div>)}</dl>
      </details>}
    </div>{children}
  </section>;
}
export function HomeQuickActions({ onObserve, onReports, onTherapies }: { onObserve: () => void; onReports: () => void; onTherapies: () => void }) {
  return <section className="home-entry-grid" aria-label="快捷记录">
    <button onClick={onObserve}><Thermometer size={22} /><strong>记录症状</strong></button>
    <button onClick={onReports}><Camera size={22} /><strong>上传报告</strong></button>
    <button onClick={onTherapies}><Pill size={22} /><strong>治疗记录</strong></button>
  </section>;
}
