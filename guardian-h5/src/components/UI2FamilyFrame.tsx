import type { ReactNode } from 'react';
import { useLocation, useNavigate, useNavigationType } from 'react-router';
import { navigationMemory } from '../state/navigation';
import { readReturnContext } from './ContextLink';
import { UI2Frame } from './UI2Frame';

export function UI2FamilyFrame({ title, screenId, note, backTo, primary, children, overlay, kicker, subtitle }: {
  title: string; screenId: string; note: string; backTo: string; children: ReactNode;
  primary?: 'home' | 'records' | 'account';
  overlay?: ReactNode; kicker?: string | null; subtitle?: string | null;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const navigationType = useNavigationType();
  const origin = readReturnContext(location.state);
  return <UI2Frame title={title} screenId={screenId} note={note} audience="family" primary={primary} overlay={overlay} kicker={kicker} subtitle={subtitle}
    scrollKey={location.key} scrollPosition={navigationType === 'POP' ? navigationMemory.readScroll(location.key) : 0}
    onScroll={position => navigationMemory.rememberScroll(location.key, position)}
    onNavigate={path => navigate(path)} onBack={() => {
      if (origin) { navigationMemory.rememberScroll(origin.key, origin.scroll); navigate(-1); }
      else navigate(backTo);
    }}>{children}</UI2Frame>;
}
