import { useEffect, useRef, useState } from 'react';
import { fetchJson } from './client';
import { validateLegacyResource } from './legacyResponseSchema';

interface ResourceAccess {
  key: string;
  enabled: boolean;
  isCurrent: () => boolean;
}

/** Public/support reads retain their existing behavior; clinical callers opt in. */
export function useResource<T>(path: string | null, access?: ResourceAccess) {
  const key = access?.key ?? '';
  const enabled = access?.enabled ?? true;
  const guard = useRef(access?.isCurrent);
  guard.current = access?.isCurrent;
  const [resource, setResource] = useState<{ path: string | null; key: string; data: T | null; error: string | null; loading: boolean }>({ path, key, data: null, error: null, loading: Boolean(path && enabled) });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const capturedGuard = guard.current;
    const isCurrent = () => !controller.signal.aborted && (!capturedGuard || capturedGuard());
    setResource({ path, key, data: null, error: null, loading: Boolean(path && enabled) });
    if (path && enabled && isCurrent()) void fetchJson<unknown>(path, { signal: controller.signal, isCurrent }).then(value => validateLegacyResource<T>(path, value)).then(value => {
      if (isCurrent()) setResource({ path, key, data: value, error: null, loading: false });
    }).then(undefined, failure => {
      if (!isCurrent()) return;
      if (!(failure instanceof Error)) throw failure;
      setResource({ path, key, data: null, error: failure.message, loading: false });
    });
    return () => controller.abort();
  }, [path, key, enabled, version]);
  const current = resource.path === path && resource.key === key && enabled && (!access || access.isCurrent());
  return { data: current ? resource.data : null, error: current ? resource.error : null, loading: current ? resource.loading : Boolean(path && enabled), reload: () => setVersion(value => value + 1) };
}
