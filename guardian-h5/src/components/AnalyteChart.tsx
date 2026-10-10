import { useEffect, useRef, useState } from 'react';
import Chart from 'chart.js/auto';
import type { AnalyteTrend } from '../api/endpoints';
import { originLabels, timeLabel } from '../api/records';

export function AnalyteChart({ trend, ui2 = false }: { trend: AnalyteTrend['trend']; ui2?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const groups = [...new Map(trend.filter(item => item.direction).map(item => [JSON.stringify([item.raw_unit, item.method]), { unit: item.raw_unit, method: item.method }])).entries()];
  const [selected, setSelected] = useState(groups[0]?.[0] ?? '');
  const group = groups.find(([key]) => key === selected)?.[1];
  useEffect(() => {
    if (!canvas.current || !group) return;
    const eligible = (index: number) => {
      const item = trend[index];
      return item.raw_unit === group.unit && item.method === group.method && item.qualifier === 'eq' && item.value_state === 'known' && item.raw_numeric !== null && Number.isFinite(Number(item.raw_numeric)) && Boolean(item.direction || trend[index + 1]?.direction);
    };
    const styles = getComputedStyle(canvas.current);
    const color = styles.getPropertyValue(ui2 ? '--link' : '--accent-text').trim();
    const text = styles.getPropertyValue(ui2 ? '--ink' : '--text-secondary').trim();
    if (!color || !text) throw new Error('检验趋势缺少当前页面的颜色 Token。');
    const chart = new Chart(canvas.current, {
      type: 'line',
      data: {
        labels: trend.map(item => timeLabel(item.medical_time)),
        datasets: [{
          label: group.unit ?? '', data: trend.map((item, index) => eligible(index) ? Number(item.raw_numeric) : null),
          borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 4, pointHitRadius: 14,
          spanGaps: false, tension: 0,
          segment: { borderColor: context => trend[context.p1DataIndex].direction ? color : 'transparent' },
        }],
      },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'nearest', intersect: true },
        scales: { x: { ticks: { color: text, maxTicksLimit: 4 }, grid: { display: false } }, y: { ticks: { color: text }, title: { display: true, text: group.unit ?? '', color: text } } },
        plugins: { legend: { display: false }, tooltip: { callbacks: {
          label: context => {
            const item = trend[context.dataIndex];
            return [`原值：${item.raw_numeric} ${item.raw_unit}`, `来源：${originLabels[item.origin_type] ?? item.origin_type}`, `机构：${item.source_organization || '未提供'}`, `核对：${item.verification_states.length ? item.verification_states.map(state => ({ verified: '已核对', pending: '待核对', revoked: '已撤销', needs_correction: '需补正', unverified: '未核对' }[state.status] ?? state.status)).join('、') : '未核对'}`];
          },
        } } },
      },
    });
    return () => chart.destroy();
  }, [trend, selected, group?.unit, group?.method, ui2]);

  if (!groups.length) return <p role="status">可比较资料不足，尚未形成趋势图。</p>;
  return <>
    {groups.length > 1 && <div className="field"><label htmlFor="trend-group">比较条件</label><select id="trend-group" value={selected} onChange={event => setSelected(event.target.value)}>{groups.map(([key, value]) => <option key={key} value={key}>{value.unit} · {value.method}</option>)}</select></div>}
    <p className="field-hint">{group?.unit} · {group?.method}。横轴按结果顺序排列，时间间隔以原始记录为准。点击数据点查看来源。</p>
    <div className="analyte-chart"><canvas ref={canvas} role="img" aria-label="检验趋势图，完整原值与来源见历次结果列表" /></div>
  </>;
}
