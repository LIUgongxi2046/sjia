import { useId } from 'react';
import type { RichThemePreference as Preference } from '../state/richTheme';

export function RichThemePreference({ value, onChange }: {
  value: Preference; onChange: (value: string) => void;
}) {
  const id = useId();
  return <fieldset className="rich-theme-preference">
    <legend>外观设置</legend>
    <p>选择浅色、深色，或跟随设备设置。</p>
    <div className="rich-theme-options">{([
      ['system', '跟随系统'], ['light', '浅色'], ['dark', '深色'],
    ] as const).map(([preference, label]) => <label className="rich-theme-choice" key={preference}>
      <input type="radio" name={id} value={preference} checked={value === preference}
        onChange={event => onChange(event.target.value)} />
      <span>{label}</span>
    </label>)}</div>
  </fieldset>;
}
