import type { ReportTimeCandidate } from '../api/endpoints';
import { editableMedicalTime, type TimeInput } from './medicalTimeValue';

/** Review-only candidate; no state changes until the user explicitly adopts it. */
export function ReportTimeSuggestion({ label, candidate, onAdopt }: {
  label: string; candidate: ReportTimeCandidate | null | undefined; onAdopt: (value: TimeInput) => void;
}) {
  if (!candidate) return null;
  return <section className="status-panel" aria-label={`${label}识别候选`}>
    <p>{label}候选原文：{candidate.raw_text}</p>
    <p>精度：{candidate.precision}；时区：{candidate.timezone_name ?? '未提供'}；
      UTC 偏移（分钟）：{candidate.utc_offset_minutes ?? '未提供'}</p>
    <small>请对照原件确认。这项候选不会自动应用到其他项目。</small>
    <button type="button" className="text-action" onClick={() => onAdopt(editableMedicalTime(candidate))}>
      采用{label}候选</button>
  </section>;
}
