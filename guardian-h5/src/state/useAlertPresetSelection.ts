import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { ApiError } from '../api/client';
import { createAlertPresetSubmission, readAlertPresetRequest, readAlertPresets,
  type AlertPresetReceipt, type AlertPresetRequest, type AlertPresetsView } from '../api/alertPresets';
import { useAccess, type AccessContextValue } from './access';

const markerKeys = ['alert_preset_request', 'alert_preset_actor', 'alert_preset_patient'];
export function alertPresetAccessKey(access: AccessContextValue) {
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  return JSON.stringify([access.principalId, access.patientId, access.scopeVersion, relation?.authorization_scope_fingerprint,
    relation?.relation_id, relation?.version, relation?.valid_until, relation?.authorization_scope_valid_until, relation?.actions]);
}
function authorized(access: AccessContextValue) {
  const relation = access.relations.find(item => item.patient_id === access.patientId);
  return Boolean(access.principalId && access.patientId && !access.loading && !access.error && relation?.status === 'active'
    && relation.actions.includes('select_alert_preset') && Date.parse(relation.valid_from) <= Date.now()
    && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now())
    && (!relation.authorization_scope_valid_until || Date.parse(relation.authorization_scope_valid_until) > Date.now())
    && access.hasCurrentAccess(access.principalId, access.patientId, access.scopeVersion));
}
const message = (failure: unknown) => failure instanceof Error ? failure.message : '预警方案暂未核验，请稍后重试。';

