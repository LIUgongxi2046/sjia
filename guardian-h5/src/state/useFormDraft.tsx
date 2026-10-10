import { useCallback, useEffect, useRef, useState } from 'react';
import { useBlocker, useSearchParams } from 'react-router';
import equal from 'fast-deep-equal';
import { createDraft, readDraft, updateDraft, type DraftView } from '../api/endpoints';
import { useAccess } from './access';

export function useFormDraft<P extends Record<string, unknown>>(formType: string, payload: P, restore: (value: P) => void, enabled = true, continuationPaths: readonly string[] = [], restoreEnabled = true) {
  const { patientId, csrfToken } = useAccess();
  const [params, setParams] = useSearchParams();
  const draftId = params.get('draft');
  const [draft, setDraft] = useState<DraftView | null>(null);
  const [restoring, setRestoring] = useState(Boolean(draftId));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [saved, setSaved] = useState<P>(payload);
  const current = useRef({ payload, restore, draft, enabled });
  current.current = { payload, restore, draft, enabled };
  const inFlight = useRef(false);
  const submitted = useRef(false);
  const mounted = useRef(true);
  const serialized = JSON.stringify(payload);
  const dirty = !equal(payload, saved) && !submitted.current;
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    const leavingDirtyForm = dirty && !submitted.current && !!patientId
      && currentLocation.pathname !== nextLocation.pathname
      && !(continuationPaths.includes(currentLocation.pathname) && continuationPaths.includes(nextLocation.pathname));
    if (!leavingDirtyForm) return false;
    return !window.confirm('当前内容尚未全部保存。选择取消可以继续编辑并保存草稿。确认离开此页面？');
  });

  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    blocker.reset();
  }, [blocker]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!draftId || !patientId) { setRestoring(false); return; }
    if (!restoreEnabled) { setRestoring(true); return; }
    if (current.current.draft?.draft_id === draftId) { setRestoring(false); return; }
    const controller = new AbortController();
    setRestoring(true);
    setError(null);
    void readDraft(draftId, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      if (value.patient_id !== patientId || value.form_type !== formType || value.status !== 'editing') throw new Error('草稿身份、患儿或表单不符合恢复条件');
      current.current.restore(value.payload as P);
      setDraft(value);
      setSaved(value.payload as P);
      setRestoring(false);
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted) { setError(failure.message); setRestoring(false); }
    });
    return () => controller.abort();
  }, [draftId, patientId, formType, restoreEnabled, restoreAttempt]);

  const save = useCallback(async () => {
    if (!patientId || restoring || !current.current.enabled || inFlight.current || submitted.current) return;
    const snapshot = current.current.payload;
    const previous = current.current.draft;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    const request = previous
      ? updateDraft(previous.draft_id, previous.version, snapshot, csrfToken ?? undefined)
      : createDraft(patientId, formType, snapshot, csrfToken ?? undefined);
    await request.then(value => {
      if (!mounted.current) return;
      current.current.draft = value;
      setDraft(value);
      setSaved(snapshot);
      if (!draftId) {
        const next = new URLSearchParams(params);
        next.set('draft', value.draft_id);
        setParams(next, { replace: true, preventScrollReset: true });
      }
    }, failure => { if (mounted.current) setError(failure.message); }).finally(() => {
      inFlight.current = false;
      if (mounted.current) setSaving(false);
    });
  }, [patientId, csrfToken, formType, restoring, draftId, params, setParams]);

  useEffect(() => {
    if (!patientId || !dirty || !enabled || restoring || error) return;
    const timer = window.setTimeout(() => { void save(); }, 10000);
    const blur = (event: FocusEvent) => {
      if (event.relatedTarget instanceof HTMLButtonElement || event.relatedTarget instanceof HTMLAnchorElement) return;
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLSelectElement) void save();
    };
    document.addEventListener('focusout', blur);
    return () => { window.clearTimeout(timer); document.removeEventListener('focusout', blur); };
  }, [patientId, dirty, enabled, restoring, error, serialized, save]);

  useEffect(() => {
    if (!dirty || !patientId) return;
    const unload = (event: BeforeUnloadEvent) => {
      if (submitted.current) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', unload);
    return () => { window.removeEventListener('beforeunload', unload); };
  }, [dirty, patientId]);

  return { draft, restoring, saving, error, dirty, save, retryRestore: () => setRestoreAttempt(value => value + 1), initialize: (value: P) => setSaved(value), markSubmitted: () => { submitted.current = true; }, submission: draft ? { draft_id: draft.draft_id, draft_version: draft.version } : {} };
}

export function DraftActions({ controller, disabled = false }: { controller: Pick<ReturnType<typeof useFormDraft>, 'draft' | 'restoring' | 'saving' | 'error' | 'dirty' | 'save'>; disabled?: boolean }) {
  return <section aria-label="草稿状态">
    {controller.restoring && <p role="status">正在恢复草稿</p>}
    {controller.error && <p role="alert" className="form-error">{controller.error}</p>}
    {controller.draft && <p role="status">草稿保存时间：{new Date(controller.draft.saved_at).toLocaleString('zh-CN')} · 版本 {controller.draft.version}{controller.dirty ? ' · 当前修改尚未保存' : ''}</p>}
    <button type="button" className="text-action" disabled={disabled || controller.restoring || controller.saving} onClick={() => { void controller.save(); }}>{controller.saving ? '正在保存草稿' : '保存草稿'}</button>
  </section>;
}
