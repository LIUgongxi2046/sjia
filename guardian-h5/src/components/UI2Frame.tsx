import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { CSSProperties, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { MoreHorizontal } from 'lucide-react';
import { ChevronLeft as ArrowLeft, X, Bell, CircleHelp, ClipboardList, House, UserRound } from './ThreeDIcon';
import sourceStyles from '../../../ui-delivery/v2/src/styles.css?inline';
import tokens from '../../../ui-delivery/v2/tokens.json';
import richStyles from '../styles/rich-ui.css?inline';
import richPageStyles from '../styles/rich-pages.css?inline';
import richRegistrationStyles from '../styles/rich-registration.css?inline';
import richThemeStyles from '../styles/rich-theme.css?inline';
import { useRichTheme } from '../state/richTheme';
import { richPageSubtitles } from './richPagePresentation';

const designStyle: CSSProperties = {
  fontFamily: tokens.font.family, color: tokens.color.text.primary,
  '--font': tokens.font.family, '--ink': tokens.color.text.primary, '--navy': tokens.color.brand.primary,
  '--blue': tokens.color.action.primary, '--link': tokens.color.action.text, '--canvas': tokens.color.surface.base,
  '--radius': `${tokens.radius.lg}px`, '--shadow': `${tokens.shadow.sm},inset 0 1px #fff`,
  '--motion': `${tokens.motion.fast}ms`,
} as CSSProperties;

export function UI2Frame({ title, screenId, note, onBack, children, audience = 'support', primary,
  onNavigate, scrollKey, scrollPosition = 0, onScroll, overlay, heading, kicker, subtitle }: {
  title: string; screenId: string; note: string; onBack: () => void; children: ReactNode;
  audience?: 'family' | 'support'; primary?: 'home' | 'records' | 'account';
  heading?: string; kicker?: string | null; subtitle?: string | null;
  onNavigate?: (path: string) => void; scrollKey?: string; scrollPosition?: number; onScroll?: (position: number) => void;
  overlay?: ReactNode;
}) {
  const { preference, theme } = useRichTheme();
  const host = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [sheet, setSheet] = useState(false);
  const [root, setRoot] = useState<ShadowRoot | null>(null);
  useLayoutEffect(() => {
    if (!host.current) throw new Error('页面容器尚未建立。');
    setRoot(host.current.shadowRoot ?? host.current.attachShadow({ mode: 'open' }));
    document.title = `${title} · sJIA 守护者联盟`;
  }, [title]);
  useLayoutEffect(() => {
    if (!root || !scrollKey || !content.current) return;
    content.current.scrollTop = scrollPosition;
    root.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true });
  }, [root, scrollKey, scrollPosition]);
  useEffect(() => {
    if (!sheet || !panel.current) return;
    const previous = root?.activeElement as HTMLElement | null;
    const current = panel.current;
    const background = [...(root?.querySelectorAll<HTMLElement>('.phone-scroll, .bottom-nav') ?? [])];
    background.forEach(node => node.setAttribute('inert', ''));
    const controls = [...current.querySelectorAll<HTMLButtonElement>('button')];
    if (!controls.length) throw new Error('页面说明缺少关闭操作。');
    controls[0].focus();
    function handleKey(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') { setSheet(false); event.preventDefault(); }
      if (event.key === 'Tab') {
        const first = controls[0], last = controls[controls.length - 1];
        if (event.shiftKey && root?.activeElement === first) { last.focus(); event.preventDefault(); }
        if (!event.shiftKey && root?.activeElement === last) { first.focus(); event.preventDefault(); }
      }
    }
    current.addEventListener('keydown', handleKey);
    return () => {
      current.removeEventListener('keydown', handleKey);
      background.forEach(node => node.removeAttribute('inert'));
      previous?.focus();
    };
  }, [root, sheet]);
  function navigate(path: string) {
    if (!onNavigate) throw new Error('当前页面缺少导航处理。');
    onNavigate(path);
  }
  return <div ref={host} style={designStyle} data-screen-id={screenId} data-theme={theme} data-theme-preference={preference}>
    {root && createPortal(<><style>{sourceStyles}{richStyles}{richPageStyles}{richRegistrationStyles}{richThemeStyles}</style><div className="studio solo">
      <main className="canvas"><div className="device" data-theme={theme} data-primary={primary} data-audience={audience} data-page={screenId}><div ref={content} className="phone-scroll" onScroll={event => onScroll?.(event.currentTarget.scrollTop)}>
        <header className={`app-header${primary ? ' root-header' : ''}`}>
          {kicker !== null && <span className="header-kicker">{kicker ?? (primary === 'home' ? new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(new Date()) : audience === 'support' ? '授权工作空间' : '家庭记录')}</span>}
          <div className="app-title-row">
            {!primary && <button className="header-icon" aria-label="返回上级页面" onClick={onBack}><ArrowLeft size={22} /></button>}
            <div><h1 tabIndex={-1}>{heading ?? (primary === 'home' ? '陪伴孩子，健康成长' : title)}</h1>
              {primary !== 'home' && subtitle !== null && <p className="header-subtitle">{subtitle ?? richPageSubtitles[screenId] ?? (audience === 'support' ? '按当前岗位与授权范围操作' : '保留准确的资料')}</p>}</div>
            {primary === 'home' && <button className="header-icon" aria-label="查看预警记录" onClick={() => navigate('/alerts')}><Bell size={22} /></button>}
          </div>
          {primary !== 'home' && <button className="header-help" aria-label="页面说明" onClick={() => setSheet(true)}><MoreHorizontal size={20} /></button>}
        </header><div className="page-content" style={{ overflowWrap: 'anywhere' }}>{children}</div>
      </div>{primary && <nav className="bottom-nav" aria-label="家庭主导航">
        {([{ key: 'home', label: '首页', Icon: House }, { key: 'records', label: '记录', Icon: ClipboardList },
          { key: 'account', label: '我的', Icon: UserRound }] as const).map(({ key, label, Icon }) =>
          <button key={key} aria-current={primary === key ? 'page' : undefined} onClick={() => navigate(`/${key}`)}><Icon size={28} /><span>{label}</span></button>)}
      </nav>}{sheet && <div className="sheet-backdrop" onClick={() => setSheet(false)}>
        <section ref={panel} className="sheet" role="dialog" aria-modal="true" aria-label="页面说明" onClick={event => event.stopPropagation()}>
          <button className="sheet-close" aria-label="关闭说明" onClick={() => setSheet(false)}><X size={20} /></button>
          <span className="sheet-handle" /><CircleHelp size={40} /><h2>{title}</h2><p>{note}</p>
          <button className="button secondary" onClick={() => setSheet(false)}>继续查看</button>
        </section>
      </div>}{overlay}</div></main>
    </div></>, root)}
  </div>;
}

