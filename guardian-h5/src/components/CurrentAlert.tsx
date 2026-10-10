import { Link } from 'react-router';
import { useResource } from '../api/useResource';
import type { AlertDetailView } from '../api/endpoints';

export function CurrentAlert({ alertId, description }: { alertId: string | null; description: string }) {
  const resource = useResource<AlertDetailView>(alertId ? `/api/alerts/${alertId}` : null);
  if (!alertId) return <p>{description}</p>;
  if (resource.loading) return <p role="status">正在读取获准提示内容</p>;
  if (resource.error) return <p role="alert">{resource.error}<button type="button" className="text-action" onClick={resource.reload}>重新读取提示</button></p>;
  if (!resource.data) return null;
  if (resource.data.validity !== 'valid') return <p>当前提示已失效，请查看最新资料。</p>;
  if (resource.data.content_state !== 'available' || !resource.data.content) return <p>提示内容尚未发布。</p>;
  return <div className="approved-alert-content"><p>{resource.data.content.approved_text}</p><Link className="text-action" to={`/alerts/${alertId}`}>查看提示依据与联系信息</Link></div>;
}
