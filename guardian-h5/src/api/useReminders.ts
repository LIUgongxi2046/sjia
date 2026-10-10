import { useCallback, useEffect, useState } from 'react';
import type { ReminderList, ReminderTarget, ReminderTargetList, ReminderView } from '../../../frontend-design/v2-types';
import { readReminder, searchReminders, searchReminderTargets } from './materialReminders';

interface Resources { reminders: ReminderList; targets: ReminderTargetList; selected: ReminderView | null }
export function useReminders(owner: string | null, patient: string | null, type: ReminderTarget,
  reminder: string | null, listCursor?: string, targetCursor?: string) {
  const key = owner && patient ? JSON.stringify([owner, patient, type, reminder, listCursor, targetCursor]) : null;
  const [visible, setVisible] = useState(document.visibilityState === 'visible');
  const [attempt, setAttempt] = useState(0);
  const [resource, setResource] = useState<{ key: string | null; data: Resources | null; error: Error | null; loading: boolean }>(
    { key, data: null, error: null, loading: !!key });
  const reload = useCallback(() => { setResource({ key, data: null, error: null, loading: !!key }); setAttempt(value => value + 1); }, [key]);
  useEffect(() => {
    const changed = () => setVisible(document.visibilityState === 'visible');
    document.addEventListener('visibilitychange', changed);
    return () => document.removeEventListener('visibilitychange', changed);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setResource({ key, data: null, error: null, loading: !!key && visible });
    if (key && patient && visible) void Promise.all([searchReminders(patient, listCursor, controller.signal),
      searchReminderTargets(patient, { target_type: type, ...(targetCursor ? { cursor: targetCursor } : {}) }, controller.signal),
      reminder ? readReminder(reminder, patient, controller.signal) : Promise.resolve(null)]).then(([reminders, targets, selected]) => {
        if (!controller.signal.aborted) setResource({ key, data: { reminders, targets, selected }, error: null, loading: false });
      }, failure => {
        if (!(failure instanceof Error)) throw failure;
        if (!controller.signal.aborted) setResource({ key, data: null, error: failure, loading: false });
      });
    return () => controller.abort();
  }, [key, patient, type, reminder, listCursor, targetCursor, visible, attempt]);
  const current = visible && key === resource.key;
  return { data: current ? resource.data : null, error: current ? resource.error : null,
    loading: visible && (current ? resource.loading : !!key), reload };
}
