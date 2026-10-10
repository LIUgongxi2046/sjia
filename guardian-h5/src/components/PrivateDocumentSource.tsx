import { useCallback, useEffect, useState } from 'react';
import { ApiError, fetchPrivateDocument } from '../api/client';
import { UI2Sheet } from './UI2Frame';
import { useAccess } from '../state/access';

export function PrivateDocumentSource({ file }: { file: { file_id: string; media_type: string; checksum: string; content_path: string | null } }) {
  const access = useAccess(), [open, setOpen] = useState(false), [content, setContent] = useState<{ url: string; text: string | null } | null>(null);
  const [error, setError] = useState<Error | null>(null), [attempt, setAttempt] = useState(0), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const close = useCallback(() => setOpen(false), []);
  const ready = open && visible && !!access.patientId && !!access.principalId && !access.loading && !access.error;
  useEffect(() => { const changed = () => { const active = document.visibilityState === 'visible'; setVisible(active); if (!active) setOpen(false); };
    document.addEventListener('visibilitychange', changed); return () => document.removeEventListener('visibilitychange', changed); }, []);
  useEffect(() => {
    const controller = new AbortController(); let url: string | null = null; setContent(null); setError(null);
    if (ready) void (async () => {
      if (!file.content_path?.startsWith('/api/') || file.content_path.includes('?') || file.content_path.includes('#') || file.content_path.includes('://')) throw new Error('原件读取地址无效。');
      const blob = await fetchPrivateDocument(file.content_path, file.media_type, controller.signal);
      const data = await blob.arrayBuffer();
      const checksum = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', data)), value => value.toString(16).padStart(2, '0')).join('');
      if (checksum !== file.checksum) throw new Error('原件字节与准确保存版本不一致。');
      const text = file.media_type === 'text/plain' ? new TextDecoder('utf-8', { fatal: true }).decode(data) : null;
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(blob); setContent({ url, text });
    })().then(undefined, failure => { if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) { setError(failure); if (failure instanceof ApiError && [401, 403].includes(failure.status)) void access.refresh(); } });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [ready, file.file_id, file.checksum, file.content_path, file.media_type, attempt]);
  return <><button className="button secondary" disabled={!access.patientId || !access.principalId || access.loading || !!access.error} onClick={() => setOpen(true)}>查看原件</button>
    {ready && <UI2Sheet title="完整原件" onClose={close}>{!content && !error && <p role="status">正在读取获准原件</p>}
      {error && <p role="alert">{error.message}</p>}{error && <button className="text-action" onClick={() => setAttempt(value => value + 1)}>重新读取原件</button>}
      {content?.text !== null && content && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{content.text}</pre>}
      {content && file.media_type === 'application/pdf' && <iframe title="完整 PDF 原件" sandbox="" src={content.url} style={{ width: '100%', minHeight: '60vh' }} />}
    </UI2Sheet>}</>;
}
