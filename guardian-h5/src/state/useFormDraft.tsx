import { useCallback, useEffect, useRef, useState } from 'react';
import { useBlocker, useSearchParams } from 'react-router';
import equal from 'fast-deep-equal';
import { createDraft, readDraft, updateDraft, type DraftView } from '../api/endpoints';
import { useAccess } from './access';

export function useFormDraft<P extends Record<string, unknown>>(formType: string, payload: P, restore: (value: P) => void, enabled = true, continuationPaths: readonly string[] = [], restoreEnabled = true) {
  const access = useAccess();
  const { patientId, csrfToken } = access;
  const accessReady = Boolean(access.principalId && patientId && !access.loading && !access.error
    && access.relations.some(relation => relation.patient_id === patientId && relation.status === 'active'
      && Date.parse(relation.valid_from) <= Date.now() && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now())
      && relation.actions.includes('manage_draft')));
  const liveAccess = useRef(accessReady);
  liveAccess.current = accessReady;
  const hasLiveAccess = () => liveAccess.current && access.hasCurrentAccess(access.principalId, patientId, access.scopeVersion);
  const [params, setParams] = useSearchParams();
  const draftId = params.get('draft');
  const [draft, setDraft] = useState<DraftView | null>(null);
  const [restoring, setRestoring] = useState(Boolean(draftId));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [saved, setSaved] = useState<P>(payload);
  const current = useRef({ payload, restore, draft, enabled });
  current.current = { payload, restore, draft, enabled: enabled && accessReady };
  const inFlight = useRef(false);
  const activeSave = useRef<Promise<boolean> | null>(null);
  const submitted = useRef(false);
  const submissionPaused = useRef(false);
  const mounted = useRef(true);
  const dirty = !equal(payload, saved) && !submitted.current;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const hasUnconfirmedSubmission = submissionPaused.current && !submitted.current;
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    const leavingDirtyForm = (dirty || submissionPaused.current) && !submitted.current && !!patientId
      && currentLocation.pathname !== nextLocation.pathname
      && !(continuationPaths.includes(currentLocation.pathname) && continuationPaths.includes(nextLocation.pathname));
    return leavingDirtyForm;
  });
  const blockerRef = useRef(blocker);
  blockerRef.current = blocker;

  useEffect(() => {
    mounted.current = true; liveAccess.current = accessReady;
    return () => { mounted.current = false; liveAccess.current = false; };
  }, []);

  useEffect(() => {
    if (!accessReady || !draftId || !patientId) { setRestoring(false); return; }
    if (!restoreEnabled) { setRestoring(true); return; }
    if (current.current.draft?.draft_id === draftId) { setRestoring(false); return; }
    const controller = new AbortController();
    setRestoring(true);
    setError(null);
    void readDraft(draftId, controller.signal, { isCurrent: hasLiveAccess }).then(value => {
      if (controller.signal.aborted || !hasLiveAccess()) return;
      if (value.patient_id !== patientId || value.form_type !== formType || value.status !== 'editing') throw new Error('草稿身份、患儿或表单不符合恢复条件');
      current.current.restore(value.payload as P);
      setDraft(value);
      setSaved(value.payload as P);
      setRestoring(false);
    }).then(undefined, failure => {
      if (!(failure instanceof Error)) throw failure;
      if (!controller.signal.aborted && hasLiveAccess()) { setError(failure.message); setRestoring(false); }
    });
    return () => controller.abort();
  }, [draftId, patientId, formType, restoreEnabled, restoreAttempt, accessReady]);

  const save = useCallback(async () => {
    if (!hasLiveAccess() || !patientId || restoring || !current.current.enabled || inFlight.current || submitted.current || submissionPaused.current) return false;
    const snapshot = current.current.payload;
    const previous = current.current.draft;
    inFlight.current = true;
    setSaving(true);
    setError(null);
    const request = previous
      ? updateDraft(previous.draft_id, previous.version, snapshot, csrfToken ?? undefined, { patientId, formType }, { isCurrent: hasLiveAccess })
      : createDraft(patientId, formType, snapshot, csrfToken ?? undefined, { isCurrent: hasLiveAccess });
    const completion = request.then(value => {
      if (!mounted.current || !hasLiveAccess()) return false;
      current.current.draft = value;
      setDraft(value);
      setSaved(snapshot);
      if (!draftId && blockerRef.current.state !== 'blocked') {
        const next = new URLSearchParams(params);
        next.set('draft', value.draft_id);
        setParams(next, { replace: true, preventScrollReset: true });
      }
      return true;
    }, failure => { if (mounted.current && hasLiveAccess()) setError(failure.message); return false; }).finally(() => {
      inFlight.current = false;
      activeSave.current = null;
      if (mounted.current) setSaving(false);
    });
    activeSave.current = completion;
    return completion;
  }, [patientId, csrfToken, formType, restoring, draftId, params, setParams]);

  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    // A native modal also works for forms rendered inside different shadow
    // roots. The hook owns it so every existing form gets the same choices.
    const dialog = document.createElement('dialog');
    dialog.setAttribute('aria-label', '尚未保存的内容');
    Object.assign(dialog.style, { maxWidth: 'min(30rem, calc(100vw - 2rem))', maxHeight: 'calc(100dvh - 2rem)',
      overflowY: 'auto', borderRadius: '1rem', padding: '1.25rem', color: 'var(--text-primary, #17233c)',
      background: 'var(--card-background, #fff)' });
    const description = document.createElement('p');
    description.textContent = submissionPaused.current
      ? '本次保存结果尚未确认。离开后请先查看已有记录，避免重复登记。'
      : '当前修改尚未全部保存。可以继续编辑、保存草稿后离开，或放弃未保存修改。';
    const failure = document.createElement('p');
    failure.setAttribute('role', 'alert');
    const keep = document.createElement('button');
    keep.type = 'button'; keep.textContent = '继续编辑';
    keep.onclick = () => blocker.reset();
    const saveAndLeave = document.createElement('button');
    saveAndLeave.type = 'button'; saveAndLeave.textContent = '保存草稿后离开';
    saveAndLeave.disabled = submissionPaused.current;
    saveAndLeave.onclick = async () => {
      saveAndLeave.disabled = true;
      failure.textContent = '';
      if (activeSave.current) await activeSave.current;
      const snapshot = current.current.payload;
      const success = await saveRef.current();
      if (!dialog.isConnected) return;
      if (success && equal(snapshot, current.current.payload)) blocker.proceed();
      else {
        failure.textContent = '草稿尚未保存成功，或输入在保存期间改变。请继续编辑或重试保存。';
        saveAndLeave.disabled = submissionPaused.current;
      }
    };
    const discard = document.createElement('button');
    discard.type = 'button'; discard.textContent = submissionPaused.current ? '仍然离开' : '放弃未保存修改并离开';
    discard.onclick = () => blocker.proceed();
    for (const button of [keep, saveAndLeave, discard]) Object.assign(button.style,
      { display: 'block', minHeight: '44px', width: '100%', marginTop: '0.75rem', whiteSpace: 'normal' });
    dialog.append(description, keep, saveAndLeave, discard, failure);
    let previouslyFocused = document.activeElement as HTMLElement | null;
    while (previouslyFocused?.shadowRoot?.activeElement) previouslyFocused = previouslyFocused.shadowRoot.activeElement as HTMLElement;
    dialog.addEventListener('cancel', event => { event.preventDefault(); blocker.reset(); });
    document.body.append(dialog);
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else { dialog.setAttribute('open', ''); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true'); }
    keep.focus();
    return () => { dialog.remove(); if (previouslyFocused?.isConnected) previouslyFocused.focus(); };
  }, [blocker.state]);

  useEffect(() => {
    if (!accessReady || !patientId || !enabled || restoring || error) return;
    // Keep one periodic clock while editing. Restarting on every keystroke
    // would postpone all saves indefinitely during continuous input.
    const timer = window.setInterval(() => { if (dirtyRef.current) void saveRef.current(); }, 10000);
    const blur = (event: FocusEvent) => {
      const target = event.composedPath()[0];
      // Submit buttons must be able to freeze the draft before a blur write.
      if (event.relatedTarget instanceof HTMLButtonElement && event.relatedTarget.type === 'submit') return;
      if (dirtyRef.current && (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement)) void saveRef.current();
    };
    document.addEventListener('focusout', blur);
    return () => { window.clearInterval(timer); document.removeEventListener('focusout', blur); };
  }, [patientId, formType, enabled, restoring, error, accessReady]);

  useEffect(() => {
    if ((!dirty && !hasUnconfirmedSubmission) || !patientId) return;
    const unload = (event: BeforeUnloadEvent) => {
      if (submitted.current) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', unload);
    return () => { window.removeEventListener('beforeunload', unload); };
  }, [dirty, patientId, hasUnconfirmedSubmission]);

  return { draft, restoring, saving, error, dirty, save,
    beginSubmission: () => {
      if (inFlight.current || restoring || submitted.current) throw new Error('请等待草稿保存或恢复完成后再提交');
      submissionPaused.current = true;
      const value = current.current.draft;
      return value ? { draft_id: value.draft_id, draft_version: value.version } : {};
    },
    cancelSubmission: () => { submissionPaused.current = false; },
    retryRestore: () => setRestoreAttempt(value => value + 1), initialize: (value: P) => setSaved(value), markSubmitted: () => { submitted.current = true; }, submission: draft ? { draft_id: draft.draft_id, draft_version: draft.version } : {} };
}

export function DraftActions({ controller, disabled = false }: { controller: Pick<ReturnType<typeof useFormDraft>, 'draft' | 'restoring' | 'saving' | 'error' | 'dirty' | 'save'>; disabled?: boolean }) {
  return <section aria-label="草稿状态">
    {controller.restoring && <p role="status">正在恢复草稿</p>}
    {controller.error && <p role="alert" className="form-error">{controller.error}</p>}
    {controller.draft && <p role="status">草稿保存时间：{new Date(controller.draft.saved_at).toLocaleString('zh-CN')} · 版本 {controller.draft.version}{controller.dirty ? ' · 当前修改尚未保存' : ''}</p>}
    <button type="button" className="text-action" disabled={disabled || controller.restoring || controller.saving} onClick={() => { void controller.save(); }}>{controller.saving ? '正在保存草稿' : '保存草稿'}</button>
  </section>;
}
