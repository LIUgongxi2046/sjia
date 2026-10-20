import { Fragment, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { ApiError } from '../api/client';
import { bootstrap, readSession } from '../api/endpoints';
import { ownsSessionBoundary, readSessionBoundary, recoverSessionBoundary, sessionBoundaryKey } from '../api/sessionBoundary';

export function SessionBoundary({ children }: { children: ReactNode }) {
  const [boundary, setBoundary] = useState(readSessionBoundary);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.storageArea === localStorage && (event.key === sessionBoundaryKey || event.key === null)) {
        const current = readSessionBoundary();
        if (!ownsSessionBoundary(current.record)) { setError(null); setBoundary(current); }
      }
    };
    window.addEventListener('storage', changed);
    return () => window.removeEventListener('storage', changed);
  }, []);
  useEffect(() => {
    if (boundary.record?.phase !== 'changing') return;
    let active = true;
    let timer: number | undefined;
    async function recover() {
      try {
        const checked = await recoverSessionBoundary(async () => {
          await bootstrap();
          try { await readSession(); }
          catch (failure) {
            if (!(failure instanceof ApiError && failure.status === 401 && failure.code === 'AUTH_REQUIRED')) throw failure;
          }
        });
        if (!active) return;
        if (checked) setBoundary(readSessionBoundary());
        else timer = window.setTimeout(() => void recover(), 1000);
      } catch (failure) {
        if (active) setError(failure instanceof Error ? failure.message : '暂时无法确认浏览器会话。');
      }
    }
    void recover();
    return () => { active = false; window.clearTimeout(timer); };
  }, [attempt, boundary]);
  if (boundary.record?.phase === 'changing') return <main className="state-card" role={error ? 'alert' : 'status'}>
    <h2>{error ? '会话暂未核验' : '正在同步登录状态'}</h2>
    <p>{error ?? '其他窗口正在处理账号操作，当前资料已清除。完成后重新核验访问权限。'}</p>
    {error && <button type="button" className="text-action" onClick={() => { setError(null); setAttempt(value => value + 1); }}>重新核验访问权限</button>}
  </main>;
  return <Fragment key={boundary.epoch ?? 'initial'}>{children}</Fragment>;
}
