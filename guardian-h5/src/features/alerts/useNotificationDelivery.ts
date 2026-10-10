import { useEffect, useState } from 'react';
import type { RefObject } from 'react';
import type { NotificationRecord } from '../../api/notifications';
import type { AlertDetailView } from '../../api/endpoints';


export function useNotificationDelivery(data: AlertDetailView | null, patientId: string | null,
  contentRef: RefObject<HTMLElement>, attempt: number) {
  const [record, setRecord] = useState<NotificationRecord | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setRecord(null); setError(null);
    if (!data || !patientId || document.visibilityState !== 'visible') return;
    const controller = new AbortController(); let observer: IntersectionObserver | null = null;
    const signal = controller.signal;
    async function receive() {
      const api = await import('../../api/notifications');
      const value = await api.readAlertNotification(data!.alert_id, patientId!, signal);
      if (signal.aborted) return;
      setRecord(value);
      if (!value || value.delivery_status !== 'accepted' || data!.validity !== 'valid'
          || data!.content_state !== 'available' || !data!.content) return;
      const message = await api.readNotificationMessage(value, signal);
      if (message.body.approved_text !== data!.content.approved_text || message.body.content_id !== data!.content.content_id
          || message.body.content_version !== data!.content.content_version) throw new Error('站内正文与当前批准内容不一致。');
      if (signal.aborted || !contentRef.current) return;
      let confirmed = false;
      observer = new IntersectionObserver(entries => {
        if (confirmed || signal.aborted || document.visibilityState !== 'visible' || !entries.some(row => row.isIntersecting)) return;
        confirmed = true; observer?.disconnect();
        window.requestAnimationFrame(() => {
          if (signal.aborted || document.visibilityState !== 'visible') return;
          void api.confirmNotificationDelivery(message, signal).then(result => { if (!signal.aborted) setRecord(result); }, failure => {
            if (!(failure instanceof Error)) throw failure;
            if (!signal.aborted) setError(failure.message);
          });
        });
      }, { threshold: 0.1 });
      observer.observe(contentRef.current);
    }
    void receive().then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!signal.aborted) setError(failure.message);
    });
    const hidden = () => { if (document.visibilityState !== 'visible') { controller.abort(); observer?.disconnect(); setRecord(null); setError(null); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { controller.abort(); observer?.disconnect(); document.removeEventListener('visibilitychange', hidden); };
  }, [data, patientId, contentRef, attempt]);
  return { record, error };
}
