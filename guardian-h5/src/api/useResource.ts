import { useEffect, useState } from 'react';
import { fetchJson } from './client';

export function useResource<T>(path: string | null) {
  const [resource, setResource] = useState<{ path: string | null; data: T | null; error: string | null; loading: boolean }>({ path, data: null, error: null, loading: Boolean(path) });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ path, data: null, error: null, loading: Boolean(path) });
    if (path) void fetchJson<T>(path, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) setResource({ path, data: value, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setResource({ path, data: null, error: failure.message, loading: false });
    });
    return () => controller.abort();
  }, [path, version]);
  const current = resource.path === path;
  return { data: current ? resource.data : null, error: current ? resource.error : null, loading: current ? resource.loading : Boolean(path), reload: () => setVersion(value => value + 1) };
}
