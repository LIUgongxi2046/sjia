import { useId } from 'react';
import type { RichThemePreference as Preference } from '../state/richTheme';

export function RichThemePreference({ value, onChange }: {
  value: Preference; onChange: (value: string) => void;
}) {
  const id = useId();
  return <div className="rich-theme-preference">
    <label htmlFor={id}>外观</label>
    <select id={id} value={value} onChange={event => onChange(event.target.value)}>
      <option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option>
    </select>
  </div>;
}
