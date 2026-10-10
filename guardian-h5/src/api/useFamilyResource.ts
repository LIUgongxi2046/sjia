import { useCallback, useEffect, useState } from 'react';
import { fetchJson } from './client';
import type { validateFamilyRead } from './familyReadSchema';

export function useFamilyResource<T>(path: string | null, schema: Parameters<typeof validateFamilyRead>[0]) {
  const [resource, setResource] = useState<{ path: string | null; schema: string; data: T | null; error: Error | null; loading: boolean }>(
    { path, schema, data: null, error: null, loading: !!path });
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => {
    setResource({ path, schema, data: null, error: null, loading: !!path });
    setVersion(value => value + 1);
  }, [path, schema]);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ path, schema, data: null, error: null, loading: !!path });
    if (path) void fetchJson<unknown>(path, { signal: controller.signal }).then(async value => {
      const { validateFamilyRead } = await import('./familyReadSchema');
      const result = validateFamilyRead<T>(schema, value);
      if (!controller.signal.aborted) setResource({ path, schema, data: result, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setResource({ path, schema, data: null, error: failure, loading: false });
    });
    return () => controller.abort();
  }, [path, schema, version]);
  const current = resource.path === path && resource.schema === schema;
  return { data: current ? resource.data : null, error: current ? resource.error : null, loading: current ? resource.loading : !!path, reload };
}
