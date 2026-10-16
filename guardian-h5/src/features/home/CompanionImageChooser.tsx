import { useRef, useState } from 'react';
import { companionImages, useCompanionPreference, type CompanionImage } from '../../state/companionPreference';

export function CompanionArtwork({ image, className }: { image: CompanionImage; className?: string }) {
  const [failedSource, setFailedSource] = useState<string | null>(null);
  return failedSource !== image.src ? <img className={className} src={image.src} alt={`${image.label}；仅为示意，不代表患儿身份或健康情况`} onError={() => setFailedSource(image.src)} />
    : <div className="companion-image-error"><p>形象暂时无法显示</p><button type="button" className="text-action" onClick={() => setFailedSource(null)}>重新加载示意形象</button></div>;
}

/** Inline chooser lives inside the archive's existing accessible modal. */
export function CompanionImageChooser() {
  const { image, choose } = useCompanionPreference();
  const [draft, setDraft] = useState<string | null>(null);
  const [message, setMessage] = useState('');
  const trigger = useRef<HTMLButtonElement>(null);
  function close() { setDraft(null); trigger.current?.focus(); }
  return <section className="companion-preference" aria-label="示意形象设置">
    <div className="companion-preference-heading"><span>示意形象</span><button ref={trigger} type="button" className="text-action" aria-expanded={draft !== null} aria-controls="companion-options" aria-label={`选择形象，当前${image.label}`} onClick={() => { setDraft(image.id); setMessage(''); }}>选择形象</button></div>
    {draft !== null && <div id="companion-options" className="companion-options" onKeyDownCapture={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); } }}>
      <p className="section-note">当前：{image.label}</p>
      <fieldset><legend>可选示意形象</legend><div className="companion-image-grid">{companionImages.map(option => <label key={option.id} className={draft === option.id ? 'selected' : ''}>
        <input type="radio" name="companion-image" value={option.id} checked={draft === option.id} onChange={() => setDraft(option.id)} />
        <img src={option.src} alt="" loading="lazy" /><span>{option.label}</span>
      </label>)}</div></fieldset>
      <div className="companion-choice-actions"><button type="button" className="button secondary" onClick={close}>取消选择</button><button type="button" className="button" onClick={() => {
        const result = choose(draft); setMessage(result.persisted ? '形象已更新' : '已应用；浏览器未允许保存，关闭页面后可能恢复默认。'); close();
      }}>应用形象</button></div>
    </div>}
    {message && <p role="status">{message}</p>}
  </section>;
}