/** State is owned by a principal, patient and live authorization lease. */
export function useAlertPresetSelection() {
  const access = useAccess(), live = useRef(access); live.current = access;
  const key = alertPresetAccessKey(access), owner = useRef(key), mounted = useRef(true);
  const isCurrent = () => mounted.current && owner.current === key && alertPresetAccessKey(live.current) === key && authorized(live.current);
  const [params, setParams] = useSearchParams();
  const matches = params.get('alert_preset_actor') === access.principalId && params.get('alert_preset_patient') === access.patientId;
  const marker = useRef(matches ? params.get('alert_preset_request') : null);
  const [data, setData] = useState<AlertPresetsView | null>(null), [selection, setSelection] = useState('');
  const [loading, setLoading] = useState(true), [readError, setReadError] = useState<Error | null>(null);
  const [error, setError] = useState<string | null>(null), [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(Boolean(marker.current));
  const [original, setOriginal] = useState<AlertPresetRequest | null>(null), [saved, setSaved] = useState<AlertPresetReceipt | null>(null);
  const [reload, setReload] = useState(0);
  const active = useRef<AbortController | null>(null), read = useRef<AbortController | null>(null), busyRef = useRef(false);
  const frozen = useRef<ReturnType<typeof createAlertPresetSubmission> | null>(null);
  const canAccess = authorized(access);
  function remember(requestId: string | null) {
    marker.current = requestId;
    setParams(old => {
      const next = new URLSearchParams(old); markerKeys.forEach(name => next.delete(name));
      if (requestId) { next.set(markerKeys[0], requestId); next.set(markerKeys[1], access.principalId!); next.set(markerKeys[2], access.patientId!); }
      return next;
    }, { replace: true });
  }
  useEffect(() => {
    mounted.current = true; setBusy(false);
    return () => { mounted.current = false; active.current?.abort(); active.current = null; busyRef.current = false; read.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!canAccess) { active.current?.abort(); active.current = null; busyRef.current = false; setBusy(false); read.current?.abort(); return; }
    const controller = new AbortController(); read.current = controller;
    setLoading(true); setReadError(null); setData(null); setSelection('');
    void readAlertPresets(access.patientId!, { signal: controller.signal, isCurrent }).then(value => {
      if (!controller.signal.aborted && isCurrent()) { setData(value); setSelection(value.selected_schedule_config_id ?? ''); }
    }).catch(failure => {
      if (!controller.signal.aborted && isCurrent()) setReadError(failure instanceof Error ? failure : new Error(message(failure)));
    }).finally(() => { if (!controller.signal.aborted && isCurrent()) setLoading(false); });
    return () => controller.abort();
  }, [canAccess, key, reload]);
  useEffect(() => {
    if (!canAccess) return;
    if (params.has(markerKeys[0]) && !matches) remember(null);
    if (marker.current) void recover(false);
    // Reopening is read-only; it never automatically submits the original request.
  }, [canAccess, key]);

  function startAction() {
    if (!isCurrent() || busyRef.current) return null;
    busyRef.current = true; setBusy(true); setError(null);
    const controller = new AbortController(); active.current = controller;
    return controller;
  }
  function finishAction(controller: AbortController) {
    if (active.current === controller) { active.current = null; busyRef.current = false; if (isCurrent() && !controller.signal.aborted) setBusy(false); }
  }
  function accept(receipt: AlertPresetReceipt) {
    if (!isCurrent()) return;
    setSaved(receipt); setLocked(false); setOriginal(null); frozen.current = null; remember(null);
    setData(null); setSelection(''); setLoading(true); setReload(value => value + 1);
  }
  async function choose() {
    if (!isCurrent() || locked || frozen.current || marker.current || !data || !access.csrfToken
        || !data.options.some(option => option.schedule_config_id === selection)
        || selection === data.selected_schedule_config_id && data.selection_state !== 'requires_reselection') return;
    const controller = startAction(); if (!controller) return;
    setSaved(null); setLocked(true);
    const current = createAlertPresetSubmission(access.patientId!, { assignment_id: data.assignment_id,
      assignment_checksum: data.assignment_checksum, expected_version: data.selection_version, schedule_config_id: selection },
      { signal: controller.signal, isCurrent });
    frozen.current = current; remember(current.requestId);
    try { accept(await current.submit(access.csrfToken)); }
    catch (failure) {
      if (controller.signal.aborted || !isCurrent()) return;
      setError(message(failure));
      if (!current.uncertain) {
        // A definite preparation rejection never sent the selection.
        frozen.current = null; setLocked(false); remember(null); setData(null); setReload(value => value + 1);
      }
    } finally { finishAction(controller); }
  }
  async function recover(submit: boolean) {
    if (!marker.current || !isCurrent() || submit && !access.csrfToken) return;
    const controller = startAction(); if (!controller) return;
    try {
      const request = await readAlertPresetRequest(access.patientId!, marker.current, { signal: controller.signal, isCurrent });
      if (controller.signal.aborted || !isCurrent()) return;
      frozen.current?.assertRequest(request);
      setOriginal(request);
      if (request.receipt) { accept(request.receipt); return; }
      const current = createAlertPresetSubmission(access.patientId!, request.preset_input, { signal: controller.signal, isCurrent }, request);
      frozen.current = current;
      if (submit) accept(await current.submit(access.csrfToken ?? undefined));
    } catch (failure) {
      if (controller.signal.aborted || !isCurrent()) return;
      // Only retained in-memory content may replay a missing prepare response.
      if (submit && failure instanceof ApiError && failure.status === 404 && failure.code === 'RESOURCE_NOT_FOUND' && frozen.current && !frozen.current.hasIntent) {
        const replay = createAlertPresetSubmission(access.patientId!, frozen.current.payload, { signal: controller.signal, isCurrent }, undefined, frozen.current.requestId);
        frozen.current = replay;
        try { accept(await replay.submit(access.csrfToken ?? undefined)); }
        catch (retryFailure) { if (!controller.signal.aborted && isCurrent()) setError(message(retryFailure)); }
      } else setError(message(failure));
    } finally { finishAction(controller); }
  }
  async function releaseUncommitted() {
    if (original?.state !== 'failed' || original.may_retry || original.error_code !== 'PRESET_SELECTION_SUPERSEDED' || !marker.current || !isCurrent()) return;
    const controller = startAction(); if (!controller) return;
    try {
      const current = await readAlertPresetRequest(access.patientId!, marker.current, { signal: controller.signal, isCurrent });
      if (controller.signal.aborted || !isCurrent()) return;
      if (current.receipt) { accept(current.receipt); return; }
      if (current.state !== 'failed' || current.may_retry || current.error_code !== 'PRESET_SELECTION_SUPERSEDED') throw new Error('原请求结果尚未明确，不能开始新的选择。');
      frozen.current = null; setOriginal(null); setLocked(false); remember(null); setData(null); setSelection(''); setReload(value => value + 1);
    } catch (failure) { if (!controller.signal.aborted && isCurrent()) setError(message(failure)); }
    finally { finishAction(controller); }
  }
  return { access, canAccess, data: canAccess ? data : null, selection, loading, readError, error, busy, locked, original, saved,
    canSave: canAccess && Boolean(access.csrfToken && data?.options.length && !locked && !busy && selection
      && (selection !== data.selected_schedule_config_id || data.selection_state === 'requires_reselection')),
    select: (value: string) => { if (isCurrent() && !busyRef.current && !locked && data?.options.some(option => option.schedule_config_id === value)) { setSelection(value); setSaved(null); } },
    choose, recover, releaseUncommitted, refresh: () => { if (isCurrent() && !busyRef.current) { setData(null); setReload(value => value + 1); } } };
}
