import { useMemo } from 'react';
import type { ComponentProps } from 'react';
import { Link, useLocation, useResolvedPath } from 'react-router';
import { navigationMemory } from '../state/navigation';

export interface ReturnContext {
  pathname: string;
  search: string;
  key: string;
  scroll: number;
}

export function ContextLink(props: ComponentProps<typeof Link>) {
  const location = useLocation();
  const target = useResolvedPath(props.to);
  const returnContextKey = useMemo(() => crypto.randomUUID(), [location.key, target.pathname, target.search]);
  const returnContext: ReturnContext | undefined = location.pathname === '/prototype' ? undefined : {
    pathname: location.pathname,
    search: location.search,
    key: location.key,
    scroll: window.scrollY,
  };
  return <Link {...props} state={returnContext ? { returnContextKey } : undefined} onClick={event => {
    props.onClick?.(event);
    if (!returnContext || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || props.target === '_blank') return;
    const root = event.currentTarget.getRootNode();
    const scroll = root instanceof ShadowRoot ? root.querySelector('.phone-scroll')?.scrollTop : window.scrollY;
    if (scroll === undefined) throw new Error('当前页面缺少返回位置来源。');
    navigationMemory.setReturnContext(returnContextKey, { ...returnContext, scroll });
  }} />;
}

export function readReturnContext(state: unknown): ReturnContext | undefined {
  if (!state || typeof state !== 'object' || !('returnContextKey' in state) || typeof state.returnContextKey !== 'string') return undefined;
  return navigationMemory.readReturnContext(state.returnContextKey);
}
