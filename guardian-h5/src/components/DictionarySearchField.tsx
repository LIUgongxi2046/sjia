import { useEffect, useRef, useState } from 'react';
import { fetchJson } from '../api/client';
import { validateApiValue } from '../api/schema';
import { Disclosure } from './ClinicalUI';

interface Option { id: string; concept_code: string; label: string; aliases: string[] }
interface DictionaryView { version: string; match_state: 'none' | 'unique' | 'ambiguous'; options: Option[]; next_cursor: string | null }
export interface DictionarySelection { id: string; concept_code: string; version: string }

export function DictionarySearchField({ category, disabled, selected, onSelect }: {
  category: string; disabled: boolean; selected: DictionarySelection | null;
  onSelect: (value: DictionarySelection | null) => void;
}) {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<DictionaryView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController>();
  useEffect(() => {
    if (disabled) { controller.current?.abort(); setResult(null); setError(null); setLoading(false); }
    return () => controller.current?.abort();
  }, [disabled, category]);
  async function search(cursor?: string) {
    if (disabled || loading || !query.trim()) return;
    controller.current?.abort(); controller.current = new AbortController();
    const signal = controller.current.signal; setLoading(true); setError(null);
    await fetchJson<DictionaryView>('/api/dictionaries/search', { method: 'POST',
      body: { category, query, limit: 20, ...(cursor ? { cursor } : {}) }, signal }).then(value => {
      validateApiValue('protocol.schema.json#/$defs/DictionaryView', value);
      if (signal.aborted) return;
      if (cursor && value.version !== result?.version) throw new Error('字典版本已经变化，请重新查询');
      setResult(cursor ? { ...value, options: [...result!.options, ...value.options] } : value); setLoading(false);
    }, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!signal.aborted) { setResult(null); setError(failure.message); setLoading(false); }
    });
  }
  return <Disclosure title="确认项目身份">
    <label className="field"><span>查找获准检验项目</span><input type="search" value={query} disabled={disabled || loading}
      onChange={event => { setQuery(event.target.value); setResult(null); setError(null); }} autoComplete="off" /></label>
    <button type="button" className="text-action" disabled={disabled || loading || !query.trim()} onClick={() => void search()}>{loading ? '正在查询' : '查询获准字典'}</button>
    {error && <p role="alert">{error}</p>}
    {result && <><p role="status">{result.match_state === 'none' ? '没有匹配项目，原文继续保留。' : result.match_state === 'unique' ? '找到唯一项目，请本人确认。' : '存在多个匹配项目，请核对定义后本人选择。'}</p>
      <div className="dictionary-options" role="group" aria-label="获准字典结果">{result.options.map(option => <button type="button" className="text-action" key={option.id} disabled={disabled || loading}
        aria-pressed={selected?.id === option.id && selected.version === result.version}
        onClick={() => onSelect({ id: option.id, concept_code: option.concept_code, version: result.version })}>{option.label}{option.aliases.length > 0 && <small> · {option.aliases.join('、')}</small>}</button>)}</div>
      {result.next_cursor && <button type="button" className="text-action" disabled={disabled || loading} onClick={() => void search(result.next_cursor!)}>继续读取项目</button>}</>}
    {selected && <p role="status">已选择项目：{selected.concept_code} · 字典版本 {selected.version}</p>}
    <button type="button" className="text-action" disabled={disabled || loading} onClick={() => onSelect(null)}>保留原文，暂不选择</button>
  </Disclosure>;
}
