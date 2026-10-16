import type { AnalyteTrend } from '../api/endpoints';

/** Build only explicitly approved adjacent links. Raw values remain in the DTO. */
export function analyteComparisonGroups(trend: AnalyteTrend['trend']) {
  const groups = new Map<string, { unit: string; method: string; mapped: boolean; values: Array<number | null>; links: boolean[] }>();
  trend.forEach((item, index) => {
    const comparison = item.comparison;
    if (!item.direction || !comparison || index < 1 || comparison.previous_field_id !== trend[index - 1].field_id) return;
    const previous = Number(comparison.previous_value), current = Number(comparison.current_value);
    if (!Number.isFinite(previous) || !Number.isFinite(current)) return;
    const key = JSON.stringify([comparison.kind, comparison.unit, comparison.method, comparison.config_id, comparison.config_checksum]);
    let group = groups.get(key);
    if (!group) {
      group = { unit: comparison.unit, method: comparison.method, mapped: comparison.kind === 'approved_mapping', values: trend.map(() => null), links: trend.map(() => false) };
      groups.set(key, group);
    }
    if (group.values[index - 1] !== null && group.values[index - 1] !== previous) return;
    group.values[index - 1] = previous;
    group.values[index] = current;
    group.links[index] = true;
  });
  return [...groups.entries()];
}
