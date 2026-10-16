import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { deliverMaterialNotice, markMaterialNoticeRead, readMaterialNotice, readMaterialNoticeMessage,
  retryMaterialNotice, searchMaterialNotices } from '../../api/materialNotices';
import type { MaterialNotice, MaterialNoticeHistory, MaterialNoticeMessage } from '../../api/materialNotices';
import { ContextLink } from '../../components/ContextLink';
import { UI2ReadState } from '../../components/UI2ReadState';

const statuses = { pending: '待处理', accepted: '已经受理', delivered: '已经送达', failed: '发送失败', uncertain: '结果尚未明确', cancelled: '已经取消' };

export function MaterialReminderNotices({ patientId, reminderId, enabled }: { patientId: string; reminderId: string | null; enabled: boolean }) {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get('notice');
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [cursors, setCursors] = useState<string[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [resource, setResource] = useState<{ data: MaterialNoticeHistory | null; selected: MaterialNotice | null; loading: boolean; error: Error | null }>(
    { data: null, selected: null, loading: true, error: null });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const mounted = useRef(true);
  const busyRef = useRef(false);
  const data = visible ? resource.data : null;
  const selected = visible ? resource.selected : null;
  useEffect(() => {
    mounted.current = true;
    const changed = () => { setVisible(document.visibilityState === 'visible'); setError(null); };
    document.addEventListener('visibilitychange', changed);
    return () => { mounted.current = false; document.removeEventListener('visibilitychange', changed); };
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setResource({ data: null, selected: null, loading: visible, error: null });
    if (visible) void Promise.all([searchMaterialNotices(patientId, reminderId ?? undefined, cursors.at(-1), controller.signal),
      selectedId ? readMaterialNotice(selectedId, patientId, controller.signal) : Promise.resolve(null)])
      .then(([history, current]) => {
        if (current && reminderId && current.reminder_id !== reminderId) throw new Error('所选资料提醒通知不属于当前提醒设置。');
        if (!controller.signal.aborted) setResource({ data: history, selected: current, loading: false, error: null });
      }).then(undefined, failure => { if (!(failure instanceof Error)) throw failure;
        if (!controller.signal.aborted) setResource({ data: null, selected: null, loading: false, error: failure }); });
    return () => controller.abort();
  }, [patientId, reminderId, selectedId, cursors, visible, attempt]);
  function choose(value: string | null) {
    setParams(current => { const next = new URLSearchParams(current); if (value) next.set('notice', value); else next.delete('notice'); return next; }, { replace: true });
  }
  function retry(notice: MaterialNotice) {
    if (busyRef.current || !enabled) return;
    busyRef.current = true; setBusy(true); setError(null);
    void retryMaterialNotice(notice).then(() => { if (mounted.current && document.visibilityState === 'visible') setAttempt(value => value + 1); }, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (mounted.current && document.visibilityState === 'visible') setError(failure);
    }).finally(() => { busyRef.current = false; if (mounted.current) setBusy(false); });
  }
  return <section className="card form-card"><h2>本人资料提醒通知</h2>
    <UI2ReadState loading={resource.loading} error={resource.error} onRetry={() => setAttempt(value => value + 1)} />
    {data && (data.items.length ? data.items.map(row => <div className="status-panel" key={row.notice_id}>
      <h3>{statuses[row.status]}</h3><p>计划时间：{row.scheduled_at}；提醒版本：{row.reminder_version}</p>
      <p>送达时间：{row.delivered_at ?? '尚未送达'}；阅读时间：{row.read_at ?? '尚未阅读'}</p>
      <details><summary>查看送达记录</summary>{row.receipts.length ? row.receipts.map(receipt => <p key={receipt.receipt_id}>
        {receipt.status} · {receipt.occurred_at} · {receipt.provider_event_id} · 证明 {receipt.evidence_sha256}</p>) : <p>暂无受理或送达记录。</p>}</details>
      <button type="button" className="text-action" disabled={busy || !row.current_setting || !['accepted', 'delivered'].includes(row.status)} onClick={() => choose(row.notice_id)}>查看提醒内容</button>
      {['pending', 'failed'].includes(row.status) && <button type="button" className="text-action" disabled={!enabled || busy || !row.current_setting} onClick={() => retry(row)}>再次核验原提醒通知</button>}
    </div>) : <p>当前没有保存的资料提醒通知。</p>)}
    {selected && selected.current_setting && ['accepted', 'delivered'].includes(selected.status) && <NoticeContent key={selected.notice_id}
      notice={selected} enabled={enabled} onClose={() => choose(null)} />}
    {selected && (!selected.current_setting || !['accepted', 'delivered'].includes(selected.status)) && <p role="status">当前通知正文不可用，保存的通知事实保持原状态。</p>}
    {visible && error && <p role="alert">{error.message}</p>}
    {data && <div className="two-buttons"><button type="button" className="button secondary" disabled={busy || !cursors.length} onClick={() => setCursors(value => value.slice(0, -1))}>上一页通知</button>
      <button type="button" className="button secondary" disabled={busy || !data.next_cursor} onClick={() => setCursors(value => [...value, data.next_cursor!])}>下一页通知</button></div>}
  </section>;
}

