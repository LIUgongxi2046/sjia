import { useLayoutEffect, useRef } from 'react';
import { Link, Outlet, useLocation, useNavigate, useNavigationType } from 'react-router';
import { readReturnContext } from '../components/ContextLink';
import { navigationMemory } from '../state/navigation';
import { useAccess } from '../state/access';
import { House, ClipboardList, UserRound, ChevronLeft } from '../components/ThreeDIcon';

const tabs = [
  { to: '/home', label: '首页', icon: House },
  { to: '/records', label: '记录', icon: ClipboardList },
  { to: '/account', label: '我的', icon: UserRound },
];

export function Layout() {
  const location = useLocation();
  const navigationType = useNavigationType();
  const mainRef = useRef<HTMLElement>(null);
  const previousPath = useRef<string>();
  const isRecordPage = ['/records', '/summaries', '/drafts'].some(path => location.pathname === path || location.pathname.startsWith(`${path}/`));
  const isAccountPage = ['/account', '/associations', '/login'].some(path => location.pathname === path || location.pathname.startsWith(`${path}/`));
  const currentSection = isRecordPage ? '/records' : isAccountPage ? '/account' : '/home';
  const showNavigation = tabs.some(tab => tab.to === location.pathname);

  useLayoutEffect(() => {
    if (previousPath.current !== location.pathname || navigationType === 'POP') {
      const position = navigationType === 'POP' ? navigationMemory.readScroll(location.key) : 0;
      window.scrollTo({ top: position, behavior: 'instant' });
      mainRef.current?.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true });
    }
    previousPath.current = location.pathname;
    const remember = () => navigationMemory.rememberScroll(location.key, window.scrollY);
    window.addEventListener('scroll', remember, { passive: true });
    return () => window.removeEventListener('scroll', remember);
  }, [location.key, location.pathname, navigationType]);

  return (
    <div className={`app-shell${showNavigation ? ' has-navigation' : ''}`}>
      <a className="skip-link" href="#main-content">跳转到主要内容</a>
      <main id="main-content" ref={mainRef} className="main-content" tabIndex={-1}>
        <Outlet />
      </main>
      {showNavigation && <nav className="tab-bar" aria-label="主要导航">
        {tabs.map(({ to, label, icon: Icon }) => (
          <Link key={to} to={to} className={`tab-link${currentSection === to ? ' is-active' : ''}`} aria-current={currentSection === to ? (location.pathname === to ? 'page' : 'location') : undefined}>
            <Icon size={23} strokeWidth={1.8} aria-hidden="true" />
            <span>{label}</span>
          </Link>
        ))}
      </nav>}
    </div>
  );
}

export function PatientContext() {
  const { patientId, relations, loading } = useAccess();
  const { pathname } = useLocation();
  const showPatient = ['/records', '/summaries', '/drafts', '/register', '/alerts'].some(path => pathname === path || pathname.startsWith(`${path}/`));
  if (!showPatient) return null;
  return <div className="patient-context" aria-label="当前资料归属"><div className="patient-identity">
    <span className="patient-symbol"><UserRound size={26} /></span>
    <strong>{loading ? '正在读取患儿关联' : relations.find(relation => relation.patient_id === patientId)?.display_id ?? '尚未选择患儿'}</strong>
  </div><span className="inline-label">家庭记录</span></div>;
}

export function BackLink({ to, label = '返回' }: { to: string; label?: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const origin = readReturnContext(location.state);
  return <Link className="back-link" to={origin ? `${origin.pathname}${origin.search}` : to} onClick={event => {
    if (!origin || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigationMemory.rememberScroll(origin.key, origin.scroll);
    navigate(-1);
  }} aria-label={label}><ChevronLeft size={28} aria-hidden="true" /><span className="visually-hidden">{label}</span></Link>;
}