export function UI2Sheet({ title, onClose, children, variant }: { title: string; onClose: () => void; children: ReactNode; variant?: 'archive' }) {
  const host = useRef<HTMLSpanElement>(null);
  const panel = useRef<HTMLElement>(null);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!host.current) throw new Error('弹层入口尚未建立。');
    const root = host.current.getRootNode();
    if (!(root instanceof ShadowRoot)) throw new Error('弹层缺少家庭样式容器。');
    const device = root.querySelector<HTMLElement>('.device');
    if (!device) throw new Error('弹层缺少页面容器。');
    setContainer(device);
  }, []);
  useEffect(() => {
    if (!container) return;
    const current = panel.current;
    if (!current) throw new Error('弹层容器尚未建立。');
    const rootNode = current.getRootNode();
    if (!(rootNode instanceof ShadowRoot)) throw new Error('弹层缺少家庭样式容器。');
    const root: ShadowRoot = rootNode;
    const previous = root.activeElement as HTMLElement | null;
    const background = [...root.querySelectorAll<HTMLElement>('.phone-scroll, .bottom-nav')];
    background.forEach(node => node.setAttribute('inert', ''));
    function controls() {
      return [...current!.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]')]
        .filter(node => node.getClientRects().length > 0);
    }
    const first = controls()[0];
    if (!first) throw new Error('弹层缺少可访问控件。');
    first.focus();
    function handleKey(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (event.key === 'Escape') { event.preventDefault(); onClose(); }
      if (event.key === 'Tab') {
        const values = controls(), first = values[0], last = values[values.length - 1];
        if (!first || !last) throw new Error('弹层缺少可访问控件。');
        if (event.shiftKey && root.activeElement === first) { event.preventDefault(); last.focus(); }
        if (!event.shiftKey && root.activeElement === last) { event.preventDefault(); first.focus(); }
      }
    }
    current.addEventListener('keydown', handleKey);
    return () => {
      current.removeEventListener('keydown', handleKey);
      background.forEach(node => node.removeAttribute('inert'));
      if (previous?.isConnected) previous.focus();
    };
  }, [container, onClose]);
  return <><span ref={host} hidden />{container && createPortal(<div className={`sheet-backdrop${variant === 'archive' ? ' archive-backdrop' : ''}`} onClick={onClose}><section ref={panel} className={`sheet${variant === 'archive' ? ' sheet-archive' : ''}`} role="dialog" aria-modal="true" aria-label={title} onClick={event => event.stopPropagation()}>
    <button className="sheet-close" aria-label={`关闭${title}`} onClick={onClose}><X size={20} /></button><span className="sheet-handle" />
    <h2 className={variant === 'archive' ? 'archive-sheet-title' : undefined}>{title}</h2>{children}
  </section></div>, container)}</>;
}
