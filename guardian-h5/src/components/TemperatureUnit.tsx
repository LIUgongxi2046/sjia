/** Presentation only: never infer a unit for missing source data or a new entry. */
export function TemperatureUnit({ rawUnit, selection }: { rawUnit?: string | null; selection?: string }) {
  const label = selection === undefined ? rawUnit || '未知'
    : selection === '摄氏度（°C）' ? '°C'
      : selection === '华氏度（°F）' ? '°F'
        : selection === '无法确定' ? '未知' : '待选单位';
  return <span>{label}</span>;
}
