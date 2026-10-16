import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router';
import { createRecordSubmission, createCorrectionSubmission, validateCorrectionReceipt, validateRecordCreationReceipt } from '../api/recordSubmission';
import { fetchJson } from '../api/client';
import { validateRecordRequest, validateRecordRequests, type RecordRequestView } from '../api/legacyResponseSchema';
import { useAccess } from './access';

type DraftSubmission = {
  beginSubmission: () => { draft_id?: string; draft_version?: string };
  cancelSubmission: () => void;
  markSubmitted: () => void;
};

/** Shares one frozen intent across double clicks and uncertain-result retries. */
export type RecoverableRecordRequest = RecordRequestView;

export function useRecordSubmission(draft: DraftSubmission, kind: 'observation' | 'report' | 'therapy' | 'correction', recordId?: string | null) {
  const access = useAccess();
  const { principalId, patientId, csrfToken } = access;
  const relation = access.relations.find(item => item.patient_id === patientId);
  const authorized = Boolean(principalId && patientId && !access.loading && !access.error
    && relation?.status === 'active' && Date.parse(relation.valid_from) <= Date.now()
    && (!relation.valid_until || Date.parse(relation.valid_until) > Date.now())
    && relation.actions.includes(kind === 'correction' ? 'correct_record' : 'write_record'));
  const liveAccess = useRef(authorized);
  liveAccess.current = authorized;
  const hasLiveAccess = () => liveAccess.current && access.hasCurrentAccess(principalId, patientId, access.scopeVersion);
  const [params, setParams] = useSearchParams();
  const originalMarker = useRef({ intent: params.get('record_intent'), patient: params.get('record_intent_patient'), actor: params.get('record_intent_actor') });
  function remember(intentId: string) {
    setParams(old => { const next = new URLSearchParams(old); next.set('record_intent', intentId); next.set('record_intent_patient', patientId!); next.set('record_intent_actor', principalId!); return next; }, { replace: true });
  }
  function forget() {
    setParams(old => { const next = new URLSearchParams(old); next.delete('record_intent'); next.delete('record_intent_patient'); next.delete('record_intent_actor'); return next; }, { replace: true });
  }
  const operation = kind === 'correction' ? 'record_correct' : 'record_create';
  const target = kind === 'correction' ? `/api/records/${recordId}/corrections` : `/api/patients/${patientId}/records`;
  const identity = `${principalId}:${patientId}:${kind === 'correction' ? recordId : ''}`;
  function makeRequest(payload: Record<string, unknown>, originalRequestId?: string) {
    if (!patientId || kind === 'correction' && !recordId) throw new Error('当前患儿或更正记录尚未核对。');
    return kind === 'correction' ? createCorrectionSubmission(patientId, recordId!, payload, () => owner.current === identity && hasLiveAccess(), originalRequestId, remember)
      : createRecordSubmission(patientId, payload, () => owner.current === identity && hasLiveAccess(), originalRequestId, remember);
  }
  const owner = useRef(identity);
  const request = useRef<ReturnType<typeof createRecordSubmission> | null>(null);
  const [pending, setPending] = useState(false);
  const [recoveries, setRecoveries] = useState<RecoverableRecordRequest[]>([]);
  const [recoveryError, setRecoveryError] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [recheck, setRecheck] = useState(0);
  useEffect(() => {
    const marker = originalMarker.current;
    // A temporary read failure preserves the actor/patient-bound marker. An
    // authoritative identity or patient change must never reuse it.
    if ((!access.loading && !access.error || access.errorSource === 'logout') && marker.intent && (marker.actor !== principalId || marker.patient !== patientId)) {
      originalMarker.current = { intent: null, patient: null, actor: null };
      forget();
    }
  }, [access.loading, access.error, access.errorSource, patientId, principalId]);
  useEffect(() => {
    const controller = new AbortController();
    setRecoveries([]); setRecoveryError(null); setChecking(true);
    if (!authorized || !patientId || !principalId || kind === 'correction' && !recordId) { setChecking(false); return; }
    const marker = originalMarker.current;
    const scope = { patientId, operation: operation as 'record_create' | 'record_correct', target, ...(kind === 'correction' ? {} : { kind }) };
    const load = marker.intent && marker.patient === patientId && marker.actor === principalId
      ? fetchJson<unknown>(`/api/requests/${marker.intent}`, { signal: controller.signal, isCurrent: hasLiveAccess })
          .then(value => ({ patient_id: patientId, requests: [validateRecordRequest(value, { ...scope, intentId: marker.intent! })] }))
      : fetchJson<unknown>(
          `/api/patients/${patientId}/requests?operation_id=${operation}&pending=true&${kind === 'correction' ? `record_id=${recordId}` : `kind=${kind}`}`,
          { signal: controller.signal, isCurrent: hasLiveAccess }).then(value => validateRecordRequests(value, scope));
    void load.then(value => {
      if (controller.signal.aborted || owner.current !== identity || !hasLiveAccess()) return;
      setRecoveries(value.requests);
    }).catch(error => {
      if (!controller.signal.aborted && owner.current === identity && hasLiveAccess()) setRecoveryError(error instanceof Error ? error.message : '原提交核对失败。');
    }).finally(() => { if (!controller.signal.aborted && owner.current === identity && hasLiveAccess()) setChecking(false); });
    return () => controller.abort();
  }, [patientId, principalId, identity, kind, operation, target, recordId, recheck, authorized]);

  if (owner.current !== identity) {
    request.current?.cancel();
    request.current = null;
    owner.current = identity;
    if (pending) setPending(false);
  }
  useEffect(() => {
    liveAccess.current = authorized;
    return () => { liveAccess.current = false; request.current?.cancel(); };
  }, []);

  async function submit(payload: Record<string, unknown>) {
    if (!hasLiveAccess() || !patientId || !principalId || owner.current !== identity) throw new Error('登录身份或当前患儿已改变，请重新核对记录。');
    if (!request.current) {
      if (checking || recoveryError || recoveries.length) throw new Error('请先核对之前尚未完成的原提交。');
      const metadata = draft.beginSubmission();
      try {
        request.current = makeRequest({ ...payload, ...metadata });
        setPending(true);
      } catch (error) {
        draft.cancelSubmission();
        throw error;
      }
    }
    const current = request.current;
    try {
      const receipt = await current.submit(csrfToken ?? undefined);
      if (owner.current !== identity || !hasLiveAccess()) throw new Error('登录身份或当前患儿已改变，请重新核对记录。');
      draft.markSubmitted(); forget();
      return receipt;
    } catch (error) {
      if (owner.current !== identity || !hasLiveAccess()) throw error;
      if (!current.uncertain) {
        request.current = null; forget();
        draft.cancelSubmission();
        setPending(false);
        throw error;
      }
      throw new Error('尚未确认是否保存成功。已暂停编辑和草稿保存，请重试本次保存以核对结果。');
    }
  }

  async function recover(saved: RecoverableRecordRequest) {
    if (!hasLiveAccess() || !patientId || owner.current !== identity || !recoveries.some(item => item.intent_id === saved.intent_id)) return;
    if (saved.status === 'committed' && saved.receipt) {
      const receipt = kind === 'correction' ? await validateCorrectionReceipt(saved.receipt, patientId, recordId!, saved.record_input) : validateRecordCreationReceipt(saved.receipt, patientId);
      if (owner.current !== identity || !hasLiveAccess()) throw new Error('登录身份或当前患儿已改变。');
      draft.markSubmitted(); forget(); return receipt;
    }
    if (request.current) {
      if (request.current.requestId !== saved.request_id) throw new Error('请先完成正在核对的原提交。');
      return submit(saved.record_input);
    }
    draft.beginSubmission();
    request.current = makeRequest(saved.record_input, saved.request_id);
    setPending(true);
    const result = await submit(saved.record_input);
    setRecoveries(current => current.filter(item => item.intent_id !== saved.intent_id));
    return result;
  }

  return { pending: pending || checking || Boolean(recoveryError) || recoveries.length > 0,
    retry: () => { if (!request.current) return Promise.reject(new Error('当前没有待核对的原提交。')); return submit({}); },
    recovering: pending, recoveries, recoveryError, checking, recover, submit,
    recheck: () => setRecheck(current => current + 1) };
}
