import { useEffect, useRef, useState } from 'react';
import { CircleAlert, Clock3 } from 'lucide-react';
import { ApiError } from '../../api/client';
import { readPurposeTask } from '../../api/purpose';
import type { TaskView } from '../../../../frontend-design/v2-types';

const labels: Record<TaskView['status'], string> = {
  accepted: '任务已接收', blocked: '处理条件未满足', queued: '正在排队', running: '正在处理',
  awaiting_confirmation: '等待本人确认', completed: '处理已完成', failed: '处理未完成',
  uncertain: '处理结果待确认', awaiting_resolution: '等待核查处理', cancel_requested: '取消结果待确认',
  cancelled: '任务已取消', superseded: '任务依据已变化',
};

export function PurposeTaskProgress({ taskId, active, onCompleted, onPermission, taskType = 'purpose_copy' }: {
  taskId: string; active: boolean; onCompleted: (copyId: string) => void; onPermission: () => void;
  taskType?: 'purpose_copy' | 'disposition';
}) {
  const [task, setTask] = useState<TaskView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [readSequence, setReadSequence] = useState(0);
  const latestVersion = useRef<bigint | null>(null);
  useEffect(() => { latestVersion.current = null; }, [taskId]);
  useEffect(() => {
    setTask(null); setError(null);
    if (!active || document.visibilityState !== 'visible') return;
    const controller = new AbortController();
    const started = performance.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function query() {
      try {
        const current = await readPurposeTask(taskId, controller.signal);
        if (controller.signal.aborted) return;
        if (current.task_id !== taskId || current.task_type !== taskType) throw new Error('任务身份与当前资料处理不一致。');
        const version = BigInt(current.status_version);
        if (latestVersion.current !== null && version < latestVersion.current) throw new Error('任务状态版本已经变化，请重新读取。');
        latestVersion.current = version; setTask(current);
        if (current.status === 'completed') {
          if (current.result_type !== taskType || current.result_ref === null) throw new Error('处理结果缺少准确资料引用。');
          onCompleted(current.result_ref); return;
        }
        if (['failed', 'cancelled', 'superseded', 'awaiting_resolution', 'uncertain', 'blocked'].includes(current.status)) return;
        if (current.poll_after_ms === null || !Number.isSafeInteger(current.poll_after_ms) || current.poll_after_ms <= 0) throw new Error('任务查询间隔没有提供。');
        if (performance.now() - started + current.poll_after_ms > 300_000) {
          setError('页面自动查询已经结束，可以重新读取当前任务。'); return;
        }
        timer = setTimeout(() => void query(), current.poll_after_ms);
      } catch (failure) {
        if (controller.signal.aborted) return;
        setTask(null); setError(failure instanceof Error ? failure.message : '任务读取失败。');
        if (failure instanceof ApiError && [401, 403].includes(failure.status)) onPermission();
      }
    }
    void query();
    return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
  }, [taskId, active, readSequence, onCompleted, onPermission, taskType]);
  if (!active) return null;
  const state = error || task?.status === 'failed' ? 'error' : task?.status === 'superseded' ? 'stale' : 'pending';
  return <section className={`status-panel ${state}`} role="status"><div className="status-top">
    {error ? <CircleAlert size={22} /> : <Clock3 size={22} />}<span>资料处理任务</span></div>
    <h2>{error ? '暂时无法取得任务状态' : task ? labels[task.status] : '正在读取当前任务'}</h2>
    <p>{error ?? (task?.status === 'completed' ? taskType === 'purpose_copy' ? '副本处理已经保存，人工复核需要独立完成。' : '处置结果已经保存，请读取逐项证明。' : '原件与处理进度由服务端保存。')}</p>
    {(error || task?.status === 'failed' || task?.status === 'uncertain' || task?.status === 'awaiting_resolution') &&
      <button className="text-action" onClick={() => setReadSequence(value => value + 1)}>重新读取任务</button>}
  </section>;
}
