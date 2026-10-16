import { useEffect, useRef } from 'react';
import { ApiError } from '../api/client';
import { useAccess } from './access';

/** Endpoint denial is local: refreshing authentication in its catch can loop. */
export function useEndpointAccess(resourceKey = '') {
  const access = useAccess();
  const mounted = useRef(true), blocked = useRef(false), checking = useRef(false);
  const key = useRef(resourceKey);
  if (key.current !== resourceKey) { key.current = resourceKey; blocked.current = false; checking.current = false; }
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  function authorized(signal?: AbortSignal) {
    return mounted.current && key.current === resourceKey && !signal?.aborted && document.visibilityState === 'visible'
      && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  }
  function current(signal?: AbortSignal) { return !blocked.current && !checking.current && authorized(signal); }
  function guard(signal?: AbortSignal) {
    if (!current(signal)) throw new DOMException('当前访问权限或页面范围已改变。', 'AbortError');
  }
  function deny(failure: unknown) {
    if (!(failure instanceof ApiError) || ![401, 403].includes(failure.status)) return false;
    blocked.current = true;
    return true;
  }
  async function revalidate(reload: () => void) {
    if (checking.current || !mounted.current) return;
    checking.current = true;
    try {
      await access.refresh();
      // Account/patient/scope changes remount their own clean content.
      if (!authorized()) return;
      blocked.current = false; checking.current = false;
      reload();
    } finally { if (key.current === resourceKey) checking.current = false; }
  }
  return { current, guard, deny, revalidate, blocked };
}
