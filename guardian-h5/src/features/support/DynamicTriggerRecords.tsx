import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import type { DynamicTriggerHistory } from '../../../../frontend-design/v2-types';
import { ApiError } from '../../api/client';
import { readDynamicTriggers } from '../../api/dynamic';

const events = { 'verification.changed': '专业核对变化', 'configuration.changed': '批准配置变化',
  'clinical.context_changed': '资料依据变化', 'dynamic.timer': '批准定时事件' };
const statuses: Record<string, string> = { accepted: '已经接收', queued: '等待执行', running: '正在处理', completed: '已创建评估任务',
  awaiting_confirmation: '正在执行独立静态评估', failed: '处理失败', superseded: '已有更新依据', cancelled: '已经终止' };

export function DynamicTriggerRecords({ patientId, principalId, onPermissionLost }: {
  patientId: string; principalId: string; onPermissionLost: () => void;
}) {
  const navigate = useNavigate();
  const controller = useRef<AbortController | null>(null);
  const [history, setHistory] = useState<DynamicTriggerHistory | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState<string | null>(null);
  useEffect(() => {
    function clear() { controller.current?.abort(); setHistory(null); setError(null); setLoading(false); }
    clear();
    function suspend() { if (document.visibilityState !== 'visible') clear(); }
    document.addEventListener('visibilitychange', suspend);
    return () => { controller.current?.abort(); document.removeEventListener('visibilitychange', suspend); };
  }, [patientId, principalId]);
  async function read(cursor?: string) {
    controller.current?.abort();
    const operation = new AbortController(); controller.current = operation;
    if (!cursor) setHistory(null); setError(null); setLoading(true);
    try {
      const value = await readDynamicTriggers(patientId, operation.signal, cursor);
      if (!operation.signal.aborted && document.visibilityState === 'visible') {
        if (cursor && history && (value.snapshot_version !== history.snapshot_version
            || value.items.some(item => history.items.some(prior => prior.task_id === item.task_id))))
          throw new Error('触发记录范围已经变化，请重新读取。');
        setHistory(cursor && history ? { ...value, items: [...history.items, ...value.items] } : value);
      }
    } catch (failure) {
      if (operation.signal.aborted) return;
      setHistory(null);
      setError(failure instanceof Error ? failure.message : '触发处理记录读取失败。');
      if (failure instanceof ApiError && [401, 403].includes(failure.status)) onPermissionLost();
    } finally { if (!operation.signal.aborted) setLoading(false); }
  }
  return <section className="card form-card">
    <button className="button secondary" disabled={loading} onClick={() => void read()}>读取自动触发处理记录</button>
    {loading && <p role="status">正在读取实际处理状态。</p>}{error && <p role="alert">{error}</p>}
    {history && !history.items.length && <p role="status">当前病例尚无已登记的自动触发任务。</p>}
    {history?.items.map(item => <section key={item.task_id}>
      <h3>{events[item.event_type]} · {statuses[item.status] ?? item.status}</h3>
      <p>事件发生时间：{item.occurred_at}</p><p>处理任务：{item.task_id}</p><p>批准配置：{item.schedule_id}</p>
      <p>实际来源事件：{item.source_id}</p>{item.error_code && <p>{item.error_code}</p>}
      {item.evaluation_task_id && <button className="text-action" onClick={() => navigate(`/support/assessment?${new URLSearchParams({
        patient_id: patientId, task_id: item.evaluation_task_id!, evaluation_kind: 'dynamic' })}`)}>读取实际评估任务</button>}
    </section>)}
    {history?.next_cursor && <button className="button secondary" disabled={loading} onClick={() => void read(history.next_cursor!)}>读取更多处理记录</button>}
  </section>;
}
