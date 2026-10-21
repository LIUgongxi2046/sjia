import { useRef, useState } from 'react';
import { ChevronRight as ArrowRight, CircleAlert, LoaderCircle, LockKeyhole, WifiOff } from './ThreeDIcon';
import { Navigate, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useAccess } from '../state/access';

export function UI2ReadState({ loading = false, error, onRetry, requirePatient = true }: {
  loading?: boolean; error?: Error | null; onRetry?: () => void | Promise<void>; requirePatient?: boolean;
}) {
  const access = useAccess();
  const navigate = useNavigate();
  const pending = useRef(false);
  const [retrying, setRetrying] = useState(false), [retryError, setRetryError] = useState<Error | null>(null);
  async function retry() {
    if (!onRetry || pending.current || access.loading) return;
    pending.current = true; setRetrying(true); setRetryError(null);
    try { await onRetry(); }
    catch (failure) { setRetryError(failure instanceof Error ? failure : new Error('重新读取未完成，请稍后重试。')); }
    finally { pending.current = false; setRetrying(false); }
  }
  async function recheckAccess() {
    if (pending.current || access.loading) return;
    pending.current = true;
    try {
      await access.refresh();
      if (access.hasCurrentAccess()) await onRetry?.();
    } finally { pending.current = false; }
  }
  const sessionRequired = !access.principalId || (error instanceof ApiError && error.status === 401);
  if (access.error) return <section className={`status-panel rich-read-state ${navigator.onLine ? 'error' : 'offline'}`} role="alert" style={{ overflowWrap: 'anywhere' }}>
    <div className="status-top">{navigator.onLine ? <CircleAlert size={22} /> : <WifiOff size={22} />}<span>页面状态</span></div><h2>{navigator.onLine ? (access.errorSource === 'relations' ? '患儿关联暂未读取' : '身份服务读取失败') : '网络连接已中断'}</h2>{access.errorSource === 'relations' && <p>账号已登录，无需重复注册。关联核验成功前不能访问患儿资料。</p>}<p>{access.error}</p>
    <button className="text-action" onClick={() => void access.refresh()}>重新核验访问权限<ArrowRight size={16} /></button>
  </section>;
  if (sessionRequired) return <Navigate to="/login" replace />;
  if (requirePatient && !access.patientId) return <section className="status-panel permission rich-read-state" role="status">
    <div className="status-top"><LockKeyhole size={22} /><span>患儿关联</span></div><h2>尚无可访问的患儿记录</h2>
    <p>当前账号已登录。请查看患儿关联及申请状态，取得有效关联后即可访问资料。</p>
    <button className="text-action" onClick={() => navigate('/associations')}>管理患儿关联<ArrowRight size={16} /></button>
    <button className="text-action" disabled={access.loading} onClick={() => void access.refresh()}>重新读取患儿关联<ArrowRight size={16} /></button>
  </section>;
  if (error instanceof ApiError && error.code === 'CONTENT_DISPOSED') return <section className="status-panel rich-read-state" role="status">
    <div className="status-top"><LockKeyhole size={22} /><span>资料状态</span></div><h2>这份内容已处置</h2>
    <p>原内容已按批准范围移除，无法重新读取。处置状态不代表临床结果。</p>
  </section>;
  if (error instanceof ApiError && error.code === 'CONTENT_ACCESS_REVOKED') return <section className="status-panel rich-read-state" role="status">
    <h2>这份资料的访问已撤销</h2><p>内容保留情况以处置记录为准。</p>
  </section>;
  if (error instanceof ApiError && error.status === 403) return <section className="status-panel permission rich-read-state" role="status">
    <div className="status-top"><LockKeyhole size={22} /><span>访问权限</span></div><h2>当前账号没有访问权限</h2>
    <p>请检查当前患儿的关联和授权范围。</p><p>{error.message}</p>
    <details><summary>查看错误详情</summary><p>HTTP {error.status} · {error.code}</p></details>
    <button className="text-action" disabled={access.loading} onClick={() => void recheckAccess()}>重新检查访问权限<ArrowRight size={16} /></button>
    <button className="text-action" onClick={() => navigate('/associations')}>管理患儿关联<ArrowRight size={16} /></button>
  </section>;
  if (error) return <section className={`status-panel rich-read-state ${navigator.onLine ? 'error' : 'offline'}`} role="alert" style={{ overflowWrap: 'anywhere' }}>
    <div className="status-top">{navigator.onLine ? <CircleAlert size={22} /> : <WifiOff size={22} />}<span>页面状态</span></div>
    <h2>{navigator.onLine ? '暂时无法取得资料' : '网络连接已中断'}</h2><p>{navigator.onLine ? '重新读取前，现有资料状态保持未知。' : '尚未保存的输入保留在当前页面。'}</p>{error instanceof ApiError && error.code === 'RUNTIME_CONFIGURATION_PENDING' ? <><p>服务配置尚未完成，暂时无法读取。</p><details><summary>查看技术详情</summary><p>错误代码：{error.code}</p>{error.message !== error.code && <p>{error.message}</p>}</details></> : <p>{error.message}</p>}
    {retryError && <p>{retryError.message}</p>}{onRetry && <button className="text-action" disabled={retrying || loading} onClick={() => void retry()}>重新读取资料<ArrowRight size={16} /></button>}
  </section>;
  if (loading) return <section className="status-panel loading rich-read-state" role="status">
    <div className="status-top"><LoaderCircle size={22} /><span>页面状态</span></div><h2>正在读取当前资料</h2><p>读取完成后显示当前获准内容。</p>
  </section>;
  return null;
}
