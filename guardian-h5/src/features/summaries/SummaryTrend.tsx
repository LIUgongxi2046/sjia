import Decimal from 'decimal.js';
import type { SummaryTrendSeries, SummaryView } from '../../api/summaries';
import { fieldLabel } from '../../api/records';
import { medicalSourceTimeLabel } from '../../components/recordTimePresentation';
import { ContextLink } from '../../components/ContextLink';

const reasons: Record<string, string> = {
  UNPOSITIONED_TIME_GAP: '存在无法定位的医学时间，不能连接前后结果',
};
/** Coordinates are only a display of the backend's immutable raw-value projection. */
export function summaryTrendCoordinates(series: SummaryTrendSeries) {
  const points = series.points.filter(point => point.plottable);
  if (!points.length || points.some(point => !point.raw_unit) || new Set(points.map(point => point.raw_unit)).size !== 1) return null;
  try {
    const values = points.map(point => new Decimal(point.raw_value!));
    const times = points.map(point => Date.parse(point.plot_at!));
    if (values.some(value => !value.isFinite() || value.abs().greaterThan(Number.MAX_SAFE_INTEGER) || value.sd() > 15) || times.some(time => !Number.isFinite(time))) return null;
    const min = Decimal.min(...values), max = Decimal.max(...values), range = max.minus(min);
    const first = Math.min(...times), last = Math.max(...times);
    const coords = points.map((point, index) => ({ fieldId: point.field_id,
      x: first === last ? 150 : 24 + (times[index] - first) / (last - first) * 252,
      y: range.isZero() ? 90 : 150 - values[index].minus(min).div(range).times(112).toNumber(),
    }));
    return coords.every(point => Number.isFinite(point.x) && Number.isFinite(point.y)) ? coords : null;
  } catch { return null; }
}

export function SummaryTrend({ summary }: { summary: SummaryView }) {
  if (!summary.trend_projection) return <p className="rich-summary-time">本摘要未保存可用的趋势依据，按原始项目阅读。</p>;
  const items = new Map(summary.items.filter(item => item.snapshot.kind === 'observation').map(item => [item.field_id, item]));
  const series = summary.trend_projection.series.filter(group => group.points.some(point => items.has(point.field_id)));
  if (!series.length) return <p className="rich-summary-time">本摘要没有可展示的体温数值点。</p>;
  return <>{series.map((group, index) => {
    const points = group.points.filter(point => items.has(point.field_id));
    const filtered = { ...group, points };
    const coords = summaryTrendCoordinates(filtered);
    const coordinateMap = new Map(coords?.map(point => [point.fieldId, point]));
    const lines = group.segments.filter(segment => segment.connect && coordinateMap.has(segment.from_field_id) && coordinateMap.has(segment.to_field_id));
    const firstItem = points.length ? items.get(points[0].field_id) : undefined;
    const timedPoints = points.filter(point => point.plottable && point.plot_at && Number.isFinite(Date.parse(point.plot_at)))
      .sort((left, right) => Date.parse(left.plot_at!) - Date.parse(right.plot_at!));
    const mixedUnits = new Set(points.filter(point => point.plottable).map(point => point.raw_unit)).size > 1;
    const timeEndpoints = timedPoints.length ? [timedPoints[0], timedPoints[timedPoints.length - 1]] : [];
    return <div className="rich-summary-trend" key={`${group.field_code}:${index}`}>
      <h4>{firstItem ? fieldLabel(firstItem.snapshot.field) : '原始数值'} · 保存时的记录</h4>
      {coords && <svg viewBox="0 0 300 180" role="img" aria-label={`${firstItem ? fieldLabel(firstItem.snapshot.field) : '原始数值'}原值点图，连接仅按保存时可比较依据；完整数值与时间见下方表格`}>
        <line x1="20" y1="160" x2="280" y2="160" className="summary-axis" />
        {lines.map(segment => { const from = coordinateMap.get(segment.from_field_id)!, to = coordinateMap.get(segment.to_field_id)!;
          return <line key={`${segment.from_field_id}:${segment.to_field_id}`} data-summary-segment="connected" x1={from.x} y1={from.y} x2={to.x} y2={to.y} className="summary-line" />;
        })}
        {coords.map(point => {
          const source = points.find(value => value.field_id === point.fieldId)!;
          const label = `${source.raw_value}${source.raw_unit ? ` ${source.raw_unit}` : ''}`;
          return <g key={point.fieldId}><circle data-summary-point={point.fieldId} cx={point.x} cy={point.y} r="4" className="summary-point" />
            {label.length <= 14 && coords.length <= 6 && <text x={point.x} y={point.y - 12} textAnchor={point.x < 45 ? 'start' : point.x > 255 ? 'end' : 'middle'}>{label}</text>}
          </g>;
        })}
      </svg>}
      {coords && <div className="rich-summary-trend-dates" aria-label="图中医学时间范围">{timeEndpoints.map((point, endpoint) => <span key={`${point.field_id}:${endpoint}`}>
        {endpoint === 0 ? '起点' : '终点'}：{medicalSourceTimeLabel(items.get(point.field_id)!.snapshot.field.medical_time)}
      </span>)}</div>}
      <p className="rich-summary-time">{!coords ? mixedUnits ? '原始单位不同，不能共用数值坐标；请按原值与单位分别阅读。' : '数值、单位或时间无法安全绘图，请阅读下方原值。' : lines.length ? '只连接保存时具备比较依据的相邻结果；仅描述原值，不作临床判断。' : '可比较资料不足，保留原值点，不连接为趋势。'}</p>
      <details open={!coords}><summary>完整数值、医学时间与绘图依据</summary>
        {points.map(point => { const item = items.get(point.field_id)!;
          return <div className="rich-summary-trend-row" key={point.field_id}>
            <strong>{({ lt: '<', le: '≤', gt: '>', ge: '≥' } as Record<string, string>)[item.snapshot.field.qualifier ?? ''] ?? ''}{point.raw_value ?? '未知'}{point.raw_unit ? ` ${point.raw_unit}` : '（单位未知）'}</strong>
            <p>{medicalSourceTimeLabel(item.snapshot.field.medical_time)}</p>
            <ContextLink className="text-action" to={`/records/versions/${point.record_version_id}`}>查看固定依据版本</ContextLink>
            {point.reason_codes.length > 0 && <p>该点未具备完整绘图依据。</p>}
          </div>;
        })}
        {group.segments.some(segment => !segment.connect) && <p>部分相邻结果未连接：时间、原值、方法、单位或来源依据不足。</p>}
        <details><summary>技术依据</summary><p>快照校验：{summary.trend_projection!.snapshot_sha256}</p>
          {[...new Set([...points.flatMap(point => point.reason_codes), ...group.segments.flatMap(segment => segment.reason_codes)])].map(code => <p key={code}>{reasons[code] ?? code}</p>)}
        </details>
      </details>
    </div>;
  })}</>;
}
