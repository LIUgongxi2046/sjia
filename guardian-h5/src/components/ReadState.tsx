import { Link } from 'react-router';
import { useAccess } from '../state/access';

export function ReadState({ loading, error, empty, onRetry, requirePatient = true }: { loading?: boolean; error?: string | null; empty?: string; onRetry?: () => void; requirePatient?: boolean }) {
  const access = useAccess();
  if (access.error) return <section className="state-card" role="alert"><h2>身份服务读取失败</h2><p>{access.error}</p><button type="button" className="text-action" onClick={() => void access.refresh()}>重新加载</button></section>;
  if (!access.principalId) return <section className="state-card"><h2>登录后查看资料</h2><p>当前尚未读取患儿资料。</p><Link className="primary-link" to="/login">登录账号</Link></section>;
  if (requirePatient && !access.patientId) return <section className="state-card"><h2>尚未选择有效关联的患儿</h2><Link className="primary-link" to="/associations">管理患儿关联</Link></section>;
  if (loading) return <p role="status">正在读取资料</p>;
  if (error) return <section className="state-card" role="alert"><h2>资料读取失败</h2><p>{error}</p>{onRetry && <button type="button" className="text-action" onClick={onRetry}>重新加载</button>}</section>;
  if (empty) return <section className="state-card"><h2>{empty}</h2></section>;
  return null;
}
