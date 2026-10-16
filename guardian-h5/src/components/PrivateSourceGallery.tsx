import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { ApiError, fetchPrivateImage } from '../api/client';
import { useAccess } from '../state/access';
import { SourceGallery } from './SourceGallery';
import type { SourcePhoto } from './PhotoReview';
import { ChevronRight, Image } from './ThreeDIcon';

export function PrivateSourceGallery({ files, triggerClassName, triggerContent }: { files: readonly { file_id: string; ordinal: number; checksum?: string; content_path?: string | null }[]; triggerClassName?: string; triggerContent?: ReactNode }) {
  const access = useAccess();
  const [enabled, setEnabled] = useState(false);
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [photos, setPhotos] = useState<{ sourceKey: string; values: SourcePhoto[] } | null>(null);
  const [error, setError] = useState<{ key: string; value: Error } | null>(null);
  const [loading, setLoading] = useState(false);
  const [version, setVersion] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false), retrying = useRef(false), mounted = useRef(true);
  const denied = useRef<{ key: string; value: Error } | null>(null);
  const sourceKey = JSON.stringify([access.principalId, access.patientId, access.scopeVersion, files]);
  const currentKey = useRef(sourceKey); currentKey.current = sourceKey;
  const authorized = !!access.principalId && !!access.patientId && !access.loading && !access.error
    && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  const ready = enabled && visible && authorized;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useLayoutEffect(() => {
    if (!enabled && restoreFocus.current) { trigger.current?.focus(); restoreFocus.current = false; }
  }, [enabled]);
  useEffect(() => {
    const changed = () => { setVisible(document.visibilityState === 'visible'); };
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    const urls: string[] = [];
    const current = () => !controller.signal.aborted && currentKey.current === sourceKey && document.visibilityState === 'visible'
      && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
    const denial = denied.current?.key === sourceKey ? denied.current : null;
    setPhotos(null); setError(denial); setLoading(ready && !denial);
    if (ready && !denial && current()) void Promise.all(files.map(async file => {
      const path = file.content_path === undefined ? `/api/files/${file.file_id}/content` : file.content_path;
      if (typeof path !== 'string' || !path.startsWith('/api/') || path.includes('?') || path.includes('#') || path.includes('://')) throw new Error('原件读取地址无效。');
      const blob = await fetchPrivateImage(path, controller.signal);
      if (!current()) return null;
      if (file.checksum) {
        const data = await blob.arrayBuffer();
        if (!current()) return null;
        const digest = await crypto.subtle.digest('SHA-256', data);
        if (!current()) return null;
        const checksum = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
        if (checksum !== file.checksum) throw new Error('原件内容与已读取版本的校验值不一致，本次原件未被采用。');
      }
      if (!current()) return null;
      const url = URL.createObjectURL(blob); urls.push(url);
      return { id: file.file_id, url, label: `附图顺序 ${file.ordinal}` };
    })).then(values => {
      if (!current()) return;
      if (values.some(value => value === null)) throw new Error('原件读取已经取消。');
      setPhotos({ sourceKey, values: values as SourcePhoto[] }); setLoading(false);
    }).then(undefined, failure => {
      if (!current()) return;
      if (!(failure instanceof Error)) throw failure;
      controller.abort(); urls.forEach(url => URL.revokeObjectURL(url));
      const next = { key: sourceKey, value: failure };
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) denied.current = next;
      setPhotos(null); setError(next); setLoading(false);
    });
    return () => { controller.abort(); urls.forEach(url => URL.revokeObjectURL(url)); };
  }, [ready, sourceKey, version, access.hasCurrentAccess]);
  async function retry() {
    if (retrying.current || !authorized) return;
    retrying.current = true;
    try {
      // Backend denial is terminal until the user explicitly revalidates it.
      if (denied.current?.key === sourceKey) await access.refresh();
      if (!mounted.current || currentKey.current !== sourceKey || !access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion)) return;
      denied.current = null; setError(null); setVersion(value => value + 1);
    } finally { retrying.current = false; }
  }
  const currentError = error?.key === sourceKey ? error.value : null;
  if (!files.length) return <p>本份资料尚未附原图。</p>;
  return <>
    {!enabled && <button ref={trigger} className={triggerClassName ?? 'list-row'} disabled={!authorized} onClick={() => setEnabled(true)}>{triggerContent ?? <><span className="row-icon"><Image size={30} /></span><span className="row-text"><strong>查看原始资料</strong><small>每项内容都保留原文出处</small></span><ChevronRight size={17} /></>}</button>}
    {enabled && loading && <p role="status">正在读取获准原件</p>}
    {enabled && currentError && <section className="status-panel error" role="alert"><h2>原件读取未完成</h2><p>{currentError.message}</p><button className="text-action" disabled={!authorized} onClick={() => void retry()}>重新读取原件</button></section>}
    {ready && photos?.sourceKey === sourceKey && <SourceGallery photos={photos.values} ui2 autoOpen onClose={() => { restoreFocus.current = true; setEnabled(false); }} />}
  </>;
}
