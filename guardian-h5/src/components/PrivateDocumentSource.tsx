import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError, fetchPrivateDocument } from '../api/client';
import { UI2Sheet } from './UI2Frame';
import { useAccess } from '../state/access';

export function PrivateDocumentSource({ file }: { file: { file_id: string; media_type: string; checksum: string; content_path: string | null } }) {
  const access = useAccess(), [open, setOpen] = useState(false), [content, setContent] = useState<{ key: string; url: string; text: string | null } | null>(null);
  const [error, setError] = useState<{ key: string; value: Error } | null>(null), [attempt, setAttempt] = useState(0), [visible, setVisible] = useState(document.visibilityState === 'visible');
  const denied = useRef<{ key: string; value: Error } | null>(null), retrying = useRef(false), mounted = useRef(true);
  const close = useCallback(() => setOpen(false), []);
  const key = JSON.stringify([access.principalId, access.patientId, access.scopeVersion, file]);
  const currentKey = useRef(key); currentKey.current = key;
  const authorized = !!access.patientId && !!access.principalId && !access.loading && !access.error
    && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
  const ready = open && visible && authorized;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { const changed = () => { const active = document.visibilityState === 'visible'; setVisible(active); if (!active) setOpen(false); };
    document.addEventListener('visibilitychange', changed); return () => document.removeEventListener('visibilitychange', changed); }, []);
  useEffect(() => {
    const controller = new AbortController(); let url: string | null = null;
    const current = () => !controller.signal.aborted && currentKey.current === key && document.visibilityState === 'visible'
      && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion);
    const denial = denied.current?.key === key ? denied.current : null;
    setContent(null); setError(denial);
    if (ready && !denial && current()) void (async () => {
      if (!file.content_path?.startsWith('/api/') || file.content_path.includes('?') || file.content_path.includes('#') || file.content_path.includes('://')) throw new Error('原件读取地址无效。');
      const blob = await fetchPrivateDocument(file.content_path, file.media_type, controller.signal);
      if (!current()) return;
      const data = await blob.arrayBuffer();
      if (!current()) return;
      const digest = await crypto.subtle.digest('SHA-256', data);
      if (!current()) return;
      const checksum = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
      if (checksum !== file.checksum) throw new Error('原件字节与准确保存版本不一致。');
      const text = file.media_type === 'text/plain' ? new TextDecoder('utf-8', { fatal: true }).decode(data) : null;
      url = URL.createObjectURL(blob); setContent({ key, url, text });
    })().then(undefined, failure => {
      if (!current()) return;
      if (!(failure instanceof Error)) throw failure;
      controller.abort(); if (url) URL.revokeObjectURL(url);
      const next = { key, value: failure };
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) denied.current = next;
      setContent(null); setError(next);
    });
    return () => { controller.abort(); if (url) URL.revokeObjectURL(url); };
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
  const currentContent = content?.key === key ? content : null, currentError = error?.key === key ? error.value : null;
  return <><button className="button secondary" disabled={!authorized} onClick={() => setOpen(true)}>查看原件</button>
    {ready && <UI2Sheet title="完整原件" onClose={close}>{!currentContent && !currentError && <p role="status">正在读取获准原件</p>}
      {currentError && <p role="alert">{currentError.message}</p>}{currentError && <button className="text-action" disabled={!authorized} onClick={() => void retry()}>重新读取原件</button>}
      {currentContent?.text !== null && currentContent && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{currentContent.text}</pre>}
      {currentContent && file.media_type === 'application/pdf' && <iframe title="完整 PDF 原件" sandbox="" src={currentContent.url} style={{ width: '100%', minHeight: '60vh' }} />}
    </UI2Sheet>}</>;
}
