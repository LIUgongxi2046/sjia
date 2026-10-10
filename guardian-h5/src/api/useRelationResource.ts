import { useCallback, useEffect, useState } from 'react';
import { fetchJson } from './client';
import type { RelationReadSchema } from './relationReadSchema';

export function useRelationResource<T>(path: string | null, principalId: string | null,
  schema: RelationReadSchema, expectedId: string | null = null) {
  const key = JSON.stringify([path, principalId, schema, expectedId]);
  const [resource, setResource] = useState<{ key: string; data: T | null; error: Error | null; loading: boolean }>(
    { key, data: null, error: null, loading: !!path });
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => {
    setResource({ key, data: null, error: null, loading: !!path });
    setVersion(value => value + 1);
  }, [key, path]);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ key, data: null, error: null, loading: !!path });
    if (path && principalId) void fetchJson<unknown>(path, { signal: controller.signal }).then(async value => {
      const { validateRelationRead } = await import('./relationReadSchema');
      const result = validateRelationRead<T>(schema, value);
      if (expectedId) {
        const object = result as { relation_id?: string; application_id?: string };
        const receivedId = schema === 'RelationView' ? object.relation_id : object.application_id;
        if (receivedId !== expectedId) throw new Error('服务返回资料与当前关联或申请对象不一致。');
      }
      if (!controller.signal.aborted) setResource({ key, data: result, error: null, loading: false });
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) setResource({ key, data: null, error: failure, loading: false });
    });
    return () => controller.abort();
  }, [key, path, principalId, schema, expectedId, version]);
  const current = resource.key === key;
  return { data: current ? resource.data : null, error: current ? resource.error : null,
    loading: current ? resource.loading : !!path, reload };
}
