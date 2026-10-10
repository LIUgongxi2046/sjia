import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { Button } from 'antd-mobile';
import { ChevronRight, LockKeyhole, type IconComponent } from './ThreeDIcon';
import { ContextLink } from './ContextLink';
import { BackLink, PatientContext } from '../app/Layout';
import { TaskHeader } from './TaskHeader';

export interface PageLink {
  to: string;
  title: string;
  description?: string;
  icon: IconComponent;
  tone?: 'blue' | 'orange' | 'purple' | 'teal';
}

export function LinkGroup({ links, label }: { links: PageLink[]; label: string }) {
  return (
    <section aria-label={label} className="link-group">
      {links.map(({ to, title, description, icon: Icon, tone = 'blue' }) => (
        <ContextLink key={to} to={to} className="list-link">
          <span className={`list-icon tone-${tone}`}><Icon size={21} strokeWidth={1.9} aria-hidden="true" /></span>
          <span className="list-copy"><strong>{title}</strong>{description && <small>{description}</small>}</span>
          <ChevronRight size={18} className="list-chevron" aria-hidden="true" />
        </ContextLink>
      ))}
    </section>
  );
}

interface PageFrameProps {
  screenId: string;
  title: string;
  subtitle: string;
  backTo?: string;
  headingHidden?: boolean;
  children: ReactNode;
}

export function PageFrame({ screenId, title, subtitle, backTo, children }: PageFrameProps) {
  useEffect(() => { document.title = `${title} · sJIA 守护者联盟`; }, [title]);
  return (
    <article data-screen-id={screenId} className="page-content">
      <TaskHeader title={title} subtitle={screenId === 'S03' ? undefined : subtitle} kicker={screenId === 'S03' ? subtitle : '家庭记录'} leading={backTo && <BackLink to={backTo} />} />
      <PatientContext />
      {children}
    </article>
  );
}

export function AccessState({ title, description, actionLabel, children, icon: Icon = LockKeyhole }: {
  title: string;
  description: string;
  actionLabel?: string;
  children?: ReactNode;
  icon?: IconComponent;
}) {
  return (
    <section className="state-card" aria-label={title}>
      <span className="state-icon"><Icon size={56} aria-hidden="true" /></span>
      <h2>{title}</h2>
      <p>{description}</p>
      {actionLabel && <Button color="primary" block disabled>{actionLabel}</Button>}
      {children}
    </section>
  );
}
