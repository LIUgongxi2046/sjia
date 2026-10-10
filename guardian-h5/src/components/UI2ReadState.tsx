import { ArrowRight, CircleAlert, LoaderCircle, LockKeyhole, WifiOff } from 'lucide-react';
import { useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useAccess } from '../state/access';

export function UI2ReadState({ loading = false, error, onRetry, requirePatient = true }: {
  loading?: boolean; error?: Error | null; onRetry?: () => void; requirePatient?: boolean;
}) {
  const access = useAccess();
  const navigate = useNavigate();
  const permission = !access.principalId || (requirePatient && !access.patientId)
    || (error instanceof ApiError && (error.status === 401 || error.status === 403));
  if (access.error) return <section className={`status-panel ${navigator.onLine ? 'error' : 'offline'}`} role="alert" style={{ overflowWrap: 'anywhere' }}>
    <div className="status-top">{navigator.onLine ? <CircleAlert size={22} /> : <WifiOff size={22} />}<span>页面状态</span></div><h2>{navigator.onLine ? '身份服务读取失败' : '网络连接已中断'}</h2><p>{access.error}</p>
    <button className="text-action" onClick={() => void access.refresh()}>重新核验访问权限<ArrowRight size={16} /></button>
  </section>;
  if (permission) return <section className="status-panel permission" role="status">
    <div className="status-top"><LockKeyhole size={22} /><span>页面状态</span></div><h2>当前访问关系需要核验</h2><p>重新取得有效权限后才能查看资料。</p>
    <button className="text-action" onClick={() => navigate(access.principalId ? '/associations' : '/login')}>核验访问权限<ArrowRight size={16} /></button>
  </section>;
  if (error) return <section className={`status-panel ${navigator.onLine ? 'error' : 'offline'}`} role="alert" style={{ overflowWrap: 'anywhere' }}>
    <div className="status-top">{navigator.onLine ? <CircleAlert size={22} /> : <WifiOff size={22} />}<span>页面状态</span></div>
    <h2>{navigator.onLine ? '暂时无法取得资料' : '网络连接已中断'}</h2><p>{navigator.onLine ? '重新读取前，现有资料状态保持未知。' : '尚未保存的输入保留在当前页面。'}</p><p>{error.message}</p>
    {onRetry && <button className="text-action" onClick={onRetry}>重新读取资料<ArrowRight size={16} /></button>}
  </section>;
  if (loading) return <section className="status-panel loading" role="status">
    <div className="status-top"><LoaderCircle size={22} /><span>页面状态</span></div><h2>正在读取当前资料</h2><p>读取完成后显示当前获准内容。</p>
  </section>;
  return null;
}
