import { useId, useState } from 'react';
import { Disclosure } from './ClinicalUI';

export interface DictionaryOption { id: string; label: string; aliases: readonly string[] }
type DictionarySource = { status: 'unavailable' } | {
  status: 'ready';
  version: string;
  options: readonly DictionaryOption[];
  selectedId: string | null;
  onSelect: (id: string | null, version: string) => void;
};

export function DictionaryField({ label, source = { status: 'unavailable' } }: { label: string; source?: DictionarySource }) {
  const id = useId();
  const [query, setQuery] = useState('');
  const normalized = query.trim().toLocaleLowerCase();
  if (source.status === 'ready' && !source.version) throw new Error('项目字典缺少获准版本');
  const options = source.status === 'ready' ? source.options.filter(option => [option.label, ...option.aliases].some(value => value.toLocaleLowerCase().includes(normalized))) : [];
  return <Disclosure title={label}>
    <div className="field"><label htmlFor={id}>查找{label}</label><input id={id} type="search" autoComplete="off" value={query} onChange={event => setQuery(event.target.value)} disabled={source.status === 'unavailable'} aria-describedby={`${id}-status`} placeholder="输入名称或报告上的写法" /></div>
    <p id={`${id}-status`} role="status">{source.status === 'unavailable' ? '获准清单尚未接入，原文保持不变。' : options.length ? `找到 ${options.length} 个项目` : '没有匹配项目，请保留原文并标记待确认。'}</p>
    {source.status === 'ready' && <>
      <div className="dictionary-options" role="group" aria-label={`${label}结果`}>{options.map(option => <button className="text-action" key={option.id} type="button" aria-pressed={source.selectedId === option.id} onClick={() => source.onSelect(option.id, source.version)}>{option.label}</button>)}</div>
      <button className="text-action" type="button" onClick={() => source.onSelect(null, source.version)}>保留原文，暂不选择</button>
    </>}
  </Disclosure>;
}
