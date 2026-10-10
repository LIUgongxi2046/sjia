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
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(false);
  const [version, setVersion] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const ready = enabled && visible && !!access.principalId && !!access.patientId && !access.loading && !access.error;
  const sourceKey = JSON.stringify(files);
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
    setPhotos(null); setError(null); setLoading(ready);
    if (ready) void Promise.all(files.map(async file => {
      const path = file.content_path === undefined ? `/api/files/${file.file_id}/content` : file.content_path;
      if (typeof path !== 'string' || !path.startsWith('/api/') || path.includes('?') || path.includes('#') || path.includes('://')) throw new Error('原件读取地址无效。');
      const blob = await fetchPrivateImage(path, controller.signal);
      if (file.checksum) {
        const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
        const checksum = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
        if (checksum !== file.checksum) throw new Error('原件内容与已读取版本的校验值不一致，本次原件未被采用。');
      }
      if (controller.signal.aborted) return null;
      const url = URL.createObjectURL(blob); urls.push(url);
      return { id: file.file_id, url, label: `附图顺序 ${file.ordinal}` };
    })).then(values => {
      if (controller.signal.aborted) return;
      if (values.some(value => value === null)) throw new Error('原件读取已经取消。');
      setPhotos({ sourceKey, values: values as SourcePhoto[] }); setLoading(false);
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) {
        controller.abort(); urls.forEach(url => URL.revokeObjectURL(url));
        setPhotos(null); setError(failure); setLoading(false);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) void access.refresh();
      }
    });
    return () => { controller.abort(); urls.forEach(url => URL.revokeObjectURL(url)); };
  }, [ready, sourceKey, version, access.refresh]);
  if (!files.length) return <p>本份资料尚未附原图。</p>;
  return <>
    {!enabled && <button ref={trigger} className={triggerClassName ?? 'list-row'} disabled={!access.principalId || !access.patientId || access.loading || !!access.error} onClick={() => setEnabled(true)}>{triggerContent ?? <><span className="row-icon"><Image size={30} /></span><span className="row-text"><strong>查看原始资料</strong><small>每项内容都保留原文出处</small></span><ChevronRight size={17} /></>}</button>}
    {enabled && loading && <p role="status">正在读取获准原件</p>}
    {enabled && error && <section className="status-panel error" role="alert"><h2>原件读取未完成</h2><p>{error.message}</p><button className="text-action" onClick={() => setVersion(value => value + 1)}>重新读取原件</button></section>}
    {ready && photos?.sourceKey === sourceKey && <SourceGallery photos={photos.values} ui2 autoOpen onClose={() => { restoreFocus.current = true; setEnabled(false); }} />}
  </>;
}