function NoticeContent({ notice, enabled, onClose }: { notice: MaterialNotice; enabled: boolean; onClose: () => void }) {
  const [message, setMessage] = useState<MaterialNoticeMessage | null>(null);
  const [current, setCurrent] = useState<MaterialNotice | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [attempt, setAttempt] = useState(0);
  const contentRef = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const controller = new AbortController(); setMessage(null); setCurrent(null); setError(null);
    void readMaterialNoticeMessage(notice, controller.signal).then(value => { if (!controller.signal.aborted) setMessage(value); }, failure => {
      if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setError(failure);
    });
    const hidden = () => { if (document.visibilityState !== 'visible') { controller.abort(); setMessage(null); setCurrent(null); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { controller.abort(); document.removeEventListener('visibilitychange', hidden); };
  }, [notice, attempt]);
  useEffect(() => {
    if (!message || !enabled || !contentRef.current || document.visibilityState !== 'visible') return;
    const controller = new AbortController(); let confirmed = false;
    const observer = new IntersectionObserver(entries => {
      if (confirmed || controller.signal.aborted || document.visibilityState !== 'visible' || !entries.some(row => row.isIntersecting)) return;
      confirmed = true; observer.disconnect();
      window.requestAnimationFrame(() => {
        if (controller.signal.aborted || document.visibilityState !== 'visible') return;
        void deliverMaterialNotice(message, controller.signal).then(value => markMaterialNoticeRead(value, controller.signal))
          .then(value => { if (!controller.signal.aborted) setCurrent(value); }, failure => {
            if (!(failure instanceof Error)) throw failure; if (!controller.signal.aborted) setError(failure);
          });
      });
    }, { threshold: 0.1 });
    observer.observe(contentRef.current);
    const hidden = () => { if (document.visibilityState !== 'visible') { controller.abort(); observer.disconnect(); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { controller.abort(); observer.disconnect(); document.removeEventListener('visibilitychange', hidden); };
  }, [message, enabled]);
  return <section className="status-panel" role="status"><h3>当前资料提醒内容</h3>
    {message && <><p ref={contentRef}>{message.approved_text}</p><ContextLink className="text-action" to={message.deep_link}>查看当前本人目标</ContextLink></>}
    {current && <p>实际送达：{current.delivered_at}；实际阅读：{current.read_at}</p>}
    {error && <p role="alert">{error.message}</p>}
    <button type="button" className="text-action" onClick={() => setAttempt(value => value + 1)}>重新读取原提醒内容</button>
    <button type="button" className="text-action" onClick={onClose}>关闭提醒内容</button>
  </section>;
}
