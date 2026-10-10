import type { DocumentView } from '../../../../frontend-design/v2-types';
import { timeLabel } from '../../api/records';

export function documentTimeLabel(value: DocumentView['documented_time']) {
  return timeLabel(value === null ? null : { ...value, local_year: value.local_year ?? null, local_month: value.local_month ?? null,
    end_year: value.end_year ?? null, end_month: value.end_month ?? null, relative_text: value.relative_text ?? null });
}
