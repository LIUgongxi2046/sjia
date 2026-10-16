import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ArchiveSearchItem } from '../../../../frontend-design/archive-search-types';
import { ApiError, fetchPrivateDocument } from '../../api/client';
import { UI2Sheet } from '../../components/UI2Frame';
import { SourceGallery } from '../../components/SourceGallery';
import { useAccess } from '../../state/access';

type OriginalFile = { id: string; url: string; label: string; mediaType: string; text: string | null };

export function ArchiveOriginals({ pages }: { pages: ArchiveSearchItem['pages'] }) {
  const access = useAccess();
  const [open, setOpen] = useState(false), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [resource, setResource] = useState<{ key: string; files: OriginalFile[] } | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState<{ key: string; value: Error } | null>(null), [attempt, setAttempt] = useState(0);
  const denied = useRef<{ key: string; value: Error } | null>(null), retrying = useRef(false), mounted = useRef(true);
  const trigger = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => { setOpen(false); trigger.current?.focus(); }, []);
  const key = JSON.stringify([access.principalId, access.patientId, access.scopeVersion, pages]);
  const currentKey = useRef(key); currentKey.current = key;
  const authorized = !!access.principalId && !!access.patientId && !access.loading && !access.error
    && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  const ready = open && visible && authorized;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { const change = () => { const active = document.visibilityState === 'visible'; setVisible(active); if (!active) setOpen(false); };
    document.addEventListener('visibilitychange', change); return () => document.removeEventListener('visibilitychange', change); }, []);
  useEffect(() => {
    const controller = new AbortController(), urls: string[] = [];
    const current = () => !controller.signal.aborted && currentKey.current === key && document.visibilityState === 'visible'
      && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
    const denial = denied.current?.key === key ? denied.current : null;
    setResource(null); setError(denial); setLoading(ready && !denial);
    if (ready && !denial && current()) void Promise.all(pages.map(async page => {
      if (page.status !== 'available') throw new Error('当前原件尚不能读取。');
      const blob = await fetchPrivateDocument(`/api/files/${encodeURIComponent(page.file_id)}/content`, page.media_type, controller.signal);
      if (!current()) return null;
      const data = await blob.arrayBuffer();
      if (!current()) return null;
      const hash = await crypto.subtle.digest('SHA-256', data);
      if (!current()) return null;
      if (Array.from(new Uint8Array(hash), value => value.toString(16).padStart(2, '0')).join('') !== page.checksum)
        throw new Error('当前原件内容与文书保存的准确摘要不一致。');
      const text = page.media_type === 'text/plain' ? new TextDecoder('utf-8', { fatal: true }).decode(data) : null;
      const url = URL.createObjectURL(blob); urls.push(url);
      return { id: page.page_id, url, mediaType: page.media_type, label: `文书页序 ${page.page_order}`, text };
    })).then(values => {
      if (!current()) return;
      if (values.some(value => value === null)) throw new Error('原件读取已经取消。');
      setResource({ key, files: values as OriginalFile[] }); setLoading(false);
    }).then(undefined, failure => {
      if (!current()) return;
      if (!(failure instanceof Error)) throw failure;
      controller.abort(); urls.forEach(url => URL.revokeObjectURL(url));
      const next = { key, value: failure };
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) denied.current = next;
      setResource(null); setLoading(false); setError(next);
    });
    return () => { controller.abort(); urls.forEach(url => URL.revokeObjectURL(url)); };
  }, [ready, key, attempt, access.hasCurrentAccess]);
  async function retry() {
    if (retrying.current || !authorized) return;
    retrying.current = true;
    try {
      if (denied.current?.key === key) await access.refresh();
      if (!mounted.current || currentKey.current !== key || !access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion)) return;
      denied.current = null; setError(null); setAttempt(value => value + 1);
    } finally { retrying.current = false; }
  }
  const files = resource?.key === key ? resource.files : [], currentError = error?.key === key ? error.value : null;
  const images = files.filter(file => file.mediaType.startsWith('image/'));
  const root = ready ? trigger.current?.getRootNode() : null;
  const container = root instanceof ShadowRoot ? root.querySelector('.device') : null;
  if (ready && !container) throw new Error('原件弹层缺少当前页面容器。');
  return <><button ref={trigger} className="button secondary" disabled={!authorized} onClick={() => setOpen(true)}>查看原件</button>
    {ready && container && createPortal(<UI2Sheet title="原件与页序" onClose={close}>
      {loading && <p role="status">正在读取获准原件</p>}
      {currentError && <section className="status-panel error" role="alert"><p>{currentError.message}</p><button className="text-action" disabled={!authorized} onClick={() => void retry()}>重新读取原件</button></section>}
      {!!images.length && <SourceGallery ui2 photos={images} />}
      {files.filter(file => file.mediaType === 'application/pdf').map(file => <iframe key={file.id} title={`PDF ${file.label}`} sandbox="" src={file.url} />)}
      {files.filter(file => file.mediaType === 'text/plain').map(file => <pre key={file.id} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{file.text}</pre>)}
      {pages.map(page => <p key={page.page_id}>文书页序 {page.page_order}；原件版本 {page.object_version}；状态 {page.status}。</p>)}
    </UI2Sheet>, container)}
  </>;
}
