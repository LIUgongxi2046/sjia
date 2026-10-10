import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ArchiveSearchItem } from '../../../../frontend-design/archive-search-types';
import { ApiError, fetchPrivateDocument } from '../../api/client';
import { UI2Sheet } from '../../components/UI2Frame';
import { SourceGallery } from '../../components/SourceGallery';
import { useAccess } from '../../state/access';

export function ArchiveOriginals({ pages }: { pages: ArchiveSearchItem['pages'] }) {
  const access = useAccess();
  const [open, setOpen] = useState(false), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [files, setFiles] = useState<{ id: string; url: string; label: string; mediaType: string; text: string | null }[]>([]);
  const [loading, setLoading] = useState(false), [error, setError] = useState<Error | null>(null), [attempt, setAttempt] = useState(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => { setOpen(false); trigger.current?.focus(); }, []);
  const key = JSON.stringify(pages);
  const ready = open && visible && !!access.principalId && !!access.patientId && !access.loading && !access.error;
  useEffect(() => { const change = () => { const active = document.visibilityState === 'visible'; setVisible(active); if (!active) setOpen(false); };
    document.addEventListener('visibilitychange', change); return () => document.removeEventListener('visibilitychange', change); }, []);
  useEffect(() => {
    const controller = new AbortController(), urls: string[] = [];
    setFiles([]); setError(null); setLoading(ready);
    if (ready) void Promise.all(pages.map(async page => {
      if (page.status !== 'available') throw new Error('当前原件尚不能读取。');
      const blob = await fetchPrivateDocument(`/api/files/${encodeURIComponent(page.file_id)}/content`, page.media_type, controller.signal);
      const hash = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
      if (Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join('') !== page.checksum)
        throw new Error('当前原件内容与文书保存的准确摘要不一致。');
      if (controller.signal.aborted) return null;
      const url = URL.createObjectURL(blob); urls.push(url);
      return { id: page.page_id, url, mediaType: page.media_type, label: `文书页序 ${page.page_order}`, text: page.media_type === 'text/plain' ? new TextDecoder('utf-8', { fatal: true }).decode(await blob.arrayBuffer()) : null };
    })).then(values => {
      if (controller.signal.aborted) return;
      if (values.some(value => value === null)) throw new Error('原件读取已经取消。');
      setFiles(values as typeof files); setLoading(false);
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) {
        controller.abort(); urls.forEach(url => URL.revokeObjectURL(url)); setFiles([]); setLoading(false); setError(failure);
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) void access.refresh();
      }
    });
    return () => { controller.abort(); urls.forEach(url => URL.revokeObjectURL(url)); };
  }, [ready, key, attempt, access.refresh]);
  const images = files.filter(file => file.mediaType.startsWith('image/'));
  const root = ready ? trigger.current?.getRootNode() : null;
  const container = root instanceof ShadowRoot ? root.querySelector('.device') : null;
  if (ready && !container) throw new Error('原件弹层缺少当前页面容器。');
  return <><button ref={trigger} className="button secondary" disabled={!access.principalId || !access.patientId || access.loading || !!access.error} onClick={() => setOpen(true)}>查看原件</button>
    {ready && container && createPortal(<UI2Sheet title="原件与页序" onClose={close}>
      {loading && <p role="status">正在读取获准原件</p>}
      {error && <section className="status-panel error" role="alert"><p>{error.message}</p><button className="text-action" onClick={() => setAttempt(value => value + 1)}>重新读取原件</button></section>}
      {!!images.length && <SourceGallery ui2 photos={images} />}
      {files.filter(file => file.mediaType === 'application/pdf').map(file => <iframe key={file.id} title={`PDF ${file.label}`} sandbox="" src={file.url} />)}
      {files.filter(file => file.mediaType === 'text/plain').map(file => <pre key={file.id} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{file.text}</pre>)}
      {pages.map(page => <p key={page.page_id}>文书页序 {page.page_order}；原件版本 {page.object_version}；状态 {page.status}。</p>)}
    </UI2Sheet>, container)}
  </>;
}
